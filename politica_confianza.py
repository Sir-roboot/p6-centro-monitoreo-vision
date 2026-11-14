"""Politica de confianza P6-04 sobre observaciones primarias de P6-03.

Lee un JSONL ya persistido, valida su contrato y clasifica las puntuaciones
existentes en zonas baja, duda y aceptada. No ejecuta inferencia, no aplica NMS
ni modifica puntuaciones, y no crea eventos, capturas, alertas ni metricas
G1-G7. La referencia y la regla de cambio sostenido se documentan en el
artefacto para que P6-05 pueda consumirlas sin reemplazar esta politica.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
from typing import Any, Mapping, Sequence

from observaciones import (
    ErrorObservaciones,
    leer_registros,
    recalcular_resumen_desde_registros,
    validar_registros,
)

ESQUEMA = "p6-04/1"
VERSION_POLITICA = "p6-04"
RUTA_POLITICAS = Path("datos_locales") / "politicas"

UMBRAL_INFERIOR_PREDETERMINADO = 0.30
UMBRAL_SUPERIOR_PREDETERMINADO = 0.70

# Contrato documentado del detector de P6-02. No se aplica aqui: se conserva
# como referencia para explicar que puntuaciones bajas ya existen en P6-03.
PISO_SALIDA_YOLOX = 0.10
NMS_YOLOX = "class-agnostic"
UMBRAL_NMS_YOLOX = 0.45

VENTANA_FRAMES_PREDETERMINADA = 5
MIN_PUNTUACIONES_VENTANA_PREDETERMINADA = 3
VENTANAS_CONSECUTIVAS_PREDETERMINADAS = 2
FRACCION_BAJA_MINIMA_PREDETERMINADA = 0.5
CAIDA_MEDIA_MINIMA_PREDETERMINADA = 0.10
MIN_FRAMES_REFERENCIA_PREDETERMINADOS = 3
MIN_PUNTUACIONES_REFERENCIA_PREDETERMINADAS = 5

ZONAS = ("low", "doubt", "accepted")
ESTADOS_FRAME = (
    "scored",
    "no_detections",
    "discarded",
    "technical_error",
    "no_score",
    "end_of_stream",
)
ESTADOS_REFERENCIA = ("ok", "insufficient", "incompatible")
ESTADOS_CAMBIO = (
    "sustained_change",
    "no_sustained_change",
    "insufficient_evidence",
    "reference_incompatible",
)

PUNTUACIONES_DOG = (0.884, 0.6717, 0.5386, 0.1594, 0.1128)
JUSTIFICACION_EXPLORATORIA = (
    "Umbrales exploratorios 0.30/0.70 elegidos a partir de las puntuaciones "
    "reales de una sola imagen dog.jpg documentadas en P6-02 "
    "(0.8840, 0.6717, 0.5386, 0.1594, 0.1128). Una sola imagen no es una "
    "referencia robusta para C1/C2; los umbrales deben revisarse con "
    "observaciones reales de esas escenas."
)

_CAMPOS_MODELO_COMPACTO = ("name", "dataset", "version", "sha256", "size_bytes")
_PATRON_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ErrorConfianza(ValueError):
    """Configuracion o lote que no permite construir una politica confiable."""


def _es_numero(valor: object) -> bool:
    return (
        isinstance(valor, (int, float))
        and not isinstance(valor, bool)
        and math.isfinite(valor)
    )


def _validar_umbral(nombre: str, valor: object) -> float:
    if not _es_numero(valor):
        raise ErrorConfianza(f"{nombre} debe ser un numero finito")
    numero = float(valor)
    if not 0.0 <= numero <= 1.0:
        raise ErrorConfianza(f"{nombre} debe cumplir 0 <= {nombre} <= 1")
    return numero


def _validar_entero_positivo(nombre: str, valor: object) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 1:
        raise ErrorConfianza(f"{nombre} debe ser un entero >= 1")
    return valor


def _validar_fraccion(nombre: str, valor: object) -> float:
    if not _es_numero(valor):
        raise ErrorConfianza(f"{nombre} debe ser un numero finito")
    numero = float(valor)
    if not 0.0 <= numero <= 1.0:
        raise ErrorConfianza(f"{nombre} debe cumplir 0 <= {nombre} <= 1")
    return numero


def _canonico(valor: object) -> str:
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _huella(valor: object) -> str:
    return hashlib.sha256(_canonico(valor).encode("utf-8")).hexdigest()


def _instante() -> str:
    return datetime.now(timezone.utc).isoformat()


def _definiciones_zonas(inferior: float, superior: float) -> dict[str, str]:
    return {
        "low": f"s < {inferior:g}",
        "doubt": f"{inferior:g} <= s < {superior:g}",
        "accepted": f"s >= {superior:g}",
    }


@dataclass(frozen=True)
class UmbralesConfianza:
    """Dos umbrales estrictos: 0 <= inferior < superior <= 1."""

    inferior: float = UMBRAL_INFERIOR_PREDETERMINADO
    superior: float = UMBRAL_SUPERIOR_PREDETERMINADO

    def __post_init__(self) -> None:
        inferior = _validar_umbral("inferior", self.inferior)
        superior = _validar_umbral("superior", self.superior)
        if not inferior < superior:
            raise ErrorConfianza("se requiere inferior < superior")
        object.__setattr__(self, "inferior", inferior)
        object.__setattr__(self, "superior", superior)

    def clasificar(self, puntuacion: object) -> str:
        return clasificar_puntuacion(puntuacion, self)

    def to_dict(self) -> dict[str, float]:
        return {"inferior": self.inferior, "superior": self.superior}

    def definiciones(self) -> dict[str, str]:
        return _definiciones_zonas(self.inferior, self.superior)


def clasificar_puntuacion(
    puntuacion: object, umbrales: UmbralesConfianza | None = None
) -> str:
    """Clasifica una puntuacion persistida con fronteras exactas.

    ``low``: s < inferior; ``doubt``: inferior <= s < superior; ``accepted``:
    s >= superior. No redondea ni inventa valores: solo acepta numeros finitos
    en [0, 1].
    """

    reglas = umbrales or UmbralesConfianza()
    if not _es_numero(puntuacion):
        raise ErrorConfianza("la puntuacion debe ser un numero finito")
    valor = float(puntuacion)
    if not 0.0 <= valor <= 1.0:
        raise ErrorConfianza("la puntuacion debe cumplir 0 <= s <= 1")
    if valor < reglas.inferior:
        return "low"
    if valor < reglas.superior:
        return "doubt"
    return "accepted"


@dataclass(frozen=True)
class ReglaCambioSostenido:
    """Ventanas no solapadas y evidencia sostenida de degradacion."""

    ventana_frames: int = VENTANA_FRAMES_PREDETERMINADA
    min_puntuaciones_ventana: int = MIN_PUNTUACIONES_VENTANA_PREDETERMINADA
    ventanas_consecutivas: int = VENTANAS_CONSECUTIVAS_PREDETERMINADAS
    fraccion_baja_minima: float = FRACCION_BAJA_MINIMA_PREDETERMINADA
    caida_media_minima: float = CAIDA_MEDIA_MINIMA_PREDETERMINADA

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "ventana_frames",
            _validar_entero_positivo("ventana_frames", self.ventana_frames),
        )
        object.__setattr__(
            self,
            "min_puntuaciones_ventana",
            _validar_entero_positivo(
                "min_puntuaciones_ventana", self.min_puntuaciones_ventana
            ),
        )
        consecutivas = _validar_entero_positivo(
            "ventanas_consecutivas", self.ventanas_consecutivas
        )
        if consecutivas < 2:
            raise ErrorConfianza("ventanas_consecutivas debe ser >= 2")
        object.__setattr__(self, "ventanas_consecutivas", consecutivas)
        object.__setattr__(
            self,
            "fraccion_baja_minima",
            _validar_fraccion("fraccion_baja_minima", self.fraccion_baja_minima),
        )
        object.__setattr__(
            self,
            "caida_media_minima",
            _validar_fraccion("caida_media_minima", self.caida_media_minima),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_frames": self.ventana_frames,
            "window_min_scores": self.min_puntuaciones_ventana,
            "consecutive_windows": self.ventanas_consecutivas,
            "low_fraction_min": self.fraccion_baja_minima,
            "mean_drop_min": self.caida_media_minima,
        }


@dataclass(frozen=True)
class MinimosReferencia:
    """Cantidad minima de evidencia para no confundir vacio con normalidad."""

    frames: int = MIN_FRAMES_REFERENCIA_PREDETERMINADOS
    puntuaciones: int = MIN_PUNTUACIONES_REFERENCIA_PREDETERMINADAS

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frames", _validar_entero_positivo("frames", self.frames)
        )
        object.__setattr__(
            self,
            "puntuaciones",
            _validar_entero_positivo("puntuaciones", self.puntuaciones),
        )

    def to_dict(self) -> dict[str, int]:
        return {"frames": self.frames, "scored_detections": self.puntuaciones}


@dataclass(frozen=True)
class ConfiguracionConfianza:
    """Configuracion completa, versionada dentro del artefacto."""

    umbrales: UmbralesConfianza = field(default_factory=UmbralesConfianza)
    regla: ReglaCambioSostenido = field(default_factory=ReglaCambioSostenido)
    minimos_referencia: MinimosReferencia = field(
        default_factory=MinimosReferencia
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "thresholds": self.umbrales.to_dict(),
            "window": self.regla.to_dict(),
            "reference_minimums": self.minimos_referencia.to_dict(),
        }



@dataclass(frozen=True)
class Puntuacion:
    """Puntuacion ya persistida por P6-03; se conserva sin redondear."""

    observation_id: str
    frame_id: str
    frame_number: int
    detection_index: int
    class_name: str
    confidence: float
    zone: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "frame_id": self.frame_id,
            "frame_number": self.frame_number,
            "detection_index": self.detection_index,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "zone": self.zone,
        }


def _modelo_compacto(modelo: Mapping[str, Any]) -> dict[str, Any]:
    return {
        campo: modelo[campo]
        for campo in _CAMPOS_MODELO_COMPACTO
        if campo in modelo
    }


def _identidad(cabecera: Mapping[str, Any]) -> dict[str, Any]:
    modelo = cabecera["model"]
    filtros = cabecera["filters"]
    huella = _huella({"model": modelo, "filters": filtros})
    return {"model": modelo, "filters": filtros, "sha256": huella}


def _verificar_modelos_observaciones(
    cabecera: Mapping[str, Any], observaciones: Sequence[Mapping[str, Any]]
) -> None:
    esperado = _modelo_compacto(cabecera["model"])
    for posicion, observacion in enumerate(observaciones, start=1):
        modelo = observacion.get("model")
        if modelo is not None and modelo != esperado:
            raise ErrorConfianza(
                f"observacion {posicion}: model no coincide con la cabecera del lote"
            )


def _validar_lote(
    registros: object, etiqueta: str
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(registros, list):
        raise ErrorConfianza(f"{etiqueta}: el lote debe ser una lista de registros")
    try:
        cabecera, observaciones, resumen = validar_registros(registros)
    except ErrorObservaciones as exc:
        raise ErrorConfianza(f"{etiqueta}: {exc}") from exc
    try:
        recalculado = recalcular_resumen_desde_registros(registros)
    except ErrorObservaciones as exc:
        raise ErrorConfianza(
            f"{etiqueta}: no se pudo recalcular el resumen: {exc}"
        ) from exc
    if not recalculado["coincide"]:
        raise ErrorConfianza(
            f"{etiqueta}: el resumen persistido no coincide con los registros; "
            "no se puede auditar la politica"
        )
    _verificar_modelos_observaciones(cabecera, observaciones)
    return cabecera, observaciones, resumen


def cargar_lote(
    ruta: str | Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Carga y valida un JSONL P6-03, incluyendo la reproducibilidad del resumen."""

    try:
        registros = leer_registros(ruta)
    except ErrorObservaciones as exc:
        raise ErrorConfianza(f"{ruta}: {exc}") from exc
    return _validar_lote(registros, str(ruta))



def _puntuacion_valida(confianza: object) -> float:
    if not _es_numero(confianza):
        raise ErrorConfianza("confidence debe ser un numero finito")
    valor = float(confianza)
    if not 0.0 <= valor <= 1.0:
        raise ErrorConfianza("confidence debe cumplir 0 <= s <= 1")
    return valor


def clasificar_frame(
    registro: Mapping[str, Any], umbrales: UmbralesConfianza | None = None
) -> str:
    """Clasifica registros sin inferir puntuaciones ni confundir el cierre con un frame."""

    if not isinstance(registro, Mapping):
        raise ErrorConfianza("el registro debe ser un objeto")
    reglas = umbrales or UmbralesConfianza()
    estado = registro.get("status")
    if estado == "discarded":
        return "discarded"
    if estado in {"read_error", "detector_error"}:
        return "technical_error"
    if estado == "end_of_stream":
        return "end_of_stream"
    if estado != "processed":
        raise ErrorConfianza(f"status de observacion no reconocido: {estado!r}")

    detecciones = registro.get("detections")
    if not isinstance(detecciones, list):
        raise ErrorConfianza(
            "una observacion processed debe tener detections como lista"
        )
    if not detecciones:
        return "no_detections"

    puntuadas = 0
    sin_puntuacion = 0
    for deteccion in detecciones:
        if not isinstance(deteccion, Mapping):
            raise ErrorConfianza("cada deteccion debe ser un objeto")
        class_name = deteccion.get("class_name")
        if not isinstance(class_name, str) or not class_name:
            raise ErrorConfianza("cada deteccion debe tener class_name no vacio")
        confianza = deteccion.get("confidence")
        if confianza is None:
            sin_puntuacion += 1
            continue
        _puntuacion_valida(confianza)
        puntuadas += 1
    if puntuadas and sin_puntuacion:
        raise ErrorConfianza(
            "la observacion mezcla detecciones con y sin confidence; "
            "no se puede construir un denominador auditable"
        )
    if not puntuadas:
        return "no_score"
    return "scored"


def _puntuaciones_de_registro(
    registro: Mapping[str, Any], umbrales: UmbralesConfianza
) -> list[Puntuacion]:
    if clasificar_frame(registro, umbrales) != "scored":
        return []
    observation_id = registro["observation_id"]
    frame_id = registro["frame_id"]
    frame_number = registro["frame_number"]
    resultado: list[Puntuacion] = []
    for indice, deteccion in enumerate(registro["detections"]):
        confianza = _puntuacion_valida(deteccion["confidence"])
        resultado.append(
            Puntuacion(
                observation_id=observation_id,
                frame_id=frame_id,
                frame_number=frame_number,
                detection_index=indice,
                class_name=deteccion["class_name"],
                confidence=confianza,
                zone=clasificar_puntuacion(confianza, umbrales),
            )
        )
    return resultado


def resumir_estados(
    observaciones: Sequence[Mapping[str, Any]],
    umbrales: UmbralesConfianza | None = None,
) -> dict[str, int]:
    """Cuenta todos los registros; las sumas deben dar ``total``."""

    reglas = umbrales or UmbralesConfianza()
    conteo = {
        "total": len(observaciones),
        **{estado: 0 for estado in ESTADOS_FRAME},
    }
    for observacion in observaciones:
        conteo[clasificar_frame(observacion, reglas)] += 1
    return conteo


def _frame_audit(registro: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "observation_id": registro["observation_id"],
        "frame_id": registro["frame_id"],
        "frame_number": registro["frame_number"],
    }


def _extraer_frames(
    observaciones: Sequence[Mapping[str, Any]], umbrales: UmbralesConfianza
) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for registro in observaciones:
        if clasificar_frame(registro, umbrales) != "scored":
            continue
        frames.append(
            {
                **_frame_audit(registro),
                "scores": _puntuaciones_de_registro(registro, umbrales),
            }
        )
    return frames


def _extraer_timeline(
    observaciones: Sequence[Mapping[str, Any]], umbrales: UmbralesConfianza
) -> list[dict[str, Any]]:
    """Conserva cada frame muestreado: las lagunas no pueden unir ventanas."""

    timeline: list[dict[str, Any]] = []
    for registro in observaciones:
        estado = clasificar_frame(registro, umbrales)
        if estado in {"discarded", "end_of_stream"}:
            continue
        timeline.append(
            {
                **_frame_audit(registro),
                "state": estado,
                "scores": _puntuaciones_de_registro(registro, umbrales)
                if estado == "scored" else [],
            }
        )
    return timeline


def _aplanar(frames: Sequence[Mapping[str, Any]]) -> list[Puntuacion]:
    return [puntuacion for frame in frames for puntuacion in frame["scores"]]


def _serializar_puntuaciones(
    puntuaciones: Sequence[Puntuacion],
) -> list[dict[str, Any]]:
    return [puntuacion.to_dict() for puntuacion in puntuaciones]


def _serializar_frames(frames: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**_frame_audit(frame), **({"state": frame["state"]} if "state" in frame else {})}
        for frame in frames
    ]



def _distribucion(puntuaciones: Sequence[Puntuacion]) -> dict[str, Any]:
    zonas = {zona: 0 for zona in ZONAS}
    for puntuacion in puntuaciones:
        zonas[puntuacion.zone] += 1
    total = len(puntuaciones)
    if total:
        fracciones: dict[str, float | None] = {
            zona: round(zonas[zona] / total, 6) for zona in ZONAS
        }
        media = round(
            sum(puntuacion.confidence for puntuacion in puntuaciones) / total, 6
        )
    else:
        fracciones = {zona: None for zona in ZONAS}
        media = None
    return {
        "scored_detections": total,
        "zones": zonas,
        "zone_fractions": fracciones,
        "mean_confidence": media,
    }


def _crear_ventanas(
    frames: Sequence[Mapping[str, Any]],
    regla: ReglaCambioSostenido,
    referencia_media: float | None,
) -> list[dict[str, Any]]:
    """Divide la evaluacion en ventanas consecutivas no solapadas."""

    ventanas: list[dict[str, Any]] = []
    for indice, inicio in enumerate(
        range(0, len(frames), regla.ventana_frames), start=1
    ):
        bloque = list(frames[inicio : inicio + regla.ventana_frames])
        puntuaciones = _aplanar(bloque)
        distribucion = _distribucion(puntuaciones)
        completa = (
            len(bloque) == regla.ventana_frames
            and all(frame.get("state", "scored") == "scored" for frame in bloque)
            and distribucion["scored_detections"]
            >= regla.min_puntuaciones_ventana
        )
        motivo_incompleta: str | None = None
        if len(bloque) < regla.ventana_frames:
            motivo_incompleta = "partial_window"
        elif any(frame.get("state", "scored") != "scored" for frame in bloque):
            motivo_incompleta = "unscored_or_missing_frame"
        elif (
            distribucion["scored_detections"]
            < regla.min_puntuaciones_ventana
        ):
            motivo_incompleta = "insufficient_scored_detections"

        degradada = False
        motivos_degradacion: list[str] = []
        if completa:
            fraccion_baja = distribucion["zone_fractions"]["low"]
            if fraccion_baja < regla.fraccion_baja_minima:
                motivos_degradacion.append("low_fraction_below_minimum")
            if regla.caida_media_minima > 0:
                if referencia_media is None:
                    motivos_degradacion.append("reference_mean_unavailable")
                elif (
                    distribucion["mean_confidence"]
                    > referencia_media - regla.caida_media_minima
                ):
                    motivos_degradacion.append("mean_drop_below_minimum")
            degradada = not motivos_degradacion
            if degradada:
                motivos_degradacion = ["degraded"]
        elif motivo_incompleta is not None:
            motivos_degradacion = [motivo_incompleta]

        caida = None
        if (
            referencia_media is not None
            and distribucion["mean_confidence"] is not None
        ):
            caida = round(
                referencia_media - distribucion["mean_confidence"], 6
            )
        ventanas.append(
            {
                "index": indice,
                "frames": _serializar_frames(bloque),
                "frame_count": len(bloque),
                "scored_detections": distribucion["scored_detections"],
                "zones": distribucion["zones"],
                "zone_fractions": distribucion["zone_fractions"],
                "mean_confidence": distribucion["mean_confidence"],
                "mean_drop_vs_reference": caida,
                "complete": completa,
                "incomplete_reason": motivo_incompleta,
                "degraded": degradada,
                "degradation_reasons": motivos_degradacion,
            }
        )
    return ventanas



def _crear_referencia(
    *,
    origen: str,
    cabecera: Mapping[str, Any],
    frames: Sequence[Mapping[str, Any]],
    configuracion: ConfiguracionConfianza,
    identidad_actual: Mapping[str, Any],
    etiqueta: str | None,
    interrumpida: bool = False,
) -> dict[str, Any]:
    puntuaciones = _aplanar(frames)
    distribucion = _distribucion(puntuaciones)
    identidad = _identidad(cabecera)
    coincide = identidad["sha256"] == identidad_actual["sha256"]
    if origen == "file" and not coincide:
        estado = "incompatible"
    elif interrumpida or (
        len(frames) < configuracion.minimos_referencia.frames
        or distribucion["scored_detections"]
        < configuracion.minimos_referencia.puntuaciones
    ):
        estado = "insufficient"
    else:
        estado = "ok"
    return {
        "origin": origen,
        "path": etiqueta,
        "run_id": cabecera["run_id"],
        "source_id": cabecera["source"]["id"],
        "identity": identidad,
        "identity_matches": coincide,
        "status": estado,
        "interrupted_by_unscored_frame": interrumpida,
        "minimums": configuracion.minimos_referencia.to_dict(),
        "frame_count": len(frames),
        "frames": _serializar_frames(frames),
        "scored_detections": distribucion["scored_detections"],
        "zones": distribucion["zones"],
        "zone_fractions": distribucion["zone_fractions"],
        "mean_confidence": distribucion["mean_confidence"],
        "scores": _serializar_puntuaciones(puntuaciones),
    }


def _evaluar_cambio(
    ventanas: Sequence[Mapping[str, Any]],
    regla: ReglaCambioSostenido,
    referencia: Mapping[str, Any],
) -> dict[str, Any]:
    completas = sum(1 for ventana in ventanas if ventana["complete"])
    incompletas = sum(1 for ventana in ventanas if not ventana["complete"])
    maximo = 0
    actual = 0
    for ventana in ventanas:
        if not ventana["complete"] or not ventana["degraded"]:
            actual = 0
            continue
        actual += 1
        maximo = max(maximo, actual)

    if referencia["status"] == "incompatible":
        estado = "reference_incompatible"
        motivo = "reference_identity_mismatch"
    elif referencia["status"] != "ok":
        estado = "insufficient_evidence"
        motivo = "reference_insufficient"
    elif completas < regla.ventanas_consecutivas:
        estado = "insufficient_evidence"
        motivo = "complete_windows_below_minimum"
    elif maximo >= regla.ventanas_consecutivas:
        estado = "sustained_change"
        motivo = "consecutive_degraded_windows"
    elif incompletas:
        estado = "insufficient_evidence"
        motivo = "incomplete_windows_prevent_normality"
    else:
        estado = "no_sustained_change"
        motivo = "no_consecutive_degraded_windows"

    evidencia_suficiente = (
        referencia["status"] == "ok"
        and completas >= regla.ventanas_consecutivas
        and (incompletas == 0 or estado == "sustained_change")
    )
    normalidad = estado == "no_sustained_change"
    return {
        "rule": regla.to_dict(),
        "status": estado,
        "reason": motivo,
        "reference_status": referencia["status"],
        "reference_mean_confidence": referencia["mean_confidence"],
        "complete_windows": completas,
        "required_consecutive_windows": regla.ventanas_consecutivas,
        "max_consecutive_degraded_windows": maximo,
        "evidence_sufficient": evidencia_suficiente,
        "normality_established": normalidad,
        "insufficiency_is_not_normality": True,
        "definition": (
            "Una ventana completa es degradada si su fraccion de puntuaciones "
            "bajas alcanza low_fraction_min y su media cae al menos "
            "mean_drop_min respecto de la referencia. Hay cambio sostenido si "
            "se observan consecutive_windows ventanas completas degradadas "
            "seguidas; una ventana incompleta corta la racha."
        ),
    }



def _justificacion_umbrales(
    umbrales: UmbralesConfianza,
    justificacion: str | None,
    referencia: Mapping[str, Any],
) -> dict[str, Any]:
    es_exploratoria = (
        umbrales.inferior == UMBRAL_INFERIOR_PREDETERMINADO
        and umbrales.superior == UMBRAL_SUPERIOR_PREDETERMINADO
    )
    if es_exploratoria:
        origen = "exploratory_single_image"
        texto = JUSTIFICACION_EXPLORATORIA
        evidencia = {
            "source": "P6-02 / YOLOX assets/dog.jpg",
            "scores": list(PUNTUACIONES_DOG),
            "frames": 1,
            "robust_reference": False,
            "note": (
                "Las cinco puntuaciones provienen de una sola imagen; no "
                "alcanzan para acreditar C1/C2 ni para fijar umbrales "
                "definitivos."
            ),
        }
    else:
        origen = "operator_configured"
        texto = (
            "Umbrales configurados por el operador; este artefacto no incluye "
            "evidencia empirica que los justifique."
        )
        evidencia = {
            "source": None,
            "scores": [],
            "frames": 0,
            "robust_reference": False,
            "note": (
                "Una configuracion distinta no es comparable con la politica "
                "exploratoria 0.30/0.70; la huella de configuracion la separa."
            ),
        }
    if justificacion is not None:
        if not isinstance(justificacion, str) or not justificacion.strip():
            raise ErrorConfianza("la justificacion de umbrales no puede estar vacia")
        texto = justificacion.strip()
    observacion_referencia = None
    if referencia["status"] == "ok":
        valores = sorted(score["confidence"] for score in referencia["scores"])
        observacion_referencia = {
            "run_id": referencia["run_id"],
            "origin": referencia["origin"],
            "frames": referencia["frame_count"],
            "scored_detections": len(valores),
            "quantile_method": "floor(q * (n - 1)) on sorted scores",
            "quantiles": {
                nombre: valores[int(q * (len(valores) - 1))]
                for nombre, q in (("q25", 0.25), ("q50", 0.5), ("q75", 0.75))
            },
            "note": (
                "Cuantiles descriptivos de la referencia, no calibracion de "
                "precision ni prueba de C1/C2 sin etiquetas."
            ),
        }
    return {
        "origin": origen,
        "text": texto,
        "evidence": evidencia,
        "observed_reference": observacion_referencia,
    }


def _contrato_deteccion(filtros: Mapping[str, Any]) -> dict[str, Any]:
    piso = filtros.get("score_threshold")
    nms = filtros.get("nms")
    umbral_nms = filtros.get("nms_threshold")
    piso_observable = None
    if _es_numero(piso):
        piso_observable = float(piso) <= PISO_SALIDA_YOLOX
    coincide = (
        _es_numero(piso)
        and float(piso) == PISO_SALIDA_YOLOX
        and nms == NMS_YOLOX
        and _es_numero(umbral_nms)
        and float(umbral_nms) == UMBRAL_NMS_YOLOX
    )
    if piso_observable is None:
        advertencia = (
            "El lote no declara score_threshold; no se puede afirmar que las "
            "puntuaciones bajas sean observables."
        )
    elif not piso_observable:
        advertencia = (
            "El piso persistido es mayor que 0.10: las puntuaciones bajas "
            "pudieron descartarse antes de P6-03 y no pueden recuperarse aqui."
        )
    else:
        advertencia = None
    return {
        "source": "detecciones persistidas por P6-03",
        "score_definition": filtros.get("score"),
        "persisted_score_floor": piso,
        "documented_yolox_score_floor": PISO_SALIDA_YOLOX,
        "persisted_nms": nms,
        "documented_yolox_nms": NMS_YOLOX,
        "persisted_nms_threshold": umbral_nms,
        "documented_yolox_nms_threshold": UMBRAL_NMS_YOLOX,
        "matches_documented_yolox": coincide,
        "low_scores_observable": piso_observable,
        "warning": advertencia,
        "note": (
            "P6-04 no vuelve a ejecutar el detector ni aplica NMS. Solo "
            "clasifica las detecciones ya persistidas; el denominador son "
            "esas detecciones puntuadas, no los frames ni los eventos."
        ),
    }


def _limitaciones() -> list[str]:
    return [
        "El artefacto reutiliza las puntuaciones persistidas; no vuelve a "
        "ejecutar el detector ni recalcula inferencia.",
        "La zona accepted significa aceptacion por esta politica, no acierto, "
        "calibracion ni validacion del modelo.",
        "Los umbrales 0.30/0.70 son exploratorios y se basan en una sola "
        "imagen dog.jpg; no son una referencia robusta para C1/C2.",
        "La referencia interna usa el primer bloque de frames evaluados y no "
        "es evidencia independiente para C1/C2.",
        "Un cambio de modelo, preprocesamiento, piso de score o NMS separa "
        "series; no se comparan puntuaciones entre identidades distintas.",
        "La secuencia sintetica de P6-03 no contiene detecciones; no demuestra "
        "zonas ni cambio sostenido.",
        "La regla sostenida depende de la ventana y de los minimos "
        "configurados; resultados de configuraciones distintas no son "
        "directamente comparables.",
        "La ausencia o insuficiencia de evidencia nunca se interpreta como "
        "normalidad.",
        "P6-04 no crea eventos, alertas, capturas, portal ni metricas G1-G7; "
        "ese contrato pertenece a P6-05 y etapas posteriores.",
    ]



def _construir_politica(
    cabecera: Mapping[str, Any],
    observaciones: Sequence[Mapping[str, Any]],
    resumen: Mapping[str, Any],
    *,
    configuracion: ConfiguracionConfianza,
    referencia_cabecera: Mapping[str, Any] | None = None,
    referencia_observaciones: Sequence[Mapping[str, Any]] | None = None,
    referencia_etiqueta: str | None = None,
    entrada_etiqueta: str | None = None,
    justificacion_umbrales: str | None = None,
    generado_en: str | None = None,
) -> dict[str, Any]:
    identidad_actual = _identidad(cabecera)
    estados = resumir_estados(observaciones, configuracion.umbrales)
    frames_lote = _extraer_frames(observaciones, configuracion.umbrales)
    timeline_lote = _extraer_timeline(observaciones, configuracion.umbrales)

    externa = (
        referencia_cabecera is not None and referencia_observaciones is not None
    )
    if externa:
        origen = "file"
        timeline_referencia = _extraer_timeline(
            referencia_observaciones, configuracion.umbrales
        )
        frames_referencia = _extraer_frames(
            referencia_observaciones, configuracion.umbrales
        )
        frames_evaluacion = frames_lote
        timeline_evaluacion = timeline_lote
    else:
        origen = "first_window"
        tamano = configuracion.regla.ventana_frames
        frames_referencia = frames_lote[:tamano]
        frames_evaluacion = frames_lote[tamano:]
        vistos = 0
        corte = len(timeline_lote)
        for indice, frame in enumerate(timeline_lote):
            if frame["state"] == "scored":
                vistos += 1
            if vistos == tamano:
                corte = indice + 1
                break
        timeline_referencia = timeline_lote[:corte]
        timeline_evaluacion = timeline_lote[corte:]

    referencia_interrumpida = any(
        frame["state"] in {"technical_error", "no_score"}
        for frame in timeline_referencia
    )

    if externa:
        referencia = _crear_referencia(
            origen=origen,
            cabecera=referencia_cabecera,
            frames=frames_referencia,
            configuracion=configuracion,
            identidad_actual=identidad_actual,
            etiqueta=referencia_etiqueta,
            interrumpida=referencia_interrumpida,
        )
    else:
        referencia = _crear_referencia(
            origen=origen,
            cabecera=cabecera,
            frames=frames_referencia,
            configuracion=configuracion,
            identidad_actual=identidad_actual,
            etiqueta=None,
            interrumpida=referencia_interrumpida,
        )

    referencia_media = (
        referencia["mean_confidence"] if referencia["status"] == "ok" else None
    )
    ventanas = _crear_ventanas(
        timeline_evaluacion, configuracion.regla, referencia_media
    )
    distribucion = _distribucion(_aplanar(frames_evaluacion))
    cambio = _evaluar_cambio(ventanas, configuracion.regla, referencia)
    excluidos = len(frames_lote) - len(frames_evaluacion)

    configuracion_dict = configuracion.to_dict()
    huella_configuracion = _huella(
        {
            "schema": ESQUEMA,
            "policy_version": VERSION_POLITICA,
            "configuration": configuracion_dict,
            "documented_yolox": {
                "score_floor": PISO_SALIDA_YOLOX,
                "nms": NMS_YOLOX,
                "nms_threshold": UMBRAL_NMS_YOLOX,
            },
        }
    )
    if generado_en is None:
        generado_en = _instante()
    elif not isinstance(generado_en, str) or not generado_en.strip():
        raise ErrorConfianza("generado_en debe ser una fecha ISO no vacia")

    return {
        "schema": ESQUEMA,
        "record": "confidence_policy",
        "policy_version": VERSION_POLITICA,
        "policy_id": f"{VERSION_POLITICA}-{huella_configuracion[:16]}",
        "configuration_digest": huella_configuracion,
        "generated_at": generado_en,
        "consumer": "P6-05",
        "input": {
            "path": entrada_etiqueta,
            "run_id": cabecera["run_id"],
            "source_id": cabecera["source"]["id"],
            "p6_03_schema": cabecera["schema"],
            "records": len(observaciones),
            "summary_technical_result": resumen.get("technical_result"),
            "reference_path": referencia_etiqueta if externa else None,
        },
        "identity": identidad_actual,
        "configuration": configuracion_dict,
        "thresholds": {
            "lower": configuracion.umbrales.inferior,
            "upper": configuracion.umbrales.superior,
            "zones": configuracion.umbrales.definiciones(),
            "semantics": (
                "accepted es una decision de politica, no una afirmacion de "
                "que la deteccion sea correcta."
            ),
            "justification": _justificacion_umbrales(
                configuracion.umbrales, justificacion_umbrales, referencia
            ),
        },
        "detection_contract": _contrato_deteccion(cabecera["filters"]),
        "reference": referencia,
        "evaluation": {
            "records": estados,
            "scope": (
                "after_internal_reference" if excluidos else "all_scored_records"
            ),
            "batch_scored_frames": len(frames_lote),
            "batch_scored_detections": len(_aplanar(frames_lote)),
            "excluded_as_reference_frames": excluidos,
            "evaluated_frames": len(frames_evaluacion),
            "timeline_frames": len(timeline_evaluacion),
            "denominator": {
                "unit": "scored_detection",
                "value": distribucion["scored_detections"],
                "evaluated_frames": len(frames_evaluacion),
                "note": (
                    "Cada deteccion puntuada ya persistida cuenta una vez; "
                    "frames sin detecciones, descartados, errores y registros "
                    "sin puntuacion quedan fuera del denominador."
                ),
            },
            "zones": distribucion["zones"],
            "zone_fractions": distribucion["zone_fractions"],
            "mean_confidence": distribucion["mean_confidence"],
            "complete_windows": sum(
                1 for ventana in ventanas if ventana["complete"]
            ),
            "incomplete_windows": sum(
                1 for ventana in ventanas if not ventana["complete"]
            ),
            "windows": ventanas,
            "scores": _serializar_puntuaciones(_aplanar(frames_evaluacion)),
        },
        "sustained_change": cambio,
        "normality_established": cambio["normality_established"],
        "p6_05_contract": {
            "policy_id_source": "policy_id",
            "decision_source": "sustained_change",
            "requires": [
                "sustained_change.status",
                "sustained_change.evidence_sufficient",
                "sustained_change.normality_established",
                "evaluation.denominator",
            ],
            "must_not": [
                "reclasificar las puntuaciones",
                "crear normalidad cuando evidence_sufficient es false",
                "comparar politicas con policy_id distinto",
            ],
            "insufficiency_is_not_normality": True,
        },
        "limitations": _limitaciones(),
    }



def evaluar_lote(
    registros: object,
    *,
    configuracion: ConfiguracionConfianza | None = None,
    referencia_registros: object | None = None,
    entrada_etiqueta: str | None = None,
    referencia_etiqueta: str | None = None,
    justificacion_umbrales: str | None = None,
    generado_en: str | None = None,
) -> dict[str, Any]:
    """Construye la politica a partir de registros P6-03 ya cargados."""

    reglas = configuracion or ConfiguracionConfianza()
    if not isinstance(reglas, ConfiguracionConfianza):
        raise ErrorConfianza("configuracion debe ser ConfiguracionConfianza")
    etiqueta = entrada_etiqueta or "entrada"
    cabecera, observaciones, resumen = _validar_lote(registros, etiqueta)

    referencia_cabecera = None
    referencia_observaciones = None
    if referencia_registros is not None:
        etiqueta_referencia = referencia_etiqueta or "referencia"
        (
            referencia_cabecera,
            referencia_observaciones,
            _,
        ) = _validar_lote(referencia_registros, etiqueta_referencia)

    return _construir_politica(
        cabecera,
        observaciones,
        resumen,
        configuracion=reglas,
        referencia_cabecera=referencia_cabecera,
        referencia_observaciones=referencia_observaciones,
        referencia_etiqueta=referencia_etiqueta,
        entrada_etiqueta=entrada_etiqueta,
        justificacion_umbrales=justificacion_umbrales,
        generado_en=generado_en,
    )


def _leer_registros_archivo(ruta: Path) -> list[dict[str, Any]]:
    try:
        return leer_registros(ruta)
    except ErrorObservaciones as exc:
        raise ErrorConfianza(f"{ruta}: {exc}") from exc


def evaluar_archivo(
    entrada: str | Path,
    *,
    referencia: str | Path | None = None,
    configuracion: ConfiguracionConfianza | None = None,
    justificacion_umbrales: str | None = None,
    generado_en: str | None = None,
) -> dict[str, Any]:
    """Lee uno o dos JSONL P6-03 y devuelve la politica sin abrir la fuente."""

    ruta_entrada = Path(entrada)
    registros = _leer_registros_archivo(ruta_entrada)
    referencia_registros = None
    referencia_etiqueta = None
    if referencia is not None:
        ruta_referencia = Path(referencia)
        try:
            misma_ruta = ruta_referencia.resolve() == ruta_entrada.resolve()
        except OSError:
            misma_ruta = False
        if misma_ruta:
            raise ErrorConfianza(
                "--reference debe ser un archivo distinto de --input; "
                "omite --reference para usar la referencia interna"
            )
        referencia_registros = _leer_registros_archivo(ruta_referencia)
        referencia_etiqueta = str(referencia)
    return evaluar_lote(
        registros,
        configuracion=configuracion,
        referencia_registros=referencia_registros,
        entrada_etiqueta=str(entrada),
        referencia_etiqueta=referencia_etiqueta,
        justificacion_umbrales=justificacion_umbrales,
        generado_en=generado_en,
    )



def validar_politica(politica: object) -> dict[str, Any]:
    """Valida el sobre minimo de un artefacto P6-04 antes de consumirlo."""

    if not isinstance(politica, dict):
        raise ErrorConfianza("la politica debe ser un objeto JSON")
    if politica.get("schema") != ESQUEMA:
        raise ErrorConfianza(f"schema de politica no soportado: {politica.get('schema')!r}")
    if politica.get("record") != "confidence_policy":
        raise ErrorConfianza("record de politica no reconocido")
    if politica.get("policy_version") != VERSION_POLITICA:
        raise ErrorConfianza("policy_version no soportada")
    policy_id = politica.get("policy_id")
    if not isinstance(policy_id, str) or not policy_id.startswith(
        f"{VERSION_POLITICA}-"
    ):
        raise ErrorConfianza("policy_id ausente o invalido")

    umbrales = politica.get("thresholds")
    if not isinstance(umbrales, dict):
        raise ErrorConfianza("thresholds ausente")
    reglas = UmbralesConfianza(umbrales.get("lower"), umbrales.get("upper"))
    if umbrales.get("zones") != reglas.definiciones():
        raise ErrorConfianza("las definiciones de zonas no coinciden con los umbrales")

    cambio = politica.get("sustained_change")
    if not isinstance(cambio, dict):
        raise ErrorConfianza("sustained_change ausente")
    if cambio.get("insufficiency_is_not_normality") is not True:
        raise ErrorConfianza(
            "la politica debe declarar que la insuficiencia no es normalidad"
        )
    if politica.get("normality_established") != cambio.get(
        "normality_established"
    ):
        raise ErrorConfianza("normality_established no coincide con sustained_change")
    if cambio.get("status") not in ESTADOS_CAMBIO:
        raise ErrorConfianza("estado de cambio sostenido no reconocido")
    if cambio.get("status") in {
        "insufficient_evidence",
        "reference_incompatible",
    } and cambio.get("normality_established"):
        raise ErrorConfianza(
            "una politica sin evidencia suficiente no puede acreditar normalidad"
        )

    contrato = politica.get("p6_05_contract")
    if not isinstance(contrato, dict) or contrato.get(
        "insufficiency_is_not_normality"
    ) is not True:
        raise ErrorConfianza("contrato P6-05 ausente o incompatible")
    if not isinstance(politica.get("limitations"), list):
        raise ErrorConfianza("limitations ausente")
    return politica


def leer_politica(ruta: str | Path) -> dict[str, Any]:
    """Lee y valida un artefacto P6-04 publicado."""

    try:
        with Path(ruta).open("r", encoding="utf-8") as manejador:
            politica = json.load(manejador)
    except json.JSONDecodeError as exc:
        raise ErrorConfianza(f"{ruta}: JSON invalido: {exc}") from exc
    return validar_politica(politica)


def escribir_politica(
    politica: Mapping[str, Any], ruta: str | Path, *, overwrite: bool = False
) -> Path:
    """Escribe el JSON de forma atomica y devuelve la ruta final."""

    validada = validar_politica(dict(politica))
    destino = Path(ruta)
    if destino.exists() and destino.is_dir():
        raise IsADirectoryError(f"la salida es un directorio: {destino}")
    if destino.exists() and not overwrite:
        raise FileExistsError(f"la salida ya existe: {destino}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name(
        f".{destino.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        with temporal.open("w", encoding="utf-8", newline="\n") as manejador:
            json.dump(validada, manejador, ensure_ascii=False, indent=2, sort_keys=True)
            manejador.write("\n")
        os.replace(temporal, destino)
    except BaseException:
        try:
            temporal.unlink()
        except FileNotFoundError:
            pass
        raise
    return destino


def ruta_politica_predeterminada(run_id: str) -> Path:
    """Ruta estable dentro de datos_locales; no sobrescribe sin --overwrite."""

    if not isinstance(run_id, str) or not _PATRON_RUN_ID.fullmatch(run_id):
        raise ErrorConfianza(f"run_id inseguro para nombre de archivo: {run_id!r}")
    return RUTA_POLITICAS / f"{run_id}-p6-04.json"


def resumen_politica(politica: Mapping[str, Any]) -> dict[str, Any]:
    """Resumen compacto para consola; el artefacto completo se escribe aparte."""

    validada = validar_politica(dict(politica))
    evaluacion = validada["evaluation"]
    referencia = validada["reference"]
    cambio = validada["sustained_change"]
    return {
        "policy_id": validada["policy_id"],
        "run_id": validada["input"]["run_id"],
        "thresholds": {
            "lower": validada["thresholds"]["lower"],
            "upper": validada["thresholds"]["upper"],
        },
        "records": evaluacion["records"],
        "reference": {
            "origin": referencia["origin"],
            "status": referencia["status"],
            "scored_detections": referencia["scored_detections"],
        },
        "evaluation": {
            "scope": evaluacion["scope"],
            "denominator": evaluacion["denominator"]["value"],
            "zones": evaluacion["zones"],
            "mean_confidence": evaluacion["mean_confidence"],
        },
        "sustained_change": {
            "status": cambio["status"],
            "reason": cambio["reason"],
            "evidence_sufficient": cambio["evidence_sufficient"],
            "normality_established": cambio["normality_established"],
        },
    }
