"""Eventos P6-05 persistidos y capturas JPEG vinculadas a observaciones P6-03.

El manifiesto JSONL es la interfaz de lectura para el futuro portal. Las zonas
se copian de P6-04; este modulo nunca recalcula ni modifica una confianza.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any, Mapping

from observaciones import leer_registros, validar_registros
from politica_confianza import clasificar_frame, leer_politica

SCHEMA = "p6-05/1"
ZONAS = {"low", "doubt", "accepted"}
ORDEN_ZONAS = {"low": 0, "doubt": 1, "accepted": 2}
PATRON_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ErrorEventos(ValueError):
    """Inconsistencia entre observaciones, politica, eventos o capturas."""


def _json(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _identidad_observaciones(cabecera: Mapping[str, Any]) -> dict[str, Any]:
    return {"model": cabecera["model"], "filters": cabecera["filters"]}


def _puntuaciones_politica(
    politica: Mapping[str, Any], observaciones: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Exige que cada zona de P6-04 corresponda a una deteccion original."""

    por_id = {r["observation_id"]: r for r in observaciones}
    agrupadas: dict[str, list[dict[str, Any]]] = defaultdict(list)
    vistos: set[tuple[str, int]] = set()
    scores_politica = politica["evaluation"]["scores"]
    if len(scores_politica) != politica["evaluation"]["denominator"]["value"]:
        raise ErrorEventos("denominador de politica no coincide con sus puntuaciones")
    for puntuacion in scores_politica:
        oid = puntuacion["observation_id"]
        indice = puntuacion["detection_index"]
        registro = por_id.get(oid)
        if registro is None or registro["status"] != "processed":
            raise ErrorEventos(f"puntuacion sin observacion procesada: {oid}")
        detecciones = registro["detections"]
        if not isinstance(indice, int) or isinstance(indice, bool) or not 0 <= indice < len(detecciones):
            raise ErrorEventos(f"indice de deteccion invalido: {oid}:{indice}")
        original = detecciones[indice]
        if (
            puntuacion["class_name"] != original["class_name"]
            or puntuacion["confidence"] != original["confidence"]
            or puntuacion["zone"] not in ZONAS
        ):
            raise ErrorEventos(f"zona o puntuacion no coincide con P6-03: {oid}:{indice}")
        clave = (oid, indice)
        if clave in vistos:
            raise ErrorEventos(f"deteccion duplicada en politica: {oid}:{indice}")
        vistos.add(clave)
        agrupadas[oid].append(dict(puntuacion))
    for oid, scores in agrupadas.items():
        if len(scores) != len(por_id[oid]["detections"]):
            raise ErrorEventos(f"faltan zonas de detecciones: {oid}")
    if politica["input"]["reference_path"] is not None:
        for registro in observaciones:
            if clasificar_frame(registro) == "scored" and registro["observation_id"] not in agrupadas:
                raise ErrorEventos(f"faltan zonas del cuadro: {registro['observation_id']}")
    return agrupadas


def _evento(
    registro: Mapping[str, Any],
    cabecera: Mapping[str, Any],
    politica: Mapping[str, Any],
    zonas: list[dict[str, Any]],
    *,
    observaciones_ref: str,
    politica_ref: str,
    captura: dict[str, str] | None,
) -> dict[str, Any]:
    estado = clasificar_frame(registro)
    detecciones = registro.get("detections") or []
    principal = max(
        detecciones,
        key=lambda d: -1.0 if d["confidence"] is None else d["confidence"],
    ) if detecciones else None
    zona = min((p["zone"] for p in zonas), key=ORDEN_ZONAS.get) if zonas else None
    cambio = politica["sustained_change"]
    if not cambio["evidence_sufficient"] and cambio["normality_established"]:
        raise ErrorEventos("evidencia insuficiente no puede declarar normalidad")
    return {
        "schema": SCHEMA,
        "record": "event",
        "event_id": sha256(registro["observation_id"].encode("utf-8")).hexdigest()[:32],
        "run_id": registro["run_id"],
        "observation_id": registro["observation_id"],
        "frame_id": registro["frame_id"],
        "frame_number": registro["frame_number"],
        "occurred_at": registro["received_at"] or registro["finished_at"],
        "source": cabecera["source"],
        "model": cabecera["model"],
        "filters": cabecera["filters"],
        "source_config": cabecera["config"],
        "status": estado,
        "technical_result": registro["technical_result"],
        "reason": registro["reason"],
        "error": registro["error"],
        "prediction": {
            "class_name": principal["class_name"],
            "confidence": principal["confidence"],
        } if principal else None,
        "detections": detecciones if estado in {"scored", "no_score"} else [],
        "zones": sorted(zonas, key=lambda p: p["detection_index"]),
        "zone": zona,
        "policy_id": politica["policy_id"],
        "policy_status": cambio["status"],
        "policy_denominator": politica["evaluation"]["denominator"]["value"],
        "evidence_sufficient": cambio["evidence_sufficient"],
        "normality_established": cambio["normality_established"],
        "policy_config": politica["configuration"],
        "observation_ref": observaciones_ref,
        "policy_ref": politica_ref,
        "capture": captura,
    }


def persistir_eventos(
    observaciones_path: str | Path,
    politica_path: str | Path,
    imagenes: Mapping[str, Path],
    *,
    root: str | Path = Path("datos_locales"),
) -> Path:
    """Publica eventos de una corrida y sus imagenes muestreadas sin expiracion."""

    observaciones_path = Path(observaciones_path)
    politica_path = Path(politica_path)
    cabecera, observaciones, _ = validar_registros(leer_registros(observaciones_path))
    politica = leer_politica(politica_path)
    run_id = cabecera["run_id"]
    if politica["input"]["run_id"] != run_id:
        raise ErrorEventos("la politica pertenece a otra corrida")
    if politica["identity"]["model"] != cabecera["model"] or politica["identity"]["filters"] != cabecera["filters"]:
        raise ErrorEventos("modelo o filtros distintos de los usados por la politica")
    zonas_por_id = _puntuaciones_politica(politica, observaciones)
    root = Path(root)
    destino = root / "eventos" / f"{run_id}.jsonl"
    if destino.exists():
        raise FileExistsError(f"ya existen eventos de esta corrida: {destino}")
    carpeta_imagenes = root / "capturas" / sha256(run_id.encode("utf-8")).hexdigest()[:16]
    if carpeta_imagenes.exists():
        raise FileExistsError(f"ya existen capturas de esta corrida: {carpeta_imagenes}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    carpeta_imagenes.mkdir(parents=True, exist_ok=False)
    temporal = destino.with_name(f".{destino.name}.{secrets.token_hex(4)}.tmp")
    try:
        with temporal.open("w", encoding="utf-8", newline="\n") as archivo:
            for registro in observaciones:
                if registro["status"] in {"discarded", "end_of_stream"}:
                    continue
                oid = registro["observation_id"]
                imagen = imagenes.get(oid)
                if registro["status"] in {"processed", "detector_error"} and registro["source_status"] == "sampled" and imagen is None:
                    raise ErrorEventos(f"falta la captura del cuadro inferido: {oid}")
                captura = None
                if imagen is not None:
                    if registro["source_status"] != "sampled" or not imagen.is_file():
                        raise ErrorEventos(f"captura invalida: {oid}")
                    relativo = Path("capturas") / carpeta_imagenes.name / f"{registro['sequence']:06d}.jpg"
                    bytes_imagen = imagen.read_bytes()
                    if not bytes_imagen.startswith(b"\xff\xd8"):
                        raise ErrorEventos(f"captura no es JPEG: {oid}")
                    (root / relativo).write_bytes(bytes_imagen)
                    captura = {"path": relativo.as_posix(), "sha256": sha256(bytes_imagen).hexdigest()}
                evento = _evento(
                    registro, cabecera, politica, zonas_por_id.get(oid, []),
                    observaciones_ref=str(observaciones_path),
                    politica_ref=str(politica_path), captura=captura,
                )
                archivo.write(_json(evento) + "\n")
        os.replace(temporal, destino)
    except Exception:
        temporal.unlink(missing_ok=True)
        # Solo se elimina la carpeta nueva, nunca datos de una corrida anterior.
        for archivo in carpeta_imagenes.iterdir():
            archivo.unlink()
        carpeta_imagenes.rmdir()
        raise
    return destino


def consultar_eventos(root: str | Path, run_id: str) -> list[dict[str, Any]]:
    """Lee eventos persistidos tras reiniciar; no requiere modelo ni stream."""

    if not isinstance(run_id, str) or PATRON_RUN_ID.fullmatch(run_id) is None:
        raise ErrorEventos("run_id invalido")
    ruta = Path(root) / "eventos" / f"{run_id}.jsonl"
    eventos: list[dict[str, Any]] = []
    with ruta.open("r", encoding="utf-8") as archivo:
        for linea in archivo:
            evento = json.loads(linea)
            if evento.get("schema") != SCHEMA or evento.get("run_id") != run_id:
                raise ErrorEventos(f"manifiesto de eventos invalido: {ruta}")
            eventos.append(evento)
    return eventos


def abrir_captura(root: str | Path, evento: Mapping[str, Any]) -> bytes:
    """Resuelve el vinculo y comprueba el hash antes de entregar la imagen."""

    captura = evento.get("capture")
    if not isinstance(captura, dict):
        raise ErrorEventos("el evento no tiene captura")
    base = Path(root).resolve()
    ruta = (base / captura["path"]).resolve()
    if not ruta.is_relative_to((base / "capturas").resolve()):
        raise ErrorEventos("ruta de captura fuera de la carpeta autorizada")
    datos = ruta.read_bytes()
    if sha256(datos).hexdigest() != captura["sha256"]:
        raise ErrorEventos("hash de captura no coincide")
    return datos
