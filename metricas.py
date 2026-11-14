"""Metricas base P6-07 (G1-G7) sobre artefactos primarios ya persistidos.

Lee JSONL de observaciones P6-03, eventos P6-05 y alertas P6-06. No ejecuta el
detector, no recalcula zonas ni puntuaciones y no crea eventos, capturas,
alertas ni portal. Cada grupo G1-G7 aporta exactamente una metrica base con
numerador, denominador explicito y valor nulo cuando el denominador es cero.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Iterable, Mapping

ESQUEMA = "p6-07/1"
VERSION = "p6-07"

GRUPOS = {
    "G1": "Salud y rendimiento del servicio",
    "G2": "Captura y procesamiento del stream",
    "G3": "Uso de recursos",
    "G4": "Comportamiento de las predicciones",
    "G5": "Datos y trazabilidad",
    "G6": "Dispositivos, fuentes y versiones",
    "G7": "Alertas, recuperacion y costos",
}


class ErrorMetricas(ValueError):
    """Los artefactos de entrada no cumplen el contrato minimo de P6-07."""


def _instante_utc(valor: datetime | None = None) -> str:
    return (valor or datetime.now(timezone.utc)).isoformat()


def _parsear_instante(valor: Any) -> datetime | None:
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        texto = valor.strip().replace("Z", "+00:00")
        instante = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    return instante.astimezone(timezone.utc)


def _sha256_archivo(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def leer_jsonl(ruta: str | Path) -> list[dict[str, Any]]:
    """Lee un JSONL UTF-8 de objetos; una linea vacia se ignora."""

    camino = Path(ruta)
    if not camino.is_file():
        raise ErrorMetricas(f"no existe el JSONL de entrada: {camino}")
    registros: list[dict[str, Any]] = []
    try:
        with camino.open("r", encoding="utf-8") as archivo:
            for numero, linea in enumerate(archivo, start=1):
                if not linea.strip():
                    continue
                try:
                    registro = json.loads(linea)
                except json.JSONDecodeError as error:
                    raise ErrorMetricas(
                        f"{camino}: linea {numero} no es JSON valido: {error.msg}"
                    ) from error
                if not isinstance(registro, dict):
                    raise ErrorMetricas(
                        f"{camino}: linea {numero} debe ser un objeto JSON"
                    )
                registros.append(registro)
    except OSError as error:
        raise ErrorMetricas(f"no se pudo leer {camino}: {error}") from error
    return registros


def _resumen_observaciones(
    registros: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    """Conteos de P6-03; usa el resumen persistido o los deriva por registro."""

    filas = list(registros)
    resumen = next(
        (fila for fila in reversed(filas) if fila.get("record") == "resumen"), None
    )
    if resumen is not None and isinstance(resumen.get("counts"), Mapping):
        counts = resumen["counts"]
        return {
            "frames_received": int(counts.get("frames_received", 0) or 0),
            "frames_sampled": int(counts.get("frames_sampled", 0) or 0),
            "frames_processed": int(counts.get("frames_processed", 0) or 0),
            "frames_discarded": int(counts.get("frames_discarded", 0) or 0),
            "detector_errors": int(counts.get("detector_errors", 0) or 0),
            "detector_calls": int(counts.get("detector_calls", 0) or 0),
        }
    observaciones = [fila for fila in filas if fila.get("record") == "observacion"]
    return {
        "frames_received": len(observaciones),
        "frames_sampled": sum(
            1 for fila in observaciones if fila.get("source_status") == "sampled"
        ),
        "frames_processed": sum(
            1 for fila in observaciones if fila.get("status") == "processed"
        ),
        "frames_discarded": sum(
            1 for fila in observaciones if fila.get("status") == "discarded"
        ),
        "detector_errors": sum(
            1 for fila in observaciones if fila.get("status") == "detector_error"
        ),
        "detector_calls": sum(
            1
            for fila in observaciones
            if fila.get("status") in {"processed", "detector_error"}
        ),
    }


def _metrica(
    *,
    grupo: str,
    nombre: str,
    unidad: str,
    formula: str,
    poblacion: str,
    ventana: str,
    exclusiones: str,
    ausencias: list[str],
    fuente: str,
    numerador: int | float | None,
    denominador: int | float | None,
    valor: float | None,
    evidencia: Mapping[str, Any],
    timestamp: str,
    estado: str | None = None,
) -> dict[str, Any]:
    if estado is None:
        if denominador is None or denominador == 0:
            estado = "sin_datos"
            valor = None
            if not ausencias:
                ausencias = ["denominador cero: no se calcula una proporcion"]
        else:
            estado = "ok"
    return {
        "id": grupo,
        "grupo": GRUPOS[grupo],
        "nombre": nombre,
        "unidad": unidad,
        "formula": formula,
        "poblacion": poblacion,
        "ventana": ventana,
        "exclusiones": exclusiones,
        "ausencias": list(ausencias),
        "fuente": fuente,
        "numerador": numerador,
        "denominador": denominador,
        "denominador_explicito": True,
        "zero_safe": True,
        "division_por_cero": "valor_nulo_y_estado_sin_datos",
        "valor": valor,
        "estado": estado,
        "evidencia": dict(evidencia),
        "timestamp": timestamp,
    }


def _ratio(numerador: int | float, denominador: int | float) -> float | None:
    if denominador == 0:
        return None
    return round(float(numerador) / float(denominador), 6)


def _es_texto(valor: Any) -> bool:
    return isinstance(valor, str) and bool(valor.strip())


def _evento_trazable(evento: Mapping[str, Any]) -> bool:
    capture = evento.get("capture")
    return (
        _es_texto(evento.get("event_id"))
        and _es_texto(evento.get("frame_id"))
        and _es_texto(evento.get("observation_id"))
        and _es_texto(evento.get("occurred_at"))
        and isinstance(capture, Mapping)
        and _es_texto(capture.get("path"))
        and _es_texto(capture.get("sha256"))
    )


def _entrada(ruta: Path, registros: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ruta": str(ruta),
        "sha256": _sha256_archivo(ruta),
        "registros": len(registros),
    }



def calcular_metricas(
    *,
    ruta_observaciones: str | Path,
    ruta_eventos: str | Path,
    ruta_alertas: str | Path,
    root: str | Path = ".",
    generated_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Calcula una metrica base por grupo G1-G7 sin inventar datos."""

    camino_obs = Path(ruta_observaciones)
    camino_evt = Path(ruta_eventos)
    camino_ale = Path(ruta_alertas)
    raiz = Path(root)

    filas_obs = leer_jsonl(camino_obs)
    filas_evt = leer_jsonl(camino_evt)
    filas_ale = leer_jsonl(camino_ale)

    if isinstance(generated_at, str):
        instante = _parsear_instante(generated_at)
        if instante is None:
            raise ErrorMetricas(
                f"generated_at no es un instante ISO 8601: {generated_at}"
            )
    else:
        instante = generated_at or datetime.now(timezone.utc)
        if instante.tzinfo is None:
            instante = instante.replace(tzinfo=timezone.utc)
        instante = instante.astimezone(timezone.utc)
    timestamp = _instante_utc(instante)

    observaciones = [f for f in filas_obs if f.get("record") == "observacion"]
    eventos = [f for f in filas_evt if f.get("record") == "event"]
    alertas = [f for f in filas_ale if f.get("record") == "alert"]
    counts = _resumen_observaciones(filas_obs)

    run_ids = {
        str(fila["run_id"])
        for fila in filas_obs + filas_evt + filas_ale
        if _es_texto(fila.get("run_id"))
    }
    run_id = next(
        (
            str(fila["run_id"])
            for fila in filas_obs
            if _es_texto(fila.get("run_id"))
        ),
        sorted(run_ids)[0] if run_ids else None,
    )

    metricas: dict[str, dict[str, Any]] = {}

    # G1: fallos tecnicos del detector entre cuadros muestreados elegibles.
    g1_den = counts["frames_sampled"]
    g1_num = counts["detector_errors"]
    metricas["G1"] = _metrica(
        grupo="G1",
        nombre="Tasa de errores tecnicos de inferencia",
        unidad="ratio (errores / cuadros muestreados)",
        formula="detector_errors / frames_sampled",
        poblacion="Cuadros muestreados que llegan al detector en la corrida.",
        ventana="Corrida completa del resumen P6-03.",
        exclusiones=(
            "Cuadros descartados por muestreo y errores de lectura no entran al "
            "numerador ni al denominador."
        ),
        ausencias=(
            ["No hay resumen P6-03 ni cuadros muestreados persistidos."]
            if g1_den == 0
            else []
        ),
        fuente="observaciones",
        numerador=g1_num,
        denominador=g1_den,
        valor=_ratio(g1_num, g1_den),
        evidencia={
            "frames_sampled": g1_den,
            "detector_errors": g1_num,
            "detector_calls": counts["detector_calls"],
        },
        timestamp=timestamp,
    )

    # G2: descartes de la aplicacion entre cuadros recibidos.
    g2_den = counts["frames_received"]
    g2_num = counts["frames_discarded"]
    metricas["G2"] = _metrica(
        grupo="G2",
        nombre="Tasa de cuadros descartados por la aplicacion",
        unidad="ratio (descartes / cuadros recibidos)",
        formula="frames_discarded / frames_received",
        poblacion="Todos los cuadros recibidos por la fuente en la corrida.",
        ventana="Corrida completa del resumen P6-03.",
        exclusiones=(
            "Los descartes se cuentan una vez; los errores de lectura se reportan "
            "aparte y no se mezclan con el muestreo."
        ),
        ausencias=(
            ["No hay cuadros recibidos persistidos; la tasa no se calcula."]
            if g2_den == 0
            else []
        ),
        fuente="observaciones",
        numerador=g2_num,
        denominador=g2_den,
        valor=_ratio(g2_num, g2_den),
        evidencia={
            "frames_received": g2_den,
            "frames_discarded": g2_num,
            "frames_processed": counts["frames_processed"],
        },
        timestamp=timestamp,
    )


    # G3: recurso actual del sistema de archivos que contiene root.
    g3_ausencias: list[str] = []
    try:
        uso = shutil.disk_usage(str(raiz))
        g3_num, g3_den = int(uso.free), int(uso.total)
        g3_valor = _ratio(g3_num, g3_den)
        g3_estado = None
        g3_evidencia = {
            "path": str(raiz.resolve()),
            "free_bytes": g3_num,
            "total_bytes": g3_den,
            "used_bytes": int(uso.used),
        }
    except OSError as error:
        g3_num = g3_den = None
        g3_valor = None
        g3_estado = "sin_datos"
        g3_ausencias = [f"shutil.disk_usage no disponible: {error}"]
        g3_evidencia = {"path": str(raiz)}
    metricas["G3"] = _metrica(
        grupo="G3",
        nombre="Proporcion de disco disponible",
        unidad="ratio (bytes libres / bytes totales)",
        formula="free_bytes / total_bytes",
        poblacion="Sistema de archivos que contiene --root en el instante de calculo.",
        ventana="Instante de generated_at; es una medida instantanea.",
        exclusiones=(
            "No mide CPU ni RAM; solo el disco accesible por el proceso. "
            "No es una prediccion de agotamiento."
        ),
        ausencias=g3_ausencias,
        fuente="sistema de archivos de --root",
        numerador=g3_num,
        denominador=g3_den,
        valor=g3_valor,
        evidencia=g3_evidencia,
        timestamp=timestamp,
        estado=g3_estado,
    )

    # G4: baja confianza entre detecciones puntuadas persistidas en eventos.
    zonas = Counter(
        str(zona.get("zone"))
        for evento in eventos
        for zona in (evento.get("zones") or [])
        if isinstance(zona, Mapping) and _es_texto(zona.get("zone"))
    )
    g4_den = sum(zonas.values())
    g4_num = zonas.get("low", 0)
    metricas["G4"] = _metrica(
        grupo="G4",
        nombre="Proporcion de detecciones en zona low",
        unidad="ratio (detecciones low / detecciones puntuadas)",
        formula="detecciones_low / detecciones_puntuadas",
        poblacion="Detecciones puntuadas dentro de events[].zones de P6-05.",
        ventana="Eventos persistidos de la corrida; no se unen corridas.",
        exclusiones=(
            "Eventos no puntuados, sin detecciones o con error tecnico no aportan "
            "zonas. No se reclasifican puntuaciones de P6-04."
        ),
        ausencias=(
            ["No hay detecciones puntuadas con zona; la proporcion no se calcula."]
            if g4_den == 0
            else []
        ),
        fuente="eventos",
        numerador=g4_num,
        denominador=g4_den,
        valor=_ratio(g4_num, g4_den),
        evidencia={
            "zonas": dict(sorted(zonas.items())),
            "eventos": len(eventos),
            "detecciones_total": sum(
                len(evento.get("detections") or []) for evento in eventos
            ),
        },
        timestamp=timestamp,
    )


    # G5: cobertura de campos y referencias verificables en eventos.
    g5_den = len(eventos)
    invalidos = [
        str(evento.get("event_id") or f"indice-{indice}")
        for indice, evento in enumerate(eventos)
        if not _evento_trazable(evento)
    ]
    g5_num = g5_den - len(invalidos)
    metricas["G5"] = _metrica(
        grupo="G5",
        nombre="Cobertura de trazabilidad de eventos",
        unidad="ratio (eventos trazables / eventos persistidos)",
        formula="eventos_con_referencias_completas / eventos_persistidos",
        poblacion=(
            "Eventos P6-05 con event_id, frame_id, observation_id, occurred_at "
            "y capture.path/capture.sha256."
        ),
        ventana="Corrida completa; no se verifican los bytes de cada captura aqui.",
        exclusiones=(
            "Las referencias se validan como campos no vacios; la verificacion "
            "criptografica de archivos corresponde a verificar_evidencia_alertas."
        ),
        ausencias=(
            ["No hay eventos persistidos; la cobertura no se calcula."]
            if g5_den == 0
            else []
        ),
        fuente="eventos",
        numerador=g5_num,
        denominador=g5_den,
        valor=_ratio(g5_num, g5_den),
        evidencia={
            "eventos": g5_den,
            "eventos_trazables": g5_num,
            "eventos_incompletos": len(invalidos),
            "ids_incompletos": invalidos[:20],
        },
        timestamp=timestamp,
    )

    # G6: antiguedad del reporte de observacion mas reciente.
    instantes = [
        instante_obs
        for instante_obs in (
            _parsear_instante(fila.get("received_at")) for fila in observaciones
        )
        if instante_obs is not None
    ]
    if instantes:
        ultimo = max(instantes)
        g6_num = round(max(0.0, (instante - ultimo).total_seconds()), 3)
        g6_den = 1
        g6_valor = g6_num
        g6_ausencias: list[str] = []
        g6_estado = None
        g6_evidencia = {
            "ultimo_received_at": ultimo.isoformat(),
            "observaciones_con_timestamp": len(instantes),
        }
    else:
        g6_num = g6_den = None
        g6_valor = None
        g6_ausencias = [
            "No hay observaciones con received_at parseable; la antiguedad no se calcula."
        ]
        g6_estado = "sin_datos"
        g6_evidencia = {"observaciones_con_timestamp": 0}
    metricas["G6"] = _metrica(
        grupo="G6",
        nombre="Antiguedad del ultimo reporte de observacion",
        unidad="segundos",
        formula="generated_at - max(received_at); denominador unitario = 1",
        poblacion="Observaciones P6-03 de la corrida con received_at ISO 8601.",
        ventana="Desde el ultimo reporte hasta generated_at.",
        exclusiones=(
            "No se estima la frecuencia ni la salud de la fuente. Un solo "
            "reporte no demuestra continuidad."
        ),
        ausencias=g6_ausencias,
        fuente="observaciones",
        numerador=g6_num,
        denominador=g6_den,
        valor=g6_valor,
        evidencia=g6_evidencia,
        timestamp=timestamp,
        estado=g6_estado,
    )


    # G7: alertas disparadas entre alertas persistidas. No modela ciclo de vida.
    severidades = Counter(str(alerta.get("severity")) for alerta in alertas)
    reglas = Counter(str(alerta.get("rule_id")) for alerta in alertas)
    g7_den = len(alertas)
    g7_num = sum(
        1 for alerta in alertas if alerta.get("occurrence_state") == "triggered"
    )
    metricas["G7"] = _metrica(
        grupo="G7",
        nombre="Proporcion de alertas con estado triggered",
        unidad="ratio (alertas triggered / alertas persistidas)",
        formula="alertas_occurrence_state_triggered / alertas_persistidas",
        poblacion="Registros record=alert del JSONL P6-06; excluye el manifiesto.",
        ventana="Corrida completa de alertas persistidas.",
        exclusiones=(
            "occurrence_state no es un ciclo de vida activa/resuelta. No existen "
            "reconocimiento, recuperacion ni costos instrumentados."
        ),
        ausencias=(
            ["No hay alertas persistidas; la proporcion no se calcula."]
            if g7_den == 0
            else []
        ),
        fuente="alertas",
        numerador=g7_num,
        denominador=g7_den,
        valor=_ratio(g7_num, g7_den),
        evidencia={
            "alertas": g7_den,
            "occurrence_state": dict(
                sorted(
                    Counter(
                        str(alerta.get("occurrence_state")) for alerta in alertas
                    ).items()
                )
            ),
            "por_severidad": dict(sorted(severidades.items())),
            "por_regla": dict(sorted(reglas.items())),
        },
        timestamp=timestamp,
    )

    limitaciones = [
        "Las metricas describen artefactos persistidos; no miden exactitud ni "
        "calidad predictiva y no sustituyen C1/C2.",
        "Un denominador cero produce valor nulo y estado sin_datos; no equivale "
        "a normalidad ni a exito.",
        "G3 es una medida instantanea del disco accesible por --root.",
        "G6 solo usa timestamps de observaciones de una corrida; no prueba "
        "continuidad de la fuente.",
        "G7 usa occurrence_state como proxy auditable porque P6-06 no modela "
        "alertas activas/resueltas ni reconocimiento.",
        "Los umbrales de confianza de P6-04 siguen siendo exploratorios.",
    ]
    if len(run_ids) > 1:
        limitaciones.append(
            "Los tres artefactos contienen run_id distintos: "
            + ", ".join(sorted(run_ids))
            + "."
        )

    return {
        "schema": ESQUEMA,
        "version": VERSION,
        "timestamp": timestamp,
        "generated_at": timestamp,
        "run_id": run_id,
        "run_ids": sorted(run_ids),
        "inputs": {
            "observaciones": _entrada(camino_obs, filas_obs),
            "eventos": _entrada(camino_evt, filas_evt),
            "alertas": _entrada(camino_ale, filas_ale),
            "root": str(raiz.resolve()),
        },
        "metric_ids": list(GRUPOS),
        "metricas": metricas,
        "limitaciones": limitaciones,
    }


def metricas_por_grupo(reporte: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Acceso validado a las siete metricas de un reporte P6-07."""

    metricas = reporte.get("metricas")
    if not isinstance(metricas, Mapping):
        raise ErrorMetricas("el reporte no contiene el objeto metricas")
    faltantes = [grupo for grupo in GRUPOS if grupo not in metricas]
    if faltantes:
        raise ErrorMetricas(f"faltan metricas: {', '.join(faltantes)}")
    return {grupo: metricas[grupo] for grupo in GRUPOS}

