"""Detector YOLOX-Tiny COCO sobre ONNX en CPU para P6-02.

El modelo se ejecuta con OpenCV DNN (backend OpenCV, target CPU), sin agregar
dependencias. Este modulo no implementa metricas, eventos ni alertas: solo
convierte un cuadro BGR en detecciones trazables o en un error tecnico.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import http.client
import logging
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Literal, Protocol
import urllib.request

import cv2
import numpy as np

logger = logging.getLogger(__name__)

MODELO_NOMBRE = "YOLOX-Tiny"
MODELO_DATASET = "COCO"
VERSION_PESOS = "0.1.1rc0"
URL_PESOS = (
    "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx"
)
SHA256_PESOS = "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"
TAMANO_PESOS = 20_219_662
RUTA_PESOS = Path("models") / "yolox_tiny.onnx"

TAMANO_ENTRADA = (416, 416)
VALOR_RELLENO = 114
STRIDES = (8, 16, 32)
UMBRAL_SCORE_PREDETERMINADO = 0.10
UMBRAL_NMS_PREDETERMINADO = 0.45

_CHUNK_DESCARGA = 1024 * 1024
_TIMEOUT_DESCARGA = 60.0

# Orden de salida de YOLOX: el indice de cada clase es su posicion en la lista.
CLASES_COCO = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)

CANALES_SALIDA = 5 + len(CLASES_COCO)
CELDAS_SALIDA = sum(
    (TAMANO_ENTRADA[0] // stride) * (TAMANO_ENTRADA[1] // stride) for stride in STRIDES
)


class ErrorDetector(RuntimeError):
    """Fallo tecnico del detector; no representa una escena sin objetos."""


class ErrorPesos(RuntimeError):
    """Fallo al verificar o descargar el archivo de pesos."""


def _fallo(etapa: str, origen: str, detalle: object) -> ErrorDetector:
    logger.error("detector_error stage=%s source=%s detail=%s", etapa, origen, detalle)
    return ErrorDetector(f"{etapa} failed for {origen}: {detalle}")


def _fallo_pesos(ruta: Path, detalle: object) -> ErrorPesos:
    logger.error("weights_error path=%s detail=%s", ruta, detalle)
    return ErrorPesos(f"{ruta}: {detalle}")


def _milisegundos(inicio: float) -> float:
    return round((time.perf_counter() - inicio) * 1000.0, 3)


def metadatos_modelo(*, incluir_clases: bool = False) -> dict[str, Any]:
    """Identidad y contrato del modelo, sin depender del archivo local."""
    clases: dict[str, Any] = {"count": len(CLASES_COCO), "source": MODELO_DATASET}
    if incluir_clases:
        clases["names"] = list(CLASES_COCO)
    return {
        "name": MODELO_NOMBRE,
        "dataset": MODELO_DATASET,
        "version": VERSION_PESOS,
        "format": "ONNX",
        "weights_url": URL_PESOS,
        "sha256": SHA256_PESOS,
        "size_bytes": TAMANO_PESOS,
        "input": {
            "shape": [1, 3, TAMANO_ENTRADA[0], TAMANO_ENTRADA[1]],
            "dtype": "float32",
            "color": "BGR",
            "values": "0-255",
            "pad_value": VALOR_RELLENO,
        },
        "output": {
            "shape": [1, CELDAS_SALIDA, CANALES_SALIDA],
            "layout": "cx,cy,w,h,objectness,class_probabilities",
        },
        "runtime": {
            "backend": "opencv-dnn",
            "target": "cpu",
            "opencv_version": cv2.__version__,
        },
        "classes": clases,
    }


def metadatos_filtros(score_threshold: float, nms_threshold: float) -> dict[str, Any]:
    return {
        "score": "objectness*class_probability",
        "score_threshold": score_threshold,
        "nms": "class-agnostic",
        "nms_threshold": nms_threshold,
        "preprocess": "letterbox-bottom-right",
        "pad_value": VALOR_RELLENO,
        "input_size": list(TAMANO_ENTRADA),
        "normalization": "none",
    }


class SesionInferencia(Protocol):
    """Interfaz minima para poder probar el detector sin pesos reales."""

    def inferir(self, tensor: np.ndarray) -> np.ndarray:
        ...


@dataclass(frozen=True)
class Deteccion:
    class_id: int
    class_name: str
    confidence: float
    # Pixeles de la imagen original; origen arriba a la izquierda.
    bbox_xyxy: tuple[float, float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "bbox_xyxy": list(self.bbox_xyxy),
        }


@dataclass(frozen=True)
class ResultadoCuadro:
    source_id: str
    frame_id: str
    frame_number: int | None
    received_at: str | None
    status: Literal["ok", "error"]
    image_width: int
    image_height: int
    detections: tuple[Deteccion, ...]
    class_counts: dict[str, int]
    error: str | None
    timings_ms: dict[str, float | None]
    model: dict[str, Any]
    filters: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Contrato serializable; una escena vacia sigue siendo `status="ok"`."""
        return {
            "source_id": self.source_id,
            "frame_id": self.frame_id,
            "frame_number": self.frame_number,
            "received_at": self.received_at,
            "status": self.status,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "model": self.model,
            "filters": self.filters,
            "detections": [deteccion.to_dict() for deteccion in self.detections],
            "class_counts": self.class_counts,
            "timings_ms": self.timings_ms,
            "error": self.error,
        }


def _validar_imagen(imagen: np.ndarray, origen: str) -> tuple[int, int]:
    if not isinstance(imagen, np.ndarray):
        raise _fallo("preprocess", origen, f"expected numpy array, got {type(imagen).__name__}")
    if imagen.ndim != 3 or imagen.shape[2] != 3 or imagen.size == 0:
        raise _fallo("preprocess", origen, f"expected HxWx3 BGR image, got {imagen.shape}")
    if imagen.dtype != np.uint8:
        raise _fallo("preprocess", origen, f"expected uint8 BGR image, got {imagen.dtype}")
    return imagen.shape[0], imagen.shape[1]


def preprocesar(
    imagen: np.ndarray,
    *,
    origen: str = "array",
    tamano: tuple[int, int] = TAMANO_ENTRADA,
    valor_relleno: int = VALOR_RELLENO,
) -> tuple[np.ndarray, float]:
    """Redimensiona sin deformar y rellena abajo/derecha con 114.

    Conserva BGR y valores 0-255: estos pesos no usan media ni desviacion.
    Devuelve un tensor CHW float32 y el factor para volver a pixeles originales.
    """
    alto, ancho = _validar_imagen(imagen, origen)
    alto_entrada, ancho_entrada = tamano
    if alto_entrada <= 0 or ancho_entrada <= 0:
        raise _fallo("preprocess", origen, f"invalid input size {tamano}")

    ratio = min(alto_entrada / alto, ancho_entrada / ancho)
    nuevo_alto = max(1, int(alto * ratio))
    nuevo_ancho = max(1, int(ancho * ratio))

    try:
        redimensionada = cv2.resize(
            imagen, (nuevo_ancho, nuevo_alto), interpolation=cv2.INTER_LINEAR
        )
        lienzo = np.full((alto_entrada, ancho_entrada, 3), valor_relleno, dtype=np.uint8)
        lienzo[:nuevo_alto, :nuevo_ancho] = redimensionada
        tensor = np.ascontiguousarray(lienzo.transpose(2, 0, 1), dtype=np.float32)
    except cv2.error as error:
        raise _fallo("preprocess", origen, error) from error
    return tensor, ratio


@lru_cache(maxsize=4)
def _rejilla(alto_entrada: int, ancho_entrada: int) -> tuple[np.ndarray, np.ndarray]:
    rejillas = []
    strides = []
    for stride in STRIDES:
        filas = alto_entrada // stride
        columnas = ancho_entrada // stride
        xv, yv = np.meshgrid(np.arange(columnas), np.arange(filas))
        rejillas.append(np.stack((xv, yv), axis=2).reshape(-1, 2))
        strides.append(np.full((filas * columnas, 1), stride))
    return np.concatenate(rejillas), np.concatenate(strides)


def _decodificar_celdas(raw: np.ndarray, origen: str) -> np.ndarray:
    """Convierte la salida por celda en cajas cx, cy, w, h de la entrada 416x416."""
    rejilla, stride = _rejilla(TAMANO_ENTRADA[0], TAMANO_ENTRADA[1])
    if raw.shape != (CELDAS_SALIDA, CANALES_SALIDA):
        raise _fallo(
            "postprocess",
            origen,
            f"decoded shape {raw.shape}, expected {(CELDAS_SALIDA, CANALES_SALIDA)}",
        )

    decodificada = raw.astype(np.float64)
    decodificada[:, :2] = (decodificada[:, :2] + rejilla) * stride
    with np.errstate(over="ignore"):
        decodificada[:, 2:4] = np.exp(decodificada[:, 2:4]) * stride
    return decodificada


def _nms(cajas: np.ndarray, scores: np.ndarray, umbral: float) -> list[int]:
    """NMS sin distinguir clase, como el demo oficial de YOLOX."""
    if scores.size == 0:
        return []

    x1, y1, x2, y2 = cajas.T
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    orden = np.argsort(-scores, kind="stable")

    conservadas: list[int] = []
    while orden.size > 0:
        mejor, resto = int(orden[0]), orden[1:]
        conservadas.append(mejor)
        if resto.size == 0:
            break
        ancho = np.maximum(
            0.0, np.minimum(x2[mejor], x2[resto]) - np.maximum(x1[mejor], x1[resto]) + 1
        )
        alto = np.maximum(
            0.0, np.minimum(y2[mejor], y2[resto]) - np.maximum(y1[mejor], y1[resto]) + 1
        )
        interseccion = ancho * alto
        iou = interseccion / (areas[mejor] + areas[resto] - interseccion)
        orden = resto[iou <= umbral]
    return conservadas


class SesionOpenCVDNN:
    """Adaptador de una red OpenCV DNN ya cargada, restringida a CPU."""

    def __init__(self, red: Any, nombre_salida: str) -> None:
        self._red = red
        self._nombre_salida = nombre_salida

    def inferir(self, tensor: np.ndarray) -> np.ndarray:
        self._red.setInput(tensor[np.newaxis])
        return np.asarray(self._red.forward(self._nombre_salida))


def _validar_umbral(nombre: str, valor: float) -> float:
    if (
        isinstance(valor, bool)
        or not isinstance(valor, (int, float))
        or not math.isfinite(valor)
        or not 0.0 <= valor <= 1.0
    ):
        raise ValueError(f"{nombre} must be a finite number between 0.0 and 1.0")
    return float(valor)


class DetectorYolox:
    """YOLOX-Tiny sobre una sesion inyectable, solo con inferencia CPU.

    `detectar` nunca lanza un fallo tecnico como si fuera una escena vacia:
    devuelve `status="error"` con `detections=[]`. Una escena sin objetos
    devuelve `status="ok"` y `detections=[]`.
    """

    def __init__(
        self,
        sesion: SesionInferencia,
        *,
        score_threshold: float = UMBRAL_SCORE_PREDETERMINADO,
        nms_threshold: float = UMBRAL_NMS_PREDETERMINADO,
    ) -> None:
        self._sesion = sesion
        self.score_threshold = _validar_umbral("score_threshold", score_threshold)
        self.nms_threshold = _validar_umbral("nms_threshold", nms_threshold)
        self.model = metadatos_modelo()
        self.filters = metadatos_filtros(self.score_threshold, self.nms_threshold)

    def detectar(
        self,
        imagen: np.ndarray,
        *,
        source_id: str,
        frame_id: str,
        frame_number: int | None = None,
        received_at: str | None = None,
    ) -> ResultadoCuadro:
        if not source_id or not frame_id:
            raise ValueError("source_id y frame_id no pueden estar vacios")
        if frame_number is not None and (
            isinstance(frame_number, bool)
            or not isinstance(frame_number, int)
            or frame_number < 1
        ):
            raise ValueError("frame_number debe ser un entero >= 1")

        alto_imagen = ancho_imagen = 0
        if isinstance(imagen, np.ndarray) and imagen.ndim >= 2 and imagen.size:
            alto_imagen, ancho_imagen = int(imagen.shape[0]), int(imagen.shape[1])

        origen = f"{source_id}/{frame_id}"
        inicio_total = time.perf_counter()
        tiempos: dict[str, float | None] = {
            "preprocess": None,
            "inference": None,
            "postprocess": None,
            "total": None,
        }
        estado: Literal["ok", "error"] = "ok"
        detecciones: tuple[Deteccion, ...] = ()
        error: str | None = None

        try:
            inicio = time.perf_counter()
            tensor, ratio = preprocesar(imagen, origen=origen)
            tiempos["preprocess"] = _milisegundos(inicio)

            inicio = time.perf_counter()
            raw = self._inferir(tensor, origen)
            tiempos["inference"] = _milisegundos(inicio)

            inicio = time.perf_counter()
            detecciones = self._posprocesar(
                raw, ratio, (alto_imagen, ancho_imagen), origen
            )
            tiempos["postprocess"] = _milisegundos(inicio)
        except ErrorDetector as fallo:
            estado = "error"
            error = str(fallo)
        except Exception as fallo:  # una falla inesperada no puede parecer una escena vacia
            logger.exception("detector_error stage=unexpected source=%s", origen)
            estado = "error"
            error = f"unexpected failure for {origen}: {fallo}"

        tiempos["total"] = _milisegundos(inicio_total)
        conteo = dict(sorted(Counter(deteccion.class_name for deteccion in detecciones).items()))
        return ResultadoCuadro(
            source_id=source_id,
            frame_id=frame_id,
            frame_number=frame_number,
            received_at=received_at,
            status=estado,
            image_width=ancho_imagen,
            image_height=alto_imagen,
            detections=detecciones,
            class_counts=conteo,
            error=error,
            timings_ms=tiempos,
            model=self.model,
            filters=self.filters,
        )

    def _inferir(self, tensor: np.ndarray, origen: str) -> np.ndarray:
        try:
            raw = self._sesion.inferir(tensor)
        except Exception as error:
            raise _fallo("inference", origen, error) from error
        if not isinstance(raw, np.ndarray):
            raise _fallo("inference", origen, f"expected numpy output, got {type(raw).__name__}")
        return raw

    def _posprocesar(
        self,
        raw: np.ndarray,
        ratio: float,
        tamano_imagen: tuple[int, int],
        origen: str,
    ) -> tuple[Deteccion, ...]:
        esperado = (1, CELDAS_SALIDA, CANALES_SALIDA)
        if raw.shape != esperado:
            raise _fallo(
                "postprocess", origen, f"output shape {raw.shape}, expected {esperado}"
            )

        decodificada = _decodificar_celdas(raw[0], origen)
        if not np.isfinite(decodificada).all():
            raise _fallo("postprocess", origen, "non-finite model output")

        probabilidades = decodificada[:, 4:]
        if probabilidades.min() < 0.0 or probabilidades.max() > 1.0:
            raise _fallo("postprocess", origen, "scores outside [0, 1]")

        indices_clase = probabilidades[:, 1:].argmax(axis=1)
        scores = probabilidades[:, 0] * probabilidades[
            np.arange(CELDAS_SALIDA), indices_clase + 1
        ]
        candidatos = scores > self.score_threshold
        if not candidatos.any():
            return ()

        if not math.isfinite(ratio) or ratio <= 0.0:
            raise _fallo("postprocess", origen, f"invalid scale ratio {ratio}")

        centros = decodificada[candidatos, :2]
        tamanos = decodificada[candidatos, 2:4]
        cajas = np.concatenate((centros - tamanos / 2, centros + tamanos / 2), axis=1) / ratio
        scores_candidatos = scores[candidatos]
        clases_candidatas = indices_clase[candidatos]

        alto, ancho = tamano_imagen
        if alto <= 0 or ancho <= 0:
            raise _fallo("postprocess", origen, f"invalid image size {(ancho, alto)}")
        cajas[:, [0, 2]] = cajas[:, [0, 2]].clip(0, ancho)
        cajas[:, [1, 3]] = cajas[:, [1, 3]].clip(0, alto)

        detecciones = []
        for indice in _nms(cajas, scores_candidatos, self.nms_threshold):
            x1, y1, x2, y2 = (float(valor) for valor in cajas[indice])
            # Una caja que cae solo en el relleno queda sin area al recortarla.
            if x2 > x1 and y2 > y1:
                clase_id = int(clases_candidatas[indice])
                detecciones.append(
                    Deteccion(
                        class_id=clase_id,
                        class_name=CLASES_COCO[clase_id],
                        confidence=float(scores_candidatos[indice]),
                        bbox_xyxy=(x1, y1, x2, y2),
                    )
                )
        return tuple(detecciones)


def sha256_archivo(ruta: Path | str, *, bloque_bytes: int = _CHUNK_DESCARGA) -> str:
    digest = hashlib.sha256()
    with Path(ruta).open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(bloque_bytes), b""):
            digest.update(bloque)
    return digest.hexdigest()


def verificar_pesos(
    ruta: Path | str,
    *,
    sha256_esperado: str = SHA256_PESOS,
    tamano_esperado: int = TAMANO_PESOS,
) -> str:
    """Comprueba tamano y SHA256 antes de entregar el archivo a OpenCV."""
    archivo = Path(ruta)
    if not archivo.is_file():
        raise _fallo_pesos(archivo, "weights file not found")
    tamano = archivo.stat().st_size
    if tamano_esperado and tamano != tamano_esperado:
        raise _fallo_pesos(
            archivo, f"unexpected size {tamano} bytes, expected {tamano_esperado}"
        )
    digest = sha256_archivo(archivo)
    if digest.lower() != sha256_esperado.lower():
        raise _fallo_pesos(
            archivo, f"sha256 mismatch: got {digest}, expected {sha256_esperado}"
        )
    return digest


def _descargar_pesos(destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(prefix=f"{destino.name}.", dir=str(destino.parent))
    os.close(descriptor)
    temporal_path = Path(temporal)
    descargados = 0
    try:
        solicitud = urllib.request.Request(
            URL_PESOS, headers={"User-Agent": f"P6-02/{MODELO_NOMBRE}-{VERSION_PESOS}"}
        )
        logger.info("weights_download_start url=%s destination=%s", URL_PESOS, destino)
        with urllib.request.urlopen(solicitud, timeout=_TIMEOUT_DESCARGA) as respuesta:
            with temporal_path.open("wb") as archivo:
                while True:
                    bloque = respuesta.read(_CHUNK_DESCARGA)
                    if not bloque:
                        break
                    archivo.write(bloque)
                    descargados += len(bloque)
                    if descargados // _CHUNK_DESCARGA % 5 == 0:
                        logger.info("weights_download_progress bytes=%d", descargados)
        try:
            verificar_pesos(temporal_path)
        except ErrorPesos as error:
            raise _fallo_pesos(destino, f"download verification failed: {error}") from error
        os.replace(temporal_path, destino)
        logger.info("weights_download_done bytes=%d path=%s", descargados, destino)
    except (OSError, http.client.HTTPException) as error:
        raise _fallo_pesos(destino, f"download failed: {error}") from error
    finally:
        temporal_path.unlink(missing_ok=True)


def asegurar_pesos(
    destino: Path | str = RUTA_PESOS,
    *,
    descargar: bool = True,
) -> Path:
    """Devuelve pesos locales verificados; reemplaza solo tras validar la descarga.

    Los pesos nunca se versionan: `models/` esta ignorado por Git.
    """
    ruta = Path(destino)
    if ruta.is_file():
        try:
            verificar_pesos(ruta)
            return ruta
        except ErrorPesos as error:
            logger.warning("weights_invalid path=%s detail=%s", ruta, error)
            if not descargar:
                raise _fallo_pesos(ruta, "invalid weights and download disabled") from error

    if not descargar:
        raise _fallo_pesos(ruta, "weights missing and download disabled")
    _descargar_pesos(ruta)
    return ruta


def cargar_detector(
    pesos: Path | str = RUTA_PESOS,
    *,
    score_threshold: float = UMBRAL_SCORE_PREDETERMINADO,
    nms_threshold: float = UMBRAL_NMS_PREDETERMINADO,
    descargar: bool = True,
) -> DetectorYolox:
    """Verifica (o descarga) los pesos y prepara OpenCV DNN en CPU."""
    _validar_umbral("score_threshold", score_threshold)
    _validar_umbral("nms_threshold", nms_threshold)
    ruta = asegurar_pesos(pesos, descargar=descargar)

    try:
        red = cv2.dnn.readNetFromONNX(str(ruta))
        red.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        red.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        nombres_salida = [str(nombre) for nombre in red.getUnconnectedOutLayersNames()]
    except Exception as error:
        raise _fallo("load", str(ruta), error) from error

    if len(nombres_salida) != 1:
        raise _fallo(
            "load", str(ruta), f"expected exactly one output layer, got {nombres_salida}"
        )
    logger.info("detector_ready model=%s path=%s output=%s", MODELO_NOMBRE, ruta, nombres_salida[0])
    return DetectorYolox(
        SesionOpenCVDNN(red, nombres_salida[0]),
        score_threshold=score_threshold,
        nms_threshold=nms_threshold,
    )
