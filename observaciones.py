"""Pipeline progresivo fuente -> detector -> observaciones primarias.

Versiona un JSONL con cabecera, una observacion por cuadro y un resumen final.
No guarda pixeles, no calcula indicadores G1-G7 y no crea eventos, capturas ni
alertas: esa instrumentacion pertenece a etapas posteriores.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import platform
import re
import secrets
import time
from typing import Any, Callable, Iterator, Mapping, Protocol, TextIO
from urllib.parse import urlsplit, urlunsplit

import cv2
import numpy as np

from detector_yolox import Deteccion, ResultadoCuadro
from fuente import (
    EXTENSIONES_IMAGEN,
    ObservacionFuente,
    leer_fuente,
    validar_configuracion_fuente,
)

logger = logging.getLogger(__name__)

ESQUEMA = "p6-03/1"
VERSION_PIPELINE = "p6-03"
RUTA_OBSERVACIONES = Path("datos_locales") / "observaciones"

ESTADOS_OBSERVACION = frozenset(
    {"processed", "discarded", "read_error", "detector_error", "end_of_stream"}
)
ESTADOS_FUENTE = frozenset({"sampled", "discarded", "read_error", "end_of_stream"})
RESULTADOS_TECNICOS = frozenset({"ok", "no_detections", "error"})
TIPOS_REGISTRO = frozenset({"cabecera", "observacion", "resumen"})

_PATRON_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EXTENSIONES_VIDEO = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".mjpg", ".mjpeg", ".m4v"}


class ErrorObservaciones(RuntimeError):
    """El JSONL no cumple el contrato de observaciones de P6-03."""


class DetectorObservaciones(Protocol):
    """Interfaz minima que P6-03 necesita del detector de P6-02."""

    model: Mapping[str, Any]
    filters: Mapping[str, Any]

    def detectar(
        self,
        imagen: np.ndarray,
        *,
        source_id: str,
        frame_id: str,
        frame_number: int | None = None,
        received_at: str | None = None,
    ) -> ResultadoCuadro:
        ...


def _instante() -> str:
    return datetime.now(timezone.utc).isoformat()


def _milisegundos(inicio: float) -> float:
    return round((time.perf_counter() - inicio) * 1000.0, 3)


def nuevo_run_id() -> str:
    """Identificador unico y estable para un lote de observaciones."""
    marca = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"run-{marca}-{secrets.token_hex(3)}"


def _validar_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or not _PATRON_RUN_ID.match(run_id):
        raise ValueError(
            "run_id debe iniciar con letra o digito y usar solo letras, digitos, punto, guion o guion bajo"
        )


def ruta_salida_predeterminada(run_id: str) -> Path:
    """Ruta ignorada por Git para un lote nuevo; no contiene pixeles."""
    _validar_run_id(run_id)
    return RUTA_OBSERVACIONES / f"{run_id}.jsonl"


def _tipo_fuente(source: str) -> str:
    if re.match(r"^[a-z][a-z0-9+.-]*://", source, re.IGNORECASE):
        return "stream"
    ruta = Path(source)
    if ruta.is_dir():
        return "directory"
    sufijo = ruta.suffix.lower()
    if sufijo in EXTENSIONES_IMAGEN:
        return "image"
    if sufijo in _EXTENSIONES_VIDEO:
        return "video"
    return "file"


def _fuente_publica(source: str) -> str:
    """Quita credenciales y parametros de una URL antes de persistirla."""
    if re.match(r"^[a-z][a-z0-9+.-]*://", source, re.IGNORECASE):
        partes = urlsplit(source)
        sin_credenciales = partes.netloc.rsplit("@", 1)[-1]
        return urlunsplit((partes.scheme, sin_credenciales, partes.path, "", ""))
    return source


def _version_entorno() -> dict[str, str]:
    return {
        "schema": ESQUEMA,
        "pipeline": VERSION_PIPELINE,
        "python": platform.python_version(),
        "opencv": cv2.__version__,
    }


def _modelo_de(detector: object) -> dict[str, Any]:
    modelo = getattr(detector, "model", None)
    if not isinstance(modelo, Mapping):
        raise ValueError("el detector debe exponer model como diccionario")
    modelo = dict(modelo)
    if not isinstance(modelo.get("name"), str) or not modelo["name"].strip():
        raise ValueError("el detector debe declarar model['name']")
    if not isinstance(modelo.get("version"), str) or not modelo["version"].strip():
        raise ValueError("el detector debe declarar model['version']")
    try:
        json.dumps(modelo, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"model no es serializable: {error}") from error
    return modelo


def _filtros_de(detector: object) -> dict[str, Any]:
    filtros = getattr(detector, "filters", None)
    if filtros is None:
        return {}
    if not isinstance(filtros, Mapping):
        raise ValueError("el detector debe exponer filters como diccionario")
    filtros = dict(filtros)
    try:
        json.dumps(filtros, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"filters no es serializable: {error}") from error
    return filtros


def _modelo_compacto(modelo: Mapping[str, Any]) -> dict[str, Any]:
    claves = ("name", "dataset", "version", "sha256", "size_bytes")
    return {clave: modelo[clave] for clave in claves if clave in modelo}


def _validar_max_seconds(max_seconds: float | None) -> None:
    if max_seconds is not None and (
        isinstance(max_seconds, bool)
        or not isinstance(max_seconds, (int, float))
        or not math.isfinite(max_seconds)
        or max_seconds < 0
    ):
        raise ValueError("max_seconds debe ser un numero finito >= 0")


def _validar_ruta_salida(ruta: Path, *, overwrite: bool) -> None:
    if ruta.exists():
        if ruta.is_dir():
            raise IsADirectoryError(f"la salida debe ser un archivo, no un directorio: {ruta}")
        if not overwrite:
            raise FileExistsError(f"la salida ya existe; usa overwrite=True: {ruta}")
    ruta.parent.mkdir(parents=True, exist_ok=True)


def _dimensiones(width: int | None, height: int | None) -> dict[str, int] | None:
    if (
        isinstance(width, int) and not isinstance(width, bool)
        and isinstance(height, int) and not isinstance(height, bool)
        and width > 0 and height > 0
    ):
        return {"width": width, "height": height}
    return None


def _contadores_nuevos() -> dict[str, Any]:
    return {
        "frames_received": 0,
        "frames_sampled": 0,
        "frames_discarded": 0,
        "discarded_by_reason": Counter(),
        "read_errors": 0,
        "read_errors_by_reason": Counter(),
        "frames_processed": 0,
        "frames_with_detections": 0,
        "frames_without_detections": 0,
        "detector_calls": 0,
        "detector_errors": 0,
        "detector_errors_by_reason": Counter(),
        "detections_total": 0,
        "detections_by_class": Counter(),
        "end_of_stream_events": 0,
    }


def _serializar_contadores(contadores: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "frames_received": contadores["frames_received"],
        "frames_sampled": contadores["frames_sampled"],
        "frames_discarded": contadores["frames_discarded"],
        "discarded_by_reason": dict(sorted(contadores["discarded_by_reason"].items())),
        "read_errors": contadores["read_errors"],
        "read_errors_by_reason": dict(sorted(contadores["read_errors_by_reason"].items())),
        "frames_processed": contadores["frames_processed"],
        "frames_with_detections": contadores["frames_with_detections"],
        "frames_without_detections": contadores["frames_without_detections"],
        "detector_calls": contadores["detector_calls"],
        "detector_errors": contadores["detector_errors"],
        "detector_errors_by_reason": dict(
            sorted(contadores["detector_errors_by_reason"].items())
        ),
        "detections_total": contadores["detections_total"],
        "detections_by_class": dict(sorted(contadores["detections_by_class"].items())),
        "end_of_stream_events": contadores["end_of_stream_events"],
    }


def _resultado_tecnico(contadores: Mapping[str, Any]) -> str:
    if contadores["read_errors"] or contadores["detector_errors"]:
        return "with_errors"
    if contadores["frames_received"] == 0 and contadores["frames_processed"] == 0:
        return "no_data"
    return "ok"


def _acumular(registro: Mapping[str, Any], contadores: dict[str, Any]) -> None:
    """Recalcula contadores solo desde campos persistidos del registro."""
    estado_fuente = registro.get("source_status")
    estado = registro.get("status")
    if estado_fuente in {"sampled", "discarded"}:
        contadores["frames_received"] += 1
    if estado_fuente == "sampled":
        contadores["frames_sampled"] += 1
    if estado == "discarded":
        contadores["frames_discarded"] += 1
        contadores["discarded_by_reason"][registro.get("reason") or "unknown"] += 1
    elif estado == "read_error":
        contadores["read_errors"] += 1
        contadores["read_errors_by_reason"][registro.get("reason") or "unknown"] += 1
    elif estado == "processed":
        contadores["frames_processed"] += 1
        clases = registro.get("class_counts") or {}
        if clases:
            contadores["frames_with_detections"] += 1
            for nombre, cantidad in clases.items():
                contadores["detections_by_class"][nombre] += cantidad
                contadores["detections_total"] += cantidad
        else:
            contadores["frames_without_detections"] += 1
    elif estado == "detector_error":
        contadores["detector_errors"] += 1
        contadores["detector_errors_by_reason"][registro.get("reason") or "unknown"] += 1
    elif estado == "end_of_stream":
        contadores["end_of_stream_events"] += 1
    if estado in {"processed", "detector_error"}:
        contadores["detector_calls"] += 1


def _tiempos(resultado: ResultadoCuadro) -> dict[str, float] | None:
    tiempos = resultado.timings_ms
    if not isinstance(tiempos, Mapping):
        return None
    return {str(clave): valor for clave, valor in tiempos.items()}


def _desajuste_identidad(
    observacion: ObservacionFuente, resultado: ResultadoCuadro
) -> str | None:
    if resultado.source_id != observacion.source_id:
        return f"source_id {resultado.source_id!r} != {observacion.source_id!r}"
    if resultado.frame_id != observacion.frame_id:
        return f"frame_id {resultado.frame_id!r} != {observacion.frame_id!r}"
    if observacion.frame_number is not None and resultado.frame_number != observacion.frame_number:
        return f"frame_number {resultado.frame_number!r} != {observacion.frame_number!r}"
    if observacion.received_at is not None and resultado.received_at != observacion.received_at:
        return "received_at no coincide con el de la fuente"
    if (
        observacion.width is not None and observacion.width > 0
        and resultado.image_width != observacion.width
    ):
        return f"image_width {resultado.image_width!r} != {observacion.width!r}"
    if (
        observacion.height is not None and observacion.height > 0
        and resultado.image_height != observacion.height
    ):
        return f"image_height {resultado.image_height!r} != {observacion.height!r}"
    return None


def _serializar_detecciones(
    detecciones: object,
) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(detecciones, (tuple, list)):
        return [], f"detections debe ser tupla o lista, no {type(detecciones).__name__}"
    serializadas: list[dict[str, Any]] = []
    for deteccion in detecciones:
        if not isinstance(deteccion, Deteccion):
            return [], f"deteccion inesperada de tipo {type(deteccion).__name__}"
        if (
            isinstance(deteccion.class_id, bool)
            or not isinstance(deteccion.class_id, int)
            or deteccion.class_id < 0
        ):
            return [], f"class_id invalido: {deteccion.class_id!r}"
        if not isinstance(deteccion.class_name, str) or not deteccion.class_name:
            return [], "class_name invalido"
        if (
            isinstance(deteccion.confidence, bool)
            or not isinstance(deteccion.confidence, (int, float))
            or not math.isfinite(deteccion.confidence)
            or not 0.0 <= deteccion.confidence <= 1.0
        ):
            return [], f"confidence invalida: {deteccion.confidence!r}"
        if len(deteccion.bbox_xyxy) != 4 or not all(
            isinstance(valor, (int, float))
            and not isinstance(valor, bool)
            and math.isfinite(valor)
            for valor in deteccion.bbox_xyxy
        ):
            return [], "bbox_xyxy invalido"
        serializadas.append(deteccion.to_dict())
    return serializadas, None


def _procesar_observacion(
    observacion: ObservacionFuente,
    detector: DetectorObservaciones,
    *,
    run_id: str,
    secuencia: int,
    modelo_compacto: dict[str, Any],
    modelo: Mapping[str, Any],
    filtros: Mapping[str, Any],
) -> dict[str, Any]:
    iniciado_en = _instante()
    inicio = time.perf_counter()
    frame_id = observacion.frame_id
    registro: dict[str, Any] = {
        "schema": ESQUEMA,
        "record": "observacion",
        "run_id": run_id,
        "observation_id": (
            f"{run_id}:{frame_id}" if frame_id else f"{run_id}:terminal-{secuencia:06d}"
        ),
        "sequence": secuencia,
        "source_id": observacion.source_id,
        "frame_id": frame_id,
        "frame_number": observacion.frame_number,
        "received_at": observacion.received_at,
        "started_at": iniciado_en,
        "finished_at": None,
        "duration_ms": None,
        "source_status": observacion.status,
        "status": "read_error",
        "technical_result": "error",
        "reason": None,
        "image": _dimensiones(observacion.width, observacion.height),
        "detections": None,
        "class_counts": None,
        "detector_timings_ms": None,
        "model": None,
        "error": None,
    }

    if observacion.status == "end_of_stream":
        registro.update(status="end_of_stream", technical_result=None)
    elif observacion.status == "discarded":
        registro.update(
            status="discarded",
            technical_result=None,
            reason=observacion.discard_reason or "sampling_interval",
        )
    elif observacion.status == "read_error":
        motivo = observacion.discard_reason or "read_error"
        registro.update(status="read_error", technical_result="error", reason=motivo, error=motivo)
    elif observacion.status == "sampled":
        try:
            _procesar_muestra(registro, observacion, detector, modelo_compacto, modelo, filtros)
        except Exception as error:
            logger.exception(
                "pipeline_invalid_detector_result source_id=%s frame_id=%s",
                observacion.source_id, observacion.frame_id,
            )
            registro.update(
                status="detector_error", technical_result="error",
                reason="invalid_detector_result",
                detections=None, class_counts=None, detector_timings_ms=None,
                model=modelo_compacto,
                error=f"{type(error).__name__}: {error}",
            )
    else:
        motivo = f"unknown_source_status:{observacion.status}"
        registro.update(
            status="read_error", technical_result="error", reason=motivo,
            error=f"estado de fuente desconocido: {observacion.status!r}",
        )
    registro["finished_at"] = _instante()
    registro["duration_ms"] = _milisegundos(inicio)
    return registro


def _procesar_muestra(
    registro: dict[str, Any],
    observacion: ObservacionFuente,
    detector: DetectorObservaciones,
    modelo_compacto: dict[str, Any],
    modelo: Mapping[str, Any],
    filtros: Mapping[str, Any],
) -> None:
    if observacion.image is None:
        registro.update(
            status="read_error", technical_result="error", reason="missing_image",
            error="la fuente marco sampled sin entregar imagen",
        )
        return
    if not observacion.frame_id:
        registro.update(
            status="read_error", technical_result="error", reason="missing_frame_id",
            error="la fuente marco sampled sin frame_id",
        )
        return

    try:
        resultado = detector.detectar(
            observacion.image,
            source_id=observacion.source_id,
            frame_id=observacion.frame_id,
            frame_number=observacion.frame_number,
            received_at=observacion.received_at,
        )
    except Exception as error:  # una falla del detector no inventa una deteccion
        logger.exception(
            "pipeline_detector_exception source_id=%s frame_id=%s",
            observacion.source_id, observacion.frame_id,
        )
        registro.update(
            status="detector_error", technical_result="error", reason="detector_exception",
            error=f"{type(error).__name__}: {error}",
        )
        return

    if not isinstance(resultado, ResultadoCuadro):
        registro.update(
            status="detector_error", technical_result="error",
            reason="invalid_detector_result",
            error=f"el detector devolvio {type(resultado).__name__}",
        )
        return

    if resultado.model != modelo or resultado.filters != filtros:
        registro.update(
            status="detector_error", technical_result="error",
            reason="detector_provenance_mismatch",
            error="model o filters no coinciden con la cabecera del lote",
            model=modelo_compacto,
        )
        return

    desajuste = _desajuste_identidad(observacion, resultado)
    if desajuste:
        registro.update(
            status="detector_error", technical_result="error",
            reason="detector_identity_mismatch", error=desajuste,
            detector_timings_ms=_tiempos(resultado), model=modelo_compacto,
        )
        return


    if resultado.status == "error":
        registro.update(
            status="detector_error", technical_result="error", reason="detector_status_error",
            error=resultado.error or "el detector reporto error sin detalle",
            detector_timings_ms=_tiempos(resultado), model=modelo_compacto,
        )
        return
    if resultado.status != "ok":
        registro.update(
            status="detector_error", technical_result="error",
            reason="invalid_detector_status",
            error=f"estado de detector inesperado: {resultado.status!r}",
            detector_timings_ms=_tiempos(resultado), model=modelo_compacto,
        )
        return

    detecciones, error_detecciones = _serializar_detecciones(resultado.detections)
    if error_detecciones:
        registro.update(
            status="detector_error", technical_result="error",
            reason="invalid_detector_result", error=error_detecciones,
            detector_timings_ms=_tiempos(resultado), model=modelo_compacto,
        )
        return

    conteo = dict(sorted(Counter(d["class_name"] for d in detecciones).items()))
    if dict(resultado.class_counts or {}) != conteo:
        registro.update(
            status="detector_error", technical_result="error",
            reason="inconsistent_class_counts",
            error=f"class_counts {resultado.class_counts!r} != {conteo!r}",
            detector_timings_ms=_tiempos(resultado), model=modelo_compacto,
        )
        return

    registro.update(
        status="processed",
        technical_result="ok" if detecciones else "no_detections",
        reason=None,
        image=_dimensiones(resultado.image_width, resultado.image_height) or registro["image"],
        detections=detecciones,
        class_counts=conteo,
        detector_timings_ms=_tiempos(resultado),
        model=modelo_compacto,
        error=None,
    )


def _registro_error_fuente(
    *, run_id: str, source_id: str, secuencia: int, error: Exception
) -> dict[str, Any]:
    iniciado_en = _instante()
    inicio = time.perf_counter()
    return {
        "schema": ESQUEMA,
        "record": "observacion",
        "run_id": run_id,
        "observation_id": f"{run_id}:terminal-{secuencia:06d}",
        "sequence": secuencia,
        "source_id": source_id,
        "frame_id": None,
        "frame_number": None,
        "received_at": None,
        "started_at": iniciado_en,
        "finished_at": _instante(),
        "duration_ms": _milisegundos(inicio),
        "source_status": "read_error",
        "status": "read_error",
        "technical_result": "error",
        "reason": "source_exception",
        "image": None,
        "detections": None,
        "class_counts": None,
        "detector_timings_ms": None,
        "model": None,
        "error": f"{type(error).__name__}: fallo del lector",
    }


def ejecutar_pipeline(
    source: str | Path,
    detector: DetectorObservaciones,
    *,
    source_id: str,
    salida: Path | str | None = None,
    sample_every: int = 1,
    interval_ms: float = 200.0,
    max_frames: int | None = None,
    max_seconds: float | None = None,
    run_id: str | None = None,
    overwrite: bool = False,
    lector: Callable[..., Iterator[ObservacionFuente]] | None = None,
) -> dict[str, Any]:
    """Ejecuta fuente -> detector y persiste observaciones primarias.

    ``max_seconds=0`` o ``None`` significa sin limite. La salida se escribe en
    un temporal y se reemplaza al final; una falla no deja un JSONL a medias.
    Devuelve el resumen persistido.
    """
    if not isinstance(source, (str, Path)):
        raise ValueError("source debe ser una ruta o URL")
    texto_fuente = str(source)
    if not texto_fuente.strip():
        raise ValueError("source no puede estar vacio")
    validar_configuracion_fuente(
        source_id=source_id, sample_every=sample_every,
        interval_ms=interval_ms, max_frames=max_frames,
    )
    _validar_max_seconds(max_seconds)
    if not callable(getattr(detector, "detectar", None)):
        raise ValueError("el detector debe exponer detectar()")
    modelo = _modelo_de(detector)
    filtros = _filtros_de(detector)

    if run_id is None:
        run_id = nuevo_run_id()
    _validar_run_id(run_id)
    if salida is not None and not str(salida).strip():
        raise ValueError("salida no puede estar vacia")
    ruta_salida = Path(salida) if salida is not None else ruta_salida_predeterminada(run_id)
    if ruta_salida.is_dir():
        raise IsADirectoryError(f"la salida debe ser un archivo, no un directorio: {ruta_salida}")
    if ruta_salida.suffix.lower() != ".jsonl":
        raise ValueError("la salida debe tener extension .jsonl")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", texto_fuente, re.IGNORECASE):
        ruta_fuente = Path(texto_fuente)
        if ruta_fuente.is_file() and ruta_salida.resolve() == ruta_fuente.resolve():
            raise ValueError("la salida no puede reemplazar el archivo de entrada")
    _validar_ruta_salida(ruta_salida, overwrite=overwrite)

    lector = lector or leer_fuente
    if not callable(lector):
        raise ValueError("lector debe ser invocable")

    iniciado_en = _instante()
    inicio = time.perf_counter()
    entorno = _version_entorno()
    fuente_info = {
        "id": source_id,
        "ref": _fuente_publica(texto_fuente),
        "kind": _tipo_fuente(texto_fuente),
    }
    configuracion = {
        "sample_every": sample_every,
        "interval_ms": float(interval_ms),
        "max_frames": max_frames,
        "max_seconds": float(max_seconds) if max_seconds is not None else None,
    }
    cabecera = {
        "schema": ESQUEMA,
        "record": "cabecera",
        "run_id": run_id,
        "created_at": iniciado_en,
        "pipeline": VERSION_PIPELINE,
        "source": fuente_info,
        "config": configuracion,
        "model": modelo,
        "filters": filtros,
        "versions": entorno,
    }
    contadores = _contadores_nuevos()
    secuencia = 0
    estado_final: str | None = None
    temporal = ruta_salida.with_name(
        f".{ruta_salida.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    iterador = lector(
        texto_fuente,
        source_id=source_id,
        sample_every=sample_every,
        interval_ms=interval_ms,
        max_frames=max_frames,
    )
    try:
        iterador = iter(iterador)
    except TypeError as error:
        raise ValueError("lector debe devolver un iterador") from error

    modelo_compacto = _modelo_compacto(modelo)
    temporal_completo = False
    try:
        with temporal.open("w", encoding="utf-8", newline="\n") as archivo:
            _escribir_registro(archivo, cabecera)
            while True:
                try:
                    observacion = next(iterador)
                except StopIteration:
                    break
                except ValueError:
                    # Configuracion invalida detectada por la fuente: no se persiste.
                    raise
                except Exception as error:
                    logger.error(
                        "pipeline_source_exception source=%s type=%s",
                        fuente_info["ref"], type(error).__name__,
                    )
                    secuencia += 1
                    registro = _registro_error_fuente(
                        run_id=run_id, source_id=source_id, secuencia=secuencia, error=error,
                    )
                    _acumular(registro, contadores)
                    _escribir_registro(archivo, registro)
                    estado_final = "source_exception"
                    break

                secuencia += 1
                registro = _procesar_observacion(
                    observacion, detector,
                    run_id=run_id, secuencia=secuencia,
                    modelo_compacto=modelo_compacto,
                    modelo=modelo, filtros=filtros,
                )
                _acumular(registro, contadores)
                _escribir_registro(archivo, registro)
                if registro["status"] == "end_of_stream":
                    estado_final = "end_of_stream"
                    break
                # Una imagen corrupta dentro de una secuencia no invalida los
                # cuadros posteriores. Los demás errores son terminales.
                if registro["status"] == "read_error" and registro["reason"] != "invalid_image":
                    estado_final = registro["reason"] or "read_error"
                    break
                if max_frames is not None and contadores["frames_received"] >= max_frames:
                    estado_final = "max_frames"
                    break
                if max_seconds and time.perf_counter() - inicio >= max_seconds:
                    estado_final = "max_duration"
                    break

            if estado_final is None:
                estado_final = "max_frames" if max_frames is not None else "source_exhausted"
            finalizado_en = _instante()
            duracion_ms = _milisegundos(inicio)
            conteos = _serializar_contadores(contadores)
            resumen = {
                "schema": ESQUEMA,
                "record": "resumen",
                "run_id": run_id,
                "started_at": iniciado_en,
                "finished_at": finalizado_en,
                "duration_ms": duracion_ms,
                "end_state": estado_final,
                "technical_result": _resultado_tecnico(contadores),
                "source": fuente_info,
                "config": configuracion,
                "model": modelo,
                "filters": filtros,
                "counts": conteos,
                "versions": entorno,
            }
            _escribir_registro(archivo, resumen)
        temporal_completo = True
        try:
            os.replace(temporal, ruta_salida)
        except OSError as error:
            raise OSError(
                f"no se pudo publicar el JSONL; temporal completo conservado en {temporal}"
            ) from error
    finally:
        # Un temporal completo se conserva si falla el reemplazo final.
        if not temporal_completo:
            temporal.unlink(missing_ok=True)
    return resumen


def _escribir_registro(archivo: TextIO, registro: Mapping[str, Any]) -> None:
    archivo.write(json.dumps(registro, ensure_ascii=False, sort_keys=True) + "\n")
    archivo.flush()


def leer_registros(ruta: Path | str) -> list[dict[str, Any]]:
    """Lee un JSONL de P6-03 sin interpretarlo; falla si una linea no es objeto."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise FileNotFoundError(f"no existe el archivo de observaciones: {ruta}")
    registros: list[dict[str, Any]] = []
    with ruta.open("r", encoding="utf-8") as archivo:
        for numero, linea in enumerate(archivo, start=1):
            if not linea.strip():
                raise ErrorObservaciones(f"linea vacia en {ruta}:{numero}")
            try:
                registro = json.loads(linea)
            except json.JSONDecodeError as error:
                raise ErrorObservaciones(
                    f"JSON invalido en {ruta}:{numero}: {error.msg}"
                ) from error
            if not isinstance(registro, dict):
                raise ErrorObservaciones(f"la linea {numero} de {ruta} no es un objeto JSON")
            registros.append(registro)
    return registros


def _validar_instante(valor: object, campo: str, numero: int) -> datetime:
    if not isinstance(valor, str) or not valor:
        raise ErrorObservaciones(f"observacion {numero}: {campo} debe ser un instante ISO")
    try:
        fecha = datetime.fromisoformat(valor)
    except ValueError as error:
        raise ErrorObservaciones(
            f"observacion {numero}: {campo} no es un instante ISO valido: {valor!r}"
        ) from error
    if fecha.tzinfo is None:
        raise ErrorObservaciones(f"observacion {numero}: {campo} debe incluir zona horaria")
    return fecha


def _validar_numero_no_negativo(valor: object, campo: str, numero: int) -> float:
    if (
        isinstance(valor, bool)
        or not isinstance(valor, (int, float))
        or not math.isfinite(valor)
        or valor < 0
    ):
        raise ErrorObservaciones(f"observacion {numero}: {campo} debe ser un numero finito >= 0")
    return float(valor)


def _validar_observacion(registro: Mapping[str, Any], numero: int, run_id: str) -> None:
    if registro.get("schema") != ESQUEMA:
        raise ErrorObservaciones(f"observacion {numero}: schema distinto de {ESQUEMA}")
    if registro.get("record") != "observacion":
        raise ErrorObservaciones(f"registro {numero}: se esperaba record='observacion'")
    if registro.get("run_id") != run_id:
        raise ErrorObservaciones(f"observacion {numero}: run_id no coincide con la cabecera")
    if registro.get("sequence") != numero:
        raise ErrorObservaciones(f"observacion {numero}: sequence no es consecutivo")
    observation_id = registro.get("observation_id")
    if not isinstance(observation_id, str) or not observation_id:
        raise ErrorObservaciones(f"observacion {numero}: observation_id vacio")
    source_id = registro.get("source_id")
    if not isinstance(source_id, str) or not source_id:
        raise ErrorObservaciones(f"observacion {numero}: source_id vacio")
    estado = registro.get("status")
    if estado not in ESTADOS_OBSERVACION:
        raise ErrorObservaciones(f"observacion {numero}: status invalido {estado!r}")
    estado_fuente = registro.get("source_status")
    if estado_fuente not in ESTADOS_FUENTE:
        raise ErrorObservaciones(f"observacion {numero}: source_status invalido {estado_fuente!r}")
    fuentes_permitidas = {
        "processed": {"sampled"},
        "discarded": {"discarded"},
        "read_error": {"read_error", "sampled"},
        "detector_error": {"sampled"},
        "end_of_stream": {"end_of_stream"},
    }[estado]
    if estado_fuente not in fuentes_permitidas:
        raise ErrorObservaciones(
            f"observacion {numero}: source_status {estado_fuente!r} incoherente con {estado!r}"
        )
    resultado = registro.get("technical_result")
    if resultado not in RESULTADOS_TECNICOS and resultado is not None:
        raise ErrorObservaciones(
            f"observacion {numero}: technical_result invalido {resultado!r}"
        )

    resultado_esperado = {
        "processed": {"ok", "no_detections"},
        "discarded": {None},
        "read_error": {"error"},
        "detector_error": {"error"},
        "end_of_stream": {None},
    }[estado]
    if resultado not in resultado_esperado:
        raise ErrorObservaciones(
            f"observacion {numero}: technical_result {resultado!r} incoherente con {estado!r}"
        )

    frame_id = registro.get("frame_id")
    frame_number = registro.get("frame_number")
    if estado in {"processed", "discarded", "read_error"}:
        if not isinstance(frame_id, str) or not frame_id:
            if estado != "read_error" or registro.get("reason") != "source_exception":
                raise ErrorObservaciones(f"observacion {numero}: frame_id requerido para {estado}")
    if frame_number is not None:
        if isinstance(frame_number, bool) or not isinstance(frame_number, int) or frame_number < 1:
            raise ErrorObservaciones(f"observacion {numero}: frame_number invalido")
    recibido_en = registro.get("received_at")
    if recibido_en is not None:
        _validar_instante(recibido_en, "received_at", numero)
    iniciado = _validar_instante(registro.get("started_at"), "started_at", numero)
    finalizado = _validar_instante(registro.get("finished_at"), "finished_at", numero)
    if finalizado < iniciado:
        raise ErrorObservaciones(f"observacion {numero}: finished_at anterior a started_at")
    _validar_numero_no_negativo(registro.get("duration_ms"), "duration_ms", numero)


    if estado in {"read_error", "detector_error"}:
        if not isinstance(registro.get("reason"), str) or not registro["reason"]:
            raise ErrorObservaciones(f"observacion {numero}: reason requerido para {estado}")
        if not isinstance(registro.get("error"), str) or not registro["error"]:
            raise ErrorObservaciones(f"observacion {numero}: error requerido para {estado}")
        if registro.get("detections") is not None:
            raise ErrorObservaciones(f"observacion {numero}: una falla no puede tener detecciones")
    if estado == "discarded":
        if not isinstance(registro.get("reason"), str) or not registro["reason"]:
            raise ErrorObservaciones(f"observacion {numero}: discarded requiere reason")
        if registro.get("detections") is not None:
            raise ErrorObservaciones(f"observacion {numero}: discarded no puede tener detecciones")
    if estado == "end_of_stream" and registro.get("reason") is not None:
        raise ErrorObservaciones(f"observacion {numero}: end_of_stream no lleva reason")

    if estado == "processed":
        detecciones = registro.get("detections")
        conteo = registro.get("class_counts")
        if not isinstance(detecciones, list) or not isinstance(conteo, dict):
            raise ErrorObservaciones(
                f"observacion {numero}: processed requiere detections y class_counts"
            )
        esperado: Counter = Counter()
        for deteccion in detecciones:
            if not isinstance(deteccion, dict) or not isinstance(deteccion.get("class_name"), str):
                raise ErrorObservaciones(f"observacion {numero}: deteccion invalida")
            confianza = deteccion.get("confidence")
            if (
                isinstance(confianza, bool)
                or not isinstance(confianza, (int, float))
                or not math.isfinite(confianza)
                or not 0.0 <= confianza <= 1.0
            ):
                raise ErrorObservaciones(f"observacion {numero}: confidence invalida")
            esperado[deteccion["class_name"]] += 1
        if dict(esperado) != conteo:
            raise ErrorObservaciones(
                f"observacion {numero}: class_counts no coincide con detections"
            )
        if (resultado == "ok") != bool(detecciones):
            raise ErrorObservaciones(
                f"observacion {numero}: technical_result incoherente con detections"
            )


def _validar_cabecera(registro: Mapping[str, Any]) -> str:
    if registro.get("schema") != ESQUEMA:
        raise ErrorObservaciones(f"cabecera: schema distinto de {ESQUEMA}")
    if registro.get("record") != "cabecera":
        raise ErrorObservaciones("el primer registro debe ser la cabecera")
    if registro.get("pipeline") != VERSION_PIPELINE:
        raise ErrorObservaciones("cabecera: version de pipeline distinta")
    run_id = registro.get("run_id")
    if not isinstance(run_id, str):
        raise ErrorObservaciones("cabecera: run_id invalido")
    _validar_run_id(run_id)
    _validar_instante(registro.get("created_at"), "created_at", 0)
    fuente = registro.get("source")
    if not isinstance(fuente, dict) or not isinstance(fuente.get("id"), str):
        raise ErrorObservaciones("cabecera: source invalido")
    if not isinstance(fuente.get("ref"), str) or not isinstance(fuente.get("kind"), str):
        raise ErrorObservaciones("cabecera: source.ref y source.kind son requeridos")
    if not isinstance(registro.get("config"), dict):
        raise ErrorObservaciones("cabecera: config invalido")
    if not isinstance(registro.get("model"), dict):
        raise ErrorObservaciones("cabecera: model invalido")
    if not isinstance(registro.get("filters"), dict):
        raise ErrorObservaciones("cabecera: filters invalido")
    if not isinstance(registro.get("versions"), dict):
        raise ErrorObservaciones("cabecera: versions invalido")
    return run_id


def _validar_contadores(conteo: object) -> None:
    if not isinstance(conteo, dict):
        raise ErrorObservaciones("resumen: counts debe ser un objeto")
    esperadas = set(_contadores_nuevos())
    if set(conteo) != esperadas:
        raise ErrorObservaciones("resumen: counts no tiene las claves del esquema")
    for clave in ("discarded_by_reason", "read_errors_by_reason",
                  "detector_errors_by_reason", "detections_by_class"):
        valor = conteo[clave]
        if not isinstance(valor, dict) or not all(
            isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v >= 0
            for k, v in valor.items()
        ):
            raise ErrorObservaciones(f"resumen: counts[{clave!r}] invalido")
    for clave in esperadas - {
        "discarded_by_reason", "read_errors_by_reason",
        "detector_errors_by_reason", "detections_by_class",
    }:
        valor = conteo[clave]
        if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
            raise ErrorObservaciones(f"resumen: counts[{clave!r}] invalido")


def _validar_resumen(registro: Mapping[str, Any], run_id: str) -> None:
    if registro.get("schema") != ESQUEMA:
        raise ErrorObservaciones(f"resumen: schema distinto de {ESQUEMA}")
    if registro.get("record") != "resumen":
        raise ErrorObservaciones("el ultimo registro debe ser el resumen")
    if registro.get("run_id") != run_id:
        raise ErrorObservaciones("resumen: run_id no coincide con la cabecera")
    if registro.get("technical_result") not in {"ok", "no_data", "with_errors"}:
        raise ErrorObservaciones("resumen: technical_result invalido")
    if not isinstance(registro.get("end_state"), str) or not registro["end_state"]:
        raise ErrorObservaciones("resumen: end_state invalido")
    _validar_contadores(registro.get("counts"))
    _validar_instante(registro.get("started_at"), "started_at", 0)
    _validar_instante(registro.get("finished_at"), "finished_at", 0)
    _validar_numero_no_negativo(registro.get("duration_ms"), "duration_ms", 0)


def validar_registros(
    registros: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Valida cabecera, secuencia, IDs y resumen de un lote de P6-03.

    Devuelve ``(cabecera, observaciones, resumen)`` para que P6-04 consuma solo
    datos que respetan el contrato.
    """
    if not isinstance(registros, list) or len(registros) < 2:
        raise ErrorObservaciones("el lote debe incluir cabecera y resumen")
    cabecera = registros[0]
    resumen = registros[-1]
    if not isinstance(cabecera, dict) or not isinstance(resumen, dict):
        raise ErrorObservaciones("cabecera y resumen deben ser objetos JSON")
    run_id = _validar_cabecera(cabecera)
    _validar_resumen(resumen, run_id)
    for campo in ("source", "config", "model", "filters", "versions"):
        if resumen.get(campo) != cabecera.get(campo):
            raise ErrorObservaciones(
                f"resumen: {campo} no coincide con la cabecera"
            )
    observaciones = registros[1:-1]
    if not all(isinstance(registro, dict) for registro in observaciones):
        raise ErrorObservaciones("todas las observaciones deben ser objetos JSON")

    vistos: set[str] = set()
    frames: set[str] = set()
    frame_anterior = 0
    for numero, registro in enumerate(observaciones, start=1):
        _validar_observacion(registro, numero, run_id)
        if registro["source_id"] != cabecera["source"]["id"]:
            raise ErrorObservaciones(
                f"observacion {numero}: source_id distinto de la cabecera"
            )
        observation_id = registro["observation_id"]
        if observation_id in vistos:
            raise ErrorObservaciones(f"observacion {numero}: observation_id duplicado")
        vistos.add(observation_id)
        frame_id = registro.get("frame_id")
        if frame_id is not None:
            if frame_id in frames:
                raise ErrorObservaciones(f"observacion {numero}: frame_id duplicado")
            frames.add(frame_id)
        frame_number = registro.get("frame_number")
        if frame_number is not None:
            if frame_number <= frame_anterior:
                raise ErrorObservaciones(
                    f"observacion {numero}: frame_number no es estrictamente creciente"
                )
            frame_anterior = frame_number
    return cabecera, observaciones, resumen


def recalcular_resumen_desde_registros(
    registros: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recalcula contadores y resultado tecnico sin fuente ni modelo."""
    cabecera, observaciones, resumen = validar_registros(registros)
    contadores = _contadores_nuevos()
    for observacion in observaciones:
        _acumular(observacion, contadores)
    conteos = _serializar_contadores(contadores)
    resultado = _resultado_tecnico(contadores)
    recalculado = {
        "run_id": cabecera["run_id"],
        "counts": conteos,
        "technical_result": resultado,
        "observaciones": len(observaciones),
    }
    coincide = (
        conteos == resumen.get("counts")
        and resultado == resumen.get("technical_result")
    )
    return {
        "run_id": cabecera["run_id"],
        "coincide": coincide,
        "recalculado": recalculado,
        "resumen_almacenado": resumen,
    }


def recalcular_resumen(ruta: Path | str) -> dict[str, Any]:
    """Recalcula un lote persistido; no abre la fuente ni carga el modelo."""
    return recalcular_resumen_desde_registros(leer_registros(ruta))
