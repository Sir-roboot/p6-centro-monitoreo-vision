"""Alertas P6-06 derivadas de eventos P6-05 y de la politica P6-04.

Este modulo solo lee artefactos ya persistidos: eventos P6-05 y la politica
P6-04 que esos eventos referencian. No ejecuta inferencia, no aplica NMS, no
reclasifica puntuaciones, no modifica eventos ni capturas y no convierte la
falta de evidencia en normalidad.

Las reglas son explicitas y su configuracion se conserva en el manifiesto y en
cada alerta. La alerta de baja confianza tiene su propia condicion y no depende
del cambio sostenido de P6-04; eso permite acreditar C2 sin inventar drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any, Mapping, Sequence

from eventos import ErrorEventos, abrir_captura, consultar_eventos
from politica_confianza import ErrorConfianza, leer_politica

SCHEMA = "p6-06/1"
VERSION_ALERTAS = "p6-06"
REGISTRO_MANIFIESTO = "alert_manifest"
REGISTRO_ALERTA = "alert"

PATRON_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PATRON_ALERT_ID = re.compile(r"^p6-06-[0-9a-f]{32}$")

SEVERIDADES = ("info", "warning", "critical")
ZONAS = {"low", "doubt", "accepted"}
ESTADOS_EVENTO = {"scored", "no_detections", "technical_error", "no_score"}

RULE_BAJA_CONFIANZA = "p6-06-low-confidence"
RULE_CAMBIO_SOSTENIDO = "p6-06-sustained-change"
RULE_EVIDENCIA_INSUFICIENTE = "p6-06-insufficient-evidence"


class ErrorAlertas(ValueError):
    """Eventos, politica, regla o manifiesto que no permiten alertar con rigor."""


def _instante() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_canonico(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _huella(valor: Any) -> str:
    return sha256(_json_canonico(valor).encode("utf-8")).hexdigest()


def _validar_run_id(run_id: object) -> str:
    if not isinstance(run_id, str) or PATRON_RUN_ID.fullmatch(run_id) is None:
        raise ErrorAlertas(f"run_id inseguro o invalido: {run_id!r}")
    return run_id


def _entero_positivo(nombre: str, valor: object) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 1:
        raise ErrorAlertas(f"{nombre} debe ser un entero >= 1")
    return valor


@dataclass(frozen=True)
class ConfiguracionAlertas:
    """Parametros declarados de las reglas; se copian a cada alerta."""

    low_min_detections: int = 1
    low_window_events: int = 1
    low_min_events_with_low: int = 1

    def __post_init__(self) -> None:
        _entero_positivo("low_min_detections", self.low_min_detections)
        _entero_positivo("low_window_events", self.low_window_events)
        _entero_positivo("low_min_events_with_low", self.low_min_events_with_low)
        if self.low_min_events_with_low > self.low_window_events:
            raise ErrorAlertas(
                "low_min_events_with_low no puede superar low_window_events"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA,
            "low_confidence": {
                "zone": "low",
                "min_low_detections_per_event": self.low_min_detections,
                "window_events": self.low_window_events,
                "min_events_with_low_in_window": self.low_min_events_with_low,
                "depends_on_sustained_change": False,
                "unscored_events_excluded": True,
            },
            "sustained_change": {
                "policy_status": "sustained_change",
                "evidence_sufficient": True,
                "window_source": "evaluation.windows",
            },
            "insufficient_evidence": {
                "policy_statuses": [
                    "insufficient_evidence",
                    "reference_incompatible",
                ],
                "also_when_evidence_sufficient_is_false": True,
                "severity": "info",
                "insufficiency_is_not_normality": True,
            },
        }


@dataclass(frozen=True)
class ReglaAlerta:
    """Regla explicita con condicion, ventana, severidad y accion sugerida."""

    rule_id: str
    version: str
    description: str
    condition: Mapping[str, Any]
    window: Mapping[str, Any]
    severity: str
    insufficient_data_handling: str
    suggested_action: str

    def __post_init__(self) -> None:
        for nombre in (
            "rule_id",
            "version",
            "description",
            "insufficient_data_handling",
            "suggested_action",
        ):
            valor = getattr(self, nombre)
            if not isinstance(valor, str) or not valor.strip():
                raise ErrorAlertas(f"{nombre} de la regla debe ser texto no vacio")
        if self.severity not in SEVERIDADES:
            raise ErrorAlertas(f"severidad de regla invalida: {self.severity!r}")
        if not isinstance(self.condition, Mapping):
            raise ErrorAlertas("condition de la regla debe ser un objeto")
        if not isinstance(self.window, Mapping):
            raise ErrorAlertas("window de la regla debe ser un objeto")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "description": self.description,
            "condition": dict(self.condition),
            "window": dict(self.window),
            "severity": self.severity,
            "insufficient_data_handling": self.insufficient_data_handling,
            "suggested_action": self.suggested_action,
        }


def _reglas_por_id(reglas: Sequence[ReglaAlerta]) -> dict[str, ReglaAlerta]:
    return {regla.rule_id: regla for regla in reglas}


def _regla_desde_dict(datos: Mapping[str, Any]) -> ReglaAlerta:
    try:
        return ReglaAlerta(
            rule_id=datos["rule_id"],
            version=datos["version"],
            description=datos["description"],
            condition=datos["condition"],
            window=datos["window"],
            severity=datos["severity"],
            insufficient_data_handling=datos["insufficient_data_handling"],
            suggested_action=datos["suggested_action"],
        )
    except (KeyError, TypeError) as exc:
        raise ErrorAlertas(f"regla declarada incompleta: {exc}") from exc


def reglas_alertas(
    configuracion: ConfiguracionAlertas | None = None,
) -> list[ReglaAlerta]:
    """Devuelve las tres reglas declaradas de P6-06."""

    cfg = configuracion or ConfiguracionAlertas()
    if not isinstance(cfg, ConfiguracionAlertas):
        raise ErrorAlertas("configuracion debe ser ConfiguracionAlertas")
    return [
        ReglaAlerta(
            rule_id=RULE_BAJA_CONFIANZA,
            version=SCHEMA,
            description=(
                "Alerta cuando uno o mas eventos puntuados contienen detecciones "
                "en la zona low de la politica P6-04."
            ),
            condition={
                "event_status": "scored",
                "zone": "low",
                "min_low_detections_per_event": cfg.low_min_detections,
                "min_events_with_low_in_window": cfg.low_min_events_with_low,
                "depends_on_sustained_change": False,
            },
            window={
                "kind": "non_overlapping_event_windows",
                "window_events": cfg.low_window_events,
                "origin": "persisted_p6_05_events",
            },
            severity="warning",
            insufficient_data_handling=(
                "Los eventos no puntuados (no_detections, technical_error, "
                "no_score) no cuentan como baja confianza ni como normalidad. "
                "Sin eventos puntuados la regla no dispara; esa ausencia se "
                "reporta aparte con la regla de evidencia insuficiente."
            ),
            suggested_action=(
                "Revisar la captura y el evento vinculados; confirmar si la "
                "escena o la iluminacion explican la baja confianza. Recolectar "
                "evidencia adicional y no asumir normalidad ni drift."
            ),
        ),
        ReglaAlerta(
            rule_id=RULE_CAMBIO_SOSTENIDO,
            version=SCHEMA,
            description=(
                "Alerta cuando la politica P6-04 declara cambio sostenido con "
                "evidencia suficiente. No recalcula ni reclasifica puntuaciones."
            ),
            condition={
                "policy_status": "sustained_change",
                "evidence_sufficient": True,
                "policy_schema": "p6-04/1",
                "depends_on_sustained_change": True,
            },
            window={
                "kind": "policy_windows",
                "origin": "evaluation.windows",
                "selection": (
                    "primer bloque consecutivo de ventanas completas degradadas "
                    "que alcanza sustained_change.rule.consecutive_windows"
                ),
            },
            severity="critical",
            insufficient_data_handling=(
                "Si la politica no aporta evidencia suficiente no se emite esta "
                "alerta como si la escena fuera normal; se emite la alerta "
                "informativa de evidencia insuficiente."
            ),
            suggested_action=(
                "Revisar las capturas de las ventanas vinculadas y la referencia "
                "usada; repetir el muestreo. La politica solo detecta cambio "
                "sostenido de confianza, no demuestra drift ni calidad."
            ),
        ),
        ReglaAlerta(
            rule_id=RULE_EVIDENCIA_INSUFICIENTE,
            version=SCHEMA,
            description=(
                "Alerta informativa cuando la politica no puede acreditar "
                "normalidad por evidencia insuficiente o referencia incompatible."
            ),
            condition={
                "any_of": [
                    {"policy_status": "insufficient_evidence"},
                    {"policy_status": "reference_incompatible"},
                    {"evidence_sufficient": False},
                ],
                "depends_on_sustained_change": False,
            },
            window={
                "kind": "policy",
                "origin": "evaluation.windows",
                "event_linkage": "observation_id de los frames de las ventanas",
            },
            severity="info",
            insufficient_data_handling=(
                "La insuficiencia se registra como alerta informativa de calidad "
                "de evidencia. No se convierte en normalidad ni en una alerta de "
                "escena."
            ),
            suggested_action=(
                "Recolectar mas eventos o ventanas y corregir la referencia si es "
                "incompatible. La falta de evidencia no es normalidad; no cerrar "
                "la revision como escena normal."
            ),
        ),
    ]


def _validar_eventos(
    eventos: Sequence[Mapping[str, Any]], run_id: str
) -> list[Mapping[str, Any]]:
    if not isinstance(eventos, list) or not eventos:
        raise ErrorAlertas(f"la corrida {run_id} no tiene eventos persistidos")
    vistos: set[str] = set()
    policy_ids: set[str] = set()
    for posicion, evento in enumerate(eventos, start=1):
        if not isinstance(evento, Mapping):
            raise ErrorAlertas(f"evento {posicion}: se esperaba un objeto JSON")
        if evento.get("schema") != "p6-05/1":
            raise ErrorAlertas(f"evento {posicion}: schema P6-05 no soportado")
        if evento.get("record") != "event":
            raise ErrorAlertas(f"evento {posicion}: record no reconocido")
        if evento.get("run_id") != run_id:
            raise ErrorAlertas(f"evento {posicion}: run_id distinto de {run_id}")
        event_id = evento.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ErrorAlertas(f"evento {posicion}: event_id invalido")
        if event_id in vistos:
            raise ErrorAlertas(f"evento duplicado en el manifiesto: {event_id}")
        vistos.add(event_id)
        estado = evento.get("status")
        if estado not in ESTADOS_EVENTO:
            raise ErrorAlertas(f"evento {event_id}: status no reconocido: {estado!r}")
        for campo in (
            "observation_id",
            "frame_id",
            "frame_number",
            "occurred_at",
            "policy_id",
            "policy_ref",
            "policy_status",
            "policy_config",
            "policy_denominator",
            "evidence_sufficient",
            "normality_established",
            "source",
            "model",
            "filters",
            "capture",
            "zones",
            "zone",
            "prediction",
            "detections",
        ):
            if campo not in evento:
                raise ErrorAlertas(f"evento {event_id}: falta el campo {campo}")
        if not isinstance(evento["observation_id"], str) or not evento["observation_id"]:
            raise ErrorAlertas(f"evento {event_id}: observation_id invalido")
        if not isinstance(evento["occurred_at"], str) or not evento["occurred_at"]:
            raise ErrorAlertas(f"evento {event_id}: occurred_at invalido")
        if evento["evidence_sufficient"] is not True and (
            evento["normality_established"] is not False
        ):
            raise ErrorAlertas(
                f"evento {event_id}: evidencia insuficiente no puede declarar normalidad"
            )
        zonas = evento["zones"]
        if not isinstance(zonas, list):
            raise ErrorAlertas(f"evento {event_id}: zones debe ser una lista")
        detecciones = evento["detections"]
        if not isinstance(detecciones, list):
            raise ErrorAlertas(f"evento {event_id}: detections debe ser una lista")
        for zona in zonas:
            if not isinstance(zona, Mapping):
                raise ErrorAlertas(f"evento {event_id}: zona invalida")
            if zona.get("zone") not in ZONAS:
                raise ErrorAlertas(f"evento {event_id}: zona desconocida")
            indice = zona.get("detection_index")
            if isinstance(indice, bool) or not isinstance(indice, int):
                raise ErrorAlertas(f"evento {event_id}: detection_index invalido")
            if estado == "scored":
                if not 0 <= indice < len(detecciones):
                    raise ErrorAlertas(
                        f"evento {event_id}: detection_index fuera de rango"
                    )
                original = detecciones[indice]
                if (
                    zona.get("class_name") != original.get("class_name")
                    or zona.get("confidence") != original.get("confidence")
                ):
                    raise ErrorAlertas(
                        f"evento {event_id}: la zona no coincide con la deteccion original"
                    )
        policy_ids.add(evento["policy_id"])
    if len(policy_ids) != 1:
        raise ErrorAlertas("los eventos referencian policy_id distintos")
    return list(eventos)


def _candidatos_politica(
    root: Path,
    run_id: str,
    eventos: Sequence[Mapping[str, Any]],
    policy_path: str | Path | None,
) -> list[Path]:
    candidatos: list[Path] = []
    if policy_path is not None:
        candidatos.append(Path(policy_path))
    referencias = {str(evento["policy_ref"]) for evento in eventos}
    if len(referencias) > 1:
        raise ErrorAlertas("los eventos referencian rutas de politica distintas")
    referencia = Path(next(iter(referencias)))
    if referencia not in candidatos:
        candidatos.append(referencia)
        if not referencia.is_absolute():
            candidatos.append(Path.cwd() / referencia)
    predeterminada = root / "politicas" / f"{run_id}-p6-04.json"
    if predeterminada not in candidatos:
        candidatos.append(predeterminada)
    return candidatos


def _cargar_politica(
    root: Path,
    run_id: str,
    eventos: Sequence[Mapping[str, Any]],
    policy_path: str | Path | None,
) -> tuple[Path, dict[str, Any]]:
    candidatos = _candidatos_politica(root, run_id, eventos, policy_path)
    for candidato in candidatos:
        if not candidato.is_file():
            continue
        try:
            politica = leer_politica(candidato)
        except ErrorConfianza as exc:
            raise ErrorAlertas(f"politica invalida {candidato}: {exc}") from exc
        return candidato, politica
    rutas = ", ".join(str(candidato) for candidato in candidatos)
    raise ErrorAlertas(
        f"no se encontro una politica P6-04 legible; rutas probadas: {rutas}"
    )


def _validar_politica_contra_eventos(
    politica: Mapping[str, Any],
    eventos: Sequence[Mapping[str, Any]],
    run_id: str,
) -> None:
    try:
        if politica["input"]["run_id"] != run_id:
            raise ErrorAlertas("la politica pertenece a otra corrida")
        policy_id = politica["policy_id"]
        cambio = politica["sustained_change"]
        denominador = politica["evaluation"]["denominator"]["value"]
        ventanas = politica["evaluation"]["windows"]
        regla = cambio["rule"]
        if not isinstance(ventanas, list):
            raise ErrorAlertas("evaluation.windows debe ser una lista")
        if not isinstance(regla, Mapping):
            raise ErrorAlertas("sustained_change.rule debe ser un objeto")
        consecutivas = regla["consecutive_windows"]
        if isinstance(consecutivas, bool) or not isinstance(consecutivas, int) or consecutivas < 2:
            raise ErrorAlertas("sustained_change.rule.consecutive_windows invalido")
        for indice, ventana in enumerate(ventanas, start=1):
            if not isinstance(ventana, Mapping):
                raise ErrorAlertas(f"ventana {indice}: se esperaba un objeto")
            if not isinstance(ventana.get("frames"), list):
                raise ErrorAlertas(f"ventana {indice}: frames debe ser una lista")
            for frame in ventana["frames"]:
                if not isinstance(frame, Mapping) or not isinstance(
                    frame.get("observation_id"), str
                ):
                    raise ErrorAlertas(
                        f"ventana {indice}: frame sin observation_id valido"
                    )
        for evento in eventos:
            if evento["policy_id"] != policy_id:
                raise ErrorAlertas(f"evento {evento['event_id']}: policy_id distinto")
            if evento["policy_config"] != politica["configuration"]:
                raise ErrorAlertas(f"evento {evento['event_id']}: configuracion distinta")
            if evento["policy_status"] != cambio["status"]:
                raise ErrorAlertas(f"evento {evento['event_id']}: policy_status distinto")
            if evento["policy_denominator"] != denominador:
                raise ErrorAlertas(f"evento {evento['event_id']}: denominador distinto")
            if evento["evidence_sufficient"] != cambio["evidence_sufficient"]:
                raise ErrorAlertas(f"evento {evento['event_id']}: evidencia distinta")
            if evento["normality_established"] != cambio["normality_established"]:
                raise ErrorAlertas(f"evento {evento['event_id']}: normalidad distinta")
            if evento["model"] != politica["identity"]["model"]:
                raise ErrorAlertas(f"evento {evento['event_id']}: modelo distinto")
            if evento["filters"] != politica["identity"]["filters"]:
                raise ErrorAlertas(f"evento {evento['event_id']}: filtros distintos")
    except ErrorAlertas:
        raise
    except (KeyError, TypeError, IndexError) as exc:
        raise ErrorAlertas(f"politica P6-04 incompleta o incompatible: {exc}") from exc


def _ref_evento(
    evento: Mapping[str, Any],
    *,
    bajas: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    referencia = {
        "event_id": evento["event_id"],
        "observation_id": evento["observation_id"],
        "frame_id": evento["frame_id"],
        "frame_number": evento["frame_number"],
        "occurred_at": evento["occurred_at"],
        "status": evento["status"],
        "zone": evento["zone"],
        "prediction": evento["prediction"],
        "policy_ref": evento["policy_ref"],
        "capture": evento["capture"],
    }
    if bajas is not None:
        referencia["low_detections"] = [dict(zona) for zona in bajas]
    return referencia


def _eventos_por_observacion(
    eventos: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    indice: dict[str, Mapping[str, Any]] = {}
    for evento in eventos:
        observation_id = evento["observation_id"]
        if observation_id in indice:
            raise ErrorAlertas(f"observation_id duplicado: {observation_id}")
        indice[observation_id] = evento
    return indice


def _ventanas(politica: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    ventanas = politica["evaluation"].get("windows")
    if not isinstance(ventanas, list):
        raise ErrorAlertas("la politica no contiene evaluation.windows")
    return ventanas


def _resumen_ventana(ventana: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "index": ventana["index"],
        "complete": ventana["complete"],
        "degraded": ventana["degraded"],
        "degradation_reasons": ventana["degradation_reasons"],
        "frame_count": ventana["frame_count"],
        "scored_detections": ventana["scored_detections"],
        "zones": ventana["zones"],
        "zone_fractions": ventana["zone_fractions"],
        "mean_confidence": ventana["mean_confidence"],
        "mean_drop_vs_reference": ventana["mean_drop_vs_reference"],
    }


def _eventos_de_ventanas(
    indices: Sequence[int],
    *,
    politica: Mapping[str, Any],
    por_observacion: Mapping[str, Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
    por_indice = {ventana["index"]: ventana for ventana in _ventanas(politica)}
    eventos: list[Mapping[str, Any]] = []
    resumenes: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for indice in indices:
        ventana = por_indice.get(indice)
        if ventana is None:
            raise ErrorAlertas(f"ventana de politica inexistente: {indice}")
        resumenes.append(_resumen_ventana(ventana))
        for frame in ventana["frames"]:
            observation_id = frame["observation_id"]
            evento = por_observacion.get(observation_id)
            if evento is None:
                raise ErrorAlertas(
                    f"la ventana {indice} referencia un frame sin evento P6-05: "
                    f"{observation_id}"
                )
            if evento["event_id"] not in vistos:
                vistos.add(evento["event_id"])
                eventos.append(evento)
    return eventos, resumenes


def _ventana_desde_eventos(
    eventos: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    policy_windows: Sequence[Mapping[str, Any]] | None = None,
    policy_window_indices: Sequence[int] | None = None,
    refs_bajas: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    if not eventos:
        return {
            "kind": kind,
            "start_event_id": None,
            "end_event_id": None,
            "start_frame_number": None,
            "end_frame_number": None,
            "event_ids": [],
            "observation_ids": [],
            "event_refs": [],
            "policy_window_indices": list(policy_window_indices or []),
            "policy_windows": list(policy_windows or []),
        }
    refs = [
        _ref_evento(
            evento,
            bajas=refs_bajas.get(evento["event_id"]) if refs_bajas else None,
        )
        for evento in eventos
    ]
    return {
        "kind": kind,
        "start_event_id": eventos[0]["event_id"],
        "end_event_id": eventos[-1]["event_id"],
        "start_frame_number": eventos[0]["frame_number"],
        "end_frame_number": eventos[-1]["frame_number"],
        "event_ids": [evento["event_id"] for evento in eventos],
        "observation_ids": [evento["observation_id"] for evento in eventos],
        "event_refs": refs,
        "policy_window_indices": list(policy_window_indices or []),
        "policy_windows": list(policy_windows or []),
    }


def _base_alerta(
    *,
    regla: ReglaAlerta,
    configuracion: ConfiguracionAlertas,
    run_id: str,
    politica: Mapping[str, Any],
    occurred_at: str | None,
    primary_event_id: str | None,
    window: Mapping[str, Any],
    reason: str,
    evidence: Mapping[str, Any],
    policy_ref: str,
    events_ref: str,
    generated_at: str,
) -> dict[str, Any]:
    window_key = {
        "kind": window["kind"],
        "event_ids": list(window["event_ids"]),
        "policy_window_indices": list(window.get("policy_window_indices", [])),
    }
    dedupe_key = _json_canonico(
        {"rule_id": regla.rule_id, "run_id": run_id, "window": window_key}
    )
    cambio = politica["sustained_change"]
    return {
        "schema": SCHEMA,
        "record": REGISTRO_ALERTA,
        "alert_id": f"p6-06-{_huella({'dedupe_key': dedupe_key})[:32]}",
        "dedupe_key": dedupe_key,
        "run_id": run_id,
        "rule_id": regla.rule_id,
        "rule_version": regla.version,
        "rule": regla.to_dict(),
        "rule_config": configuracion.to_dict(),
        "severity": regla.severity,
        "reason": reason,
        "suggested_action": regla.suggested_action,
        "occurred_at": occurred_at,
        "generated_at": generated_at,
        "source": None,
        "model": None,
        "filters": None,
        "policy_id": politica["policy_id"],
        "policy_status": cambio["status"],
        "policy_reason": cambio["reason"],
        "evidence_sufficient": cambio["evidence_sufficient"],
        "normality_established": cambio["normality_established"],
        "policy_ref": policy_ref,
        "events_ref": events_ref,
        "event_id": primary_event_id,
        "event_ids": list(window["event_ids"]),
        "observation_ids": list(window["observation_ids"]),
        "window": dict(window),
        "evidence": dict(evidence),
        "policy_config": dict(politica["configuration"]),
    }


def _alertas_baja_confianza(
    eventos: Sequence[Mapping[str, Any]],
    *,
    regla: ReglaAlerta,
    configuracion: ConfiguracionAlertas,
    politica: Mapping[str, Any],
    policy_ref: str,
    events_ref: str,
    generated_at: str,
) -> list[dict[str, Any]]:
    alertas: list[dict[str, Any]] = []
    tamano = configuracion.low_window_events
    for inicio in range(0, len(eventos), tamano):
        bloque = list(eventos[inicio : inicio + tamano])
        bajas_por_evento: dict[str, list[Mapping[str, Any]]] = {}
        for evento in bloque:
            if evento["status"] != "scored":
                continue
            bajas = [zona for zona in evento["zones"] if zona["zone"] == "low"]
            if len(bajas) >= configuracion.low_min_detections:
                bajas_por_evento[evento["event_id"]] = bajas
        if len(bajas_por_evento) < configuracion.low_min_events_with_low:
            continue
        calificados = [
            evento for evento in bloque if evento["event_id"] in bajas_por_evento
        ]
        total_bajas = sum(len(bajas) for bajas in bajas_por_evento.values())
        confianzas = [
            zona["confidence"]
            for bajas in bajas_por_evento.values()
            for zona in bajas
        ]
        minimo = min(confianzas)
        maximo = max(confianzas)
        estado_politica = politica["sustained_change"]["status"]
        if tamano == 1:
            evento = calificados[0]
            motivo = (
                f"{len(bajas_por_evento[evento['event_id']])} deteccion(es) en zona "
                f"low del evento {evento['event_id']} (frame "
                f"{evento['frame_number']}); confianza low entre {minimo:.6f} y "
                f"{maximo:.6f}. La regla no depende de sustained_change "
                f"(policy_status={estado_politica})."
            )
        else:
            motivo = (
                f"{len(calificados)} de {len(bloque)} eventos conservados contienen "
                f"al menos {configuracion.low_min_detections} deteccion(es) en zona "
                f"low; {total_bajas} detecciones low en total, confianza entre "
                f"{minimo:.6f} y {maximo:.6f}. La regla no depende de "
                f"sustained_change (policy_status={estado_politica})."
            )
        ventana = _ventana_desde_eventos(
            bloque,
            kind="event_window",
            refs_bajas=bajas_por_evento,
        )
        evidencia = {
            "low_detections": [
                {**dict(zona), "event_id": evento["event_id"]}
                for evento in calificados
                for zona in bajas_por_evento[evento["event_id"]]
            ],
            "triggering_event_ids": [evento["event_id"] for evento in calificados],
            "scored_events_in_window": sum(
                1 for evento in bloque if evento["status"] == "scored"
            ),
            "unscored_events_in_window": [
                evento["event_id"]
                for evento in bloque
                if evento["status"] != "scored"
            ],
            "depends_on_sustained_change": False,
        }
        alerta = _base_alerta(
            regla=regla,
            configuracion=configuracion,
            run_id=politica["input"]["run_id"],
            politica=politica,
            occurred_at=calificados[0]["occurred_at"],
            primary_event_id=calificados[0]["event_id"],
            window=ventana,
            reason=motivo,
            evidence=evidencia,
            policy_ref=policy_ref,
            events_ref=events_ref,
            generated_at=generated_at,
        )
        alerta["source"] = calificados[0]["source"]
        alerta["model"] = calificados[0]["model"]
        alerta["filters"] = calificados[0]["filters"]
        alertas.append(alerta)
    return alertas


def _indices_cambio_sostenido(politica: Mapping[str, Any]) -> list[int]:
    cambio = politica["sustained_change"]
    requeridas = cambio.get("rule", {}).get("consecutive_windows")
    if isinstance(requeridas, bool) or not isinstance(requeridas, int) or requeridas < 2:
        raise ErrorAlertas("la politica no declara consecutive_windows valido")
    ventanas = _ventanas(politica)
    for inicio in range(len(ventanas) - requeridas + 1):
        bloque = ventanas[inicio : inicio + requeridas]
        if all(ventana["complete"] and ventana["degraded"] for ventana in bloque):
            return [ventana["index"] for ventana in bloque]
    raise ErrorAlertas(
        "la politica declaro sustained_change sin ventanas completas degradadas "
        "consecutivas que lo respalden"
    )


def _alerta_cambio_sostenido(
    *,
    regla: ReglaAlerta,
    configuracion: ConfiguracionAlertas,
    politica: Mapping[str, Any],
    por_observacion: Mapping[str, Mapping[str, Any]],
    policy_ref: str,
    events_ref: str,
    generated_at: str,
) -> list[dict[str, Any]]:
    cambio = politica["sustained_change"]
    if cambio["status"] != "sustained_change":
        return []
    if cambio["evidence_sufficient"] is not True:
        raise ErrorAlertas(
            "la politica declara cambio sostenido sin evidencia suficiente"
        )
    indices = _indices_cambio_sostenido(politica)
    vinculados, resumenes = _eventos_de_ventanas(
        indices, politica=politica, por_observacion=por_observacion
    )
    if not vinculados:
        raise ErrorAlertas("las ventanas de cambio sostenido no tienen eventos")
    ventana = _ventana_desde_eventos(
        vinculados,
        kind="policy_windows",
        policy_windows=resumenes,
        policy_window_indices=indices,
    )
    motivo = (
        f"La politica {politica['policy_id']} declaro sustained_change "
        f"(motivo={cambio['reason']}); ventanas completas degradadas consecutivas "
        f"{indices} de {len(_ventanas(politica))} evaluadas. La decision usa "
        f"evidencia suficiente y conserva {len(vinculados)} eventos de origen."
    )
    evidencia = {
        "policy_status": cambio["status"],
        "policy_reason": cambio["reason"],
        "required_consecutive_windows": cambio["rule"]["consecutive_windows"],
        "max_consecutive_degraded_windows": cambio[
            "max_consecutive_degraded_windows"
        ],
        "complete_windows": cambio["complete_windows"],
        "origin_window_indices": indices,
        "depends_on_sustained_change": True,
    }
    alerta = _base_alerta(
        regla=regla,
        configuracion=configuracion,
        run_id=politica["input"]["run_id"],
        politica=politica,
        occurred_at=vinculados[0]["occurred_at"],
        primary_event_id=vinculados[0]["event_id"],
        window=ventana,
        reason=motivo,
        evidence=evidencia,
        policy_ref=policy_ref,
        events_ref=events_ref,
        generated_at=generated_at,
    )
    alerta["source"] = vinculados[0]["source"]
    alerta["model"] = vinculados[0]["model"]
    alerta["filters"] = vinculados[0]["filters"]
    return [alerta]


def _alerta_evidencia_insuficiente(
    eventos: Sequence[Mapping[str, Any]],
    *,
    regla: ReglaAlerta,
    configuracion: ConfiguracionAlertas,
    politica: Mapping[str, Any],
    por_observacion: Mapping[str, Mapping[str, Any]],
    policy_ref: str,
    events_ref: str,
    generated_at: str,
) -> list[dict[str, Any]]:
    cambio = politica["sustained_change"]
    if cambio["evidence_sufficient"] is True:
        return []
    indices = [ventana["index"] for ventana in _ventanas(politica)]
    vinculados, resumenes = _eventos_de_ventanas(
        indices, politica=politica, por_observacion=por_observacion
    )
    ventana = _ventana_desde_eventos(
        vinculados,
        kind="policy",
        policy_windows=resumenes,
        policy_window_indices=indices,
    )
    motivo = (
        f"La politica {politica['policy_id']} no acredita normalidad: "
        f"status={cambio['status']}, motivo={cambio['reason']}, "
        f"evidence_sufficient=false, complete_windows={cambio['complete_windows']}, "
        f"incomplete_windows={len(_ventanas(politica)) - cambio['complete_windows']}. "
        "La insuficiencia se registra como alerta informativa, no como normalidad."
    )
    evidencia = {
        "policy_status": cambio["status"],
        "policy_reason": cambio["reason"],
        "reference_status": cambio["reference_status"],
        "complete_windows": cambio["complete_windows"],
        "evaluated_windows": len(_ventanas(politica)),
        "insufficiency_is_not_normality": True,
        "depends_on_sustained_change": False,
    }
    origen = vinculados[0] if vinculados else eventos[0]
    alerta = _base_alerta(
        regla=regla,
        configuracion=configuracion,
        run_id=politica["input"]["run_id"],
        politica=politica,
        occurred_at=origen["occurred_at"],
        primary_event_id=origen["event_id"],
        window=ventana,
        reason=motivo,
        evidence=evidencia,
        policy_ref=policy_ref,
        events_ref=events_ref,
        generated_at=generated_at,
    )
    alerta["source"] = origen["source"]
    alerta["model"] = origen["model"]
    alerta["filters"] = origen["filters"]
    return [alerta]


def _limitaciones() -> list[str]:
    return [
        "P6-06 no incluye portal ni interfaz: R5.3 queda pendiente hasta P6-07/P6-08.",
        "Las alertas se derivan de artefactos locales P6-05/P6-04; un clon limpio no puede reabrir esos datos.",
        "Una alerta de baja confianza no demuestra drift, fallo del detector ni calidad predictiva.",
        "Insuficiencia de evidencia no es normalidad; la alerta informativa no cierra la revision.",
        "No hay envio de correo, reconocimiento, resolucion automatica ni expiracion automatica.",
        "Los umbrales de P6-04 siguen siendo exploratorios hasta completar C1/C2 con escenas reales.",
    ]


def _contrato_portal() -> dict[str, Any]:
    return {
        "consumer": ["P6-07", "P6-08"],
        "query": {
            "manifiesto": "leer_manifiesto_alertas(root, run_id)",
            "alertas": "consultar_alertas(root, run_id)",
            "verificacion": "verificar_evidencia_alertas(root, run_id)",
            "corridas": "listar_runs_con_alertas(root)",
        },
        "required_display_fields": [
            "occurred_at",
            "rule_id",
            "severity",
            "reason",
            "suggested_action",
            "event_id",
            "event_ids",
            "window",
        ],
        "links": {
            "events": "event_ids y window.event_refs[].event_id",
            "captures": "window.event_refs[].capture (compatible con abrir_captura)",
            "policy": "policy_ref y policy_id",
        },
        "persistence": {
            "automatic_expiration": False,
            "ttl": None,
            "storage": "datos_locales/alertas/<run_id>.jsonl",
        },
        "must_not": [
            "recalcular scores o zonas de P6-04/P6-05",
            "tratar evidencia insuficiente como normalidad",
            "borrar o expirar alertas automaticamente",
        ],
        "r5_3": (
            "Pendiente de la vista de alertas del portal. Los campos de este "
            "contrato son la entrada esperada; P6-06 solo persiste y consulta."
        ),
    }


def _validar_alerta(
    alerta: object, run_id: str, reglas: Mapping[str, ReglaAlerta]
) -> dict[str, Any]:
    if not isinstance(alerta, dict):
        raise ErrorAlertas("cada alerta debe ser un objeto JSON")
    if alerta.get("schema") != SCHEMA or alerta.get("record") != REGISTRO_ALERTA:
        raise ErrorAlertas("schema o record de alerta invalido")
    if alerta.get("run_id") != run_id:
        raise ErrorAlertas("alerta con run_id distinto")
    alert_id = alerta.get("alert_id")
    if not isinstance(alert_id, str) or PATRON_ALERT_ID.fullmatch(alert_id) is None:
        raise ErrorAlertas("alert_id invalido")
    rule_id = alerta.get("rule_id")
    if not isinstance(rule_id, str) or rule_id not in reglas:
        raise ErrorAlertas(f"rule_id no declarado: {rule_id!r}")
    regla = reglas[rule_id]
    if alerta.get("severity") != regla.severity:
        raise ErrorAlertas(f"severidad distinta de la regla {rule_id}")
    if alerta.get("rule") != regla.to_dict():
        raise ErrorAlertas(f"metadatos de regla alterados: {rule_id}")
    if not isinstance(alerta.get("reason"), str) or not alerta["reason"]:
        raise ErrorAlertas("alerta sin motivo")
    if not isinstance(alerta.get("suggested_action"), str) or not alerta["suggested_action"]:
        raise ErrorAlertas("alerta sin accion sugerida")
    if not isinstance(alerta.get("dedupe_key"), str) or not alerta["dedupe_key"]:
        raise ErrorAlertas("alerta sin dedupe_key")
    event_ids = alerta.get("event_ids")
    if not isinstance(event_ids, list) or not all(
        isinstance(event_id, str) and event_id for event_id in event_ids
    ):
        raise ErrorAlertas("event_ids invalido")
    ventana = alerta.get("window")
    if not isinstance(ventana, dict):
        raise ErrorAlertas("alerta sin ventana")
    if ventana.get("event_ids") != event_ids:
        raise ErrorAlertas("event_ids no coincide con la ventana")
    refs = ventana.get("event_refs")
    if not isinstance(refs, list) or not all(isinstance(ref, Mapping) for ref in refs):
        raise ErrorAlertas("ventana sin event_refs validos")
    if [ref.get("event_id") for ref in refs] != event_ids:
        raise ErrorAlertas("event_refs no coincide con event_ids")
    indices = ventana.get("policy_window_indices", [])
    if not isinstance(indices, list) or any(
        isinstance(indice, bool) or not isinstance(indice, int) or indice < 1
        for indice in indices
    ):
        raise ErrorAlertas("policy_window_indices invalido")
    if event_ids and alerta.get("event_id") not in event_ids:
        raise ErrorAlertas("event_id principal fuera de la ventana")
    clave = _json_canonico(
        {
            "rule_id": rule_id,
            "run_id": run_id,
            "window": {
                "kind": ventana.get("kind"),
                "event_ids": event_ids,
                "policy_window_indices": list(
                    ventana.get("policy_window_indices", [])
                ),
            },
        }
    )
    if clave != alerta["dedupe_key"]:
        raise ErrorAlertas("dedupe_key no coincide con la regla y la ventana")
    if alerta["alert_id"] != f"p6-06-{_huella({'dedupe_key': clave})[:32]}":
        raise ErrorAlertas("alert_id no coincide con dedupe_key")
    if rule_id == RULE_EVIDENCIA_INSUFICIENTE:
        if alerta.get("evidence_sufficient") is not False:
            raise ErrorAlertas(
                "la alerta de insuficiencia debe declarar evidencia insuficiente"
            )
        if alerta.get("normality_established") is not False:
            raise ErrorAlertas("la insuficiencia no puede declarar normalidad")
    if rule_id == RULE_CAMBIO_SOSTENIDO:
        if alerta.get("policy_status") != "sustained_change":
            raise ErrorAlertas(
                "la alerta de cambio sostenido no coincide con la politica"
            )
        if not ventana.get("policy_window_indices"):
            raise ErrorAlertas("la alerta de cambio sostenido no vincula ventanas")
    if rule_id == RULE_BAJA_CONFIANZA:
        condicion = alerta["rule"]["condition"]
        if condicion.get("depends_on_sustained_change") is not False:
            raise ErrorAlertas(
                "la regla de baja confianza no debe depender del cambio sostenido"
            )
    return alerta


def _validar_alertas(
    alertas: Sequence[Mapping[str, Any]], run_id: str, reglas: Sequence[ReglaAlerta]
) -> list[dict[str, Any]]:
    mapa = _reglas_por_id(reglas)
    ids: set[str] = set()
    claves: set[str] = set()
    validadas: list[dict[str, Any]] = []
    for alerta in alertas:
        if not isinstance(alerta, Mapping):
            raise ErrorAlertas("cada alerta debe ser un objeto JSON")
        validada = _validar_alerta(dict(alerta), run_id, mapa)
        if validada["alert_id"] in ids:
            raise ErrorAlertas(f"alert_id duplicado: {validada['alert_id']}")
        if validada["dedupe_key"] in claves:
            raise ErrorAlertas(
                f"alerta duplicada para la misma regla y ventana: "
                f"{validada['dedupe_key']}"
            )
        ids.add(validada["alert_id"])
        claves.add(validada["dedupe_key"])
        validadas.append(validada)
    return validadas


def _resumen_alertas(
    alertas: Sequence[Mapping[str, Any]], reglas: Sequence[ReglaAlerta]
) -> tuple[dict[str, int], dict[str, int]]:
    por_regla = {regla.rule_id: 0 for regla in reglas}
    por_severidad = {severidad: 0 for severidad in SEVERIDADES}
    for alerta in alertas:
        por_regla[alerta["rule_id"]] += 1
        por_severidad[alerta["severity"]] += 1
    return por_regla, por_severidad


def _manifiesto(
    *,
    run_id: str,
    politica: Mapping[str, Any],
    policy_ref: str,
    events_ref: str,
    eventos: Sequence[Mapping[str, Any]],
    alertas: Sequence[Mapping[str, Any]],
    reglas: Sequence[ReglaAlerta],
    configuracion: ConfiguracionAlertas,
    generated_at: str,
) -> dict[str, Any]:
    por_regla, por_severidad = _resumen_alertas(alertas, reglas)
    puntuados = sum(1 for evento in eventos if evento["status"] == "scored")
    return {
        "schema": SCHEMA,
        "record": REGISTRO_MANIFIESTO,
        "alert_version": VERSION_ALERTAS,
        "run_id": run_id,
        "generated_at": generated_at,
        "policy_id": politica["policy_id"],
        "policy_status": politica["sustained_change"]["status"],
        "policy_reason": politica["sustained_change"]["reason"],
        "policy_ref": policy_ref,
        "events_ref": events_ref,
        "event_count": len(eventos),
        "scored_event_count": puntuados,
        "unscored_event_count": len(eventos) - puntuados,
        "alert_count": len(alertas),
        "counts_by_rule": por_regla,
        "counts_by_severity": por_severidad,
        "rules": [regla.to_dict() for regla in reglas],
        "configuration": configuracion.to_dict(),
        "alerts_digest": _huella(list(alertas)),
        "insufficiency_is_not_normality": True,
        "persistence": {
            "automatic_expiration": False,
            "ttl": None,
            "format": "jsonl",
        },
        "portal_contract": _contrato_portal(),
        "limitations": _limitaciones(),
    }


def _validar_manifiesto(
    manifiesto: object, alertas: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    if not isinstance(manifiesto, dict):
        raise ErrorAlertas("el manifiesto debe ser un objeto JSON")
    if (
        manifiesto.get("schema") != SCHEMA
        or manifiesto.get("record") != REGISTRO_MANIFIESTO
    ):
        raise ErrorAlertas("schema o record de manifiesto invalido")
    run_id = _validar_run_id(manifiesto.get("run_id"))
    reglas_brutas = manifiesto.get("rules")
    if not isinstance(reglas_brutas, list) or not reglas_brutas:
        raise ErrorAlertas("manifiesto sin reglas declaradas")
    reglas = [_regla_desde_dict(regla) for regla in reglas_brutas if isinstance(regla, dict)]
    if len(reglas) != len(reglas_brutas):
        raise ErrorAlertas("reglas declaradas invalidas")
    if len({regla.rule_id for regla in reglas}) != len(reglas):
        raise ErrorAlertas("rule_id declarado mas de una vez")
    validadas = _validar_alertas(alertas, run_id, reglas)
    configuracion = manifiesto.get("configuration")
    if not isinstance(configuracion, dict):
        raise ErrorAlertas("manifiesto sin configuracion")
    for alerta in validadas:
        if alerta.get("rule_config") != configuracion:
            raise ErrorAlertas(
                f"alerta {alerta['alert_id']}: rule_config distinto del manifiesto"
            )
    por_regla, por_severidad = _resumen_alertas(validadas, reglas)
    if manifiesto.get("alert_count") != len(validadas):
        raise ErrorAlertas("alert_count no coincide con las alertas")
    if manifiesto.get("counts_by_rule") != por_regla:
        raise ErrorAlertas("counts_by_rule no coincide con las alertas")
    if manifiesto.get("counts_by_severity") != por_severidad:
        raise ErrorAlertas("counts_by_severity no coincide con las alertas")
    if manifiesto.get("alerts_digest") != _huella(list(validadas)):
        raise ErrorAlertas("alerts_digest no coincide con las alertas")
    if manifiesto.get("insufficiency_is_not_normality") is not True:
        raise ErrorAlertas(
            "el manifiesto debe declarar que la insuficiencia no es normalidad"
        )
    for campo in (
        "event_count",
        "scored_event_count",
        "unscored_event_count",
        "alert_count",
    ):
        valor = manifiesto.get(campo)
        if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
            raise ErrorAlertas(f"{campo} invalido")
    if (
        manifiesto["scored_event_count"] + manifiesto["unscored_event_count"]
        != manifiesto["event_count"]
    ):
        raise ErrorAlertas("los conteos de eventos no suman event_count")
    persistencia = manifiesto.get("persistence")
    if not isinstance(persistencia, dict) or persistencia.get(
        "automatic_expiration"
    ) is not False:
        raise ErrorAlertas("el manifiesto debe declarar persistencia sin expiracion")
    if not isinstance(manifiesto.get("portal_contract"), dict):
        raise ErrorAlertas("manifiesto sin contrato para el portal")
    if not isinstance(manifiesto.get("limitations"), list):
        raise ErrorAlertas("manifiesto sin limitaciones")
    return manifiesto


def construir_alertas(
    root: str | Path,
    run_id: str,
    *,
    policy_path: str | Path | None = None,
    configuracion: ConfiguracionAlertas | None = None,
    generado_en: str | None = None,
) -> dict[str, Any]:
    """Genera alertas en memoria desde P6-05/P6-04 ya persistidos."""

    run_id = _validar_run_id(run_id)
    root = Path(root)
    cfg = configuracion or ConfiguracionAlertas()
    if not isinstance(cfg, ConfiguracionAlertas):
        raise ErrorAlertas("configuracion debe ser ConfiguracionAlertas")
    try:
        eventos_brutos = consultar_eventos(root, run_id)
    except (ErrorEventos, OSError, json.JSONDecodeError) as exc:
        raise ErrorAlertas(f"eventos P6-05 invalidos: {exc}") from exc
    eventos = _validar_eventos(eventos_brutos, run_id)
    politica_path, politica = _cargar_politica(root, run_id, eventos, policy_path)
    _validar_politica_contra_eventos(politica, eventos, run_id)
    por_observacion = _eventos_por_observacion(eventos)
    reglas = reglas_alertas(cfg)
    mapa_reglas = _reglas_por_id(reglas)
    if generado_en is None:
        generado = _instante()
    elif not isinstance(generado_en, str) or not generado_en.strip():
        raise ErrorAlertas("generado_en debe ser un instante ISO no vacio")
    else:
        generado = generado_en
    referencias = {str(evento["policy_ref"]) for evento in eventos}
    policy_ref = next(iter(referencias)) if len(referencias) == 1 else str(politica_path)
    events_ref = str(root / "eventos" / f"{run_id}.jsonl")
    alertas: list[dict[str, Any]] = []
    alertas.extend(
        _alertas_baja_confianza(
            eventos,
            regla=mapa_reglas[RULE_BAJA_CONFIANZA],
            configuracion=cfg,
            politica=politica,
            policy_ref=policy_ref,
            events_ref=events_ref,
            generated_at=generado,
        )
    )
    alertas.extend(
        _alerta_cambio_sostenido(
            regla=mapa_reglas[RULE_CAMBIO_SOSTENIDO],
            configuracion=cfg,
            politica=politica,
            por_observacion=por_observacion,
            policy_ref=policy_ref,
            events_ref=events_ref,
            generated_at=generado,
        )
    )
    alertas.extend(
        _alerta_evidencia_insuficiente(
            eventos,
            regla=mapa_reglas[RULE_EVIDENCIA_INSUFICIENTE],
            configuracion=cfg,
            politica=politica,
            por_observacion=por_observacion,
            policy_ref=policy_ref,
            events_ref=events_ref,
            generated_at=generado,
        )
    )
    validadas = _validar_alertas(alertas, run_id, reglas)
    manifiesto = _manifiesto(
        run_id=run_id,
        politica=politica,
        policy_ref=policy_ref,
        events_ref=events_ref,
        eventos=eventos,
        alertas=validadas,
        reglas=reglas,
        configuracion=cfg,
        generated_at=generado,
    )
    _validar_manifiesto(manifiesto, validadas)
    return {
        "manifest": manifiesto,
        "alerts": validadas,
        "policy_path": str(politica_path),
        "policy_id": politica["policy_id"],
        "policy_status": politica["sustained_change"]["status"],
        "event_count": len(eventos),
    }


def persistir_alertas(
    manifiesto: Mapping[str, Any],
    alertas: Sequence[Mapping[str, Any]],
    *,
    root: str | Path = Path("datos_locales"),
) -> Path:
    """Publica el manifiesto JSONL de forma atomica, sin sobrescribir corridas."""

    validado = _validar_manifiesto(dict(manifiesto), alertas)
    run_id = validado["run_id"]
    destino = Path(root) / "alertas" / f"{run_id}.jsonl"
    if destino.exists():
        raise FileExistsError(f"ya existen alertas de esta corrida: {destino}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name(f".{destino.name}.{secrets.token_hex(4)}.tmp")
    try:
        with temporal.open("w", encoding="utf-8", newline="\n") as archivo:
            archivo.write(_json_canonico(validado) + "\n")
            for alerta in alertas:
                archivo.write(_json_canonico(alerta) + "\n")
        os.replace(temporal, destino)
    except BaseException:
        temporal.unlink(missing_ok=True)
        raise
    return destino


def ejecutar_alertas(
    root: str | Path,
    run_id: str,
    *,
    policy_path: str | Path | None = None,
    configuracion: ConfiguracionAlertas | None = None,
    generado_en: str | None = None,
) -> Path:
    """Construye y persiste las alertas de una corrida ya publicada."""

    resultado = construir_alertas(
        root,
        run_id,
        policy_path=policy_path,
        configuracion=configuracion,
        generado_en=generado_en,
    )
    return persistir_alertas(resultado["manifest"], resultado["alerts"], root=root)


def leer_manifiesto_alertas(
    root: str | Path, run_id: str
) -> dict[str, Any]:
    """Carga y valida el JSONL de alertas de una corrida."""

    run_id = _validar_run_id(run_id)
    ruta = Path(root) / "alertas" / f"{run_id}.jsonl"
    if not ruta.exists():
        raise FileNotFoundError(f"no hay alertas para la corrida {run_id}: {ruta}")
    filas: list[Any] = []
    with ruta.open("r", encoding="utf-8") as archivo:
        for numero, linea in enumerate(archivo, start=1):
            if not linea.strip():
                continue
            try:
                filas.append(json.loads(linea))
            except json.JSONDecodeError as exc:
                raise ErrorAlertas(
                    f"JSON invalido en {ruta.name}, linea {numero}: {exc.msg}"
                ) from exc
    if not filas:
        raise ErrorAlertas(f"el archivo de alertas esta vacio: {ruta}")
    manifiesto = _validar_manifiesto(filas[0], filas[1:])
    if manifiesto["run_id"] != run_id:
        raise ErrorAlertas("el manifiesto pertenece a otra corrida")
    return {
        "manifest": manifiesto,
        "alerts": [dict(alerta) for alerta in filas[1:]],
        "path": str(ruta),
    }


def consultar_alertas(root: str | Path, run_id: str) -> list[dict[str, Any]]:
    """Devuelve las alertas persistidas en el orden del artefacto."""

    return leer_manifiesto_alertas(root, run_id)["alerts"]


def listar_runs_con_alertas(root: str | Path) -> list[str]:
    """Lista corridas con alertas persistidas, sin interpretar su contenido."""

    carpeta = Path(root) / "alertas"
    if not carpeta.is_dir():
        return []
    return sorted(
        ruta.stem
        for ruta in carpeta.glob("*.jsonl")
        if PATRON_RUN_ID.fullmatch(ruta.stem) is not None
    )


def resumen_alertas(root: str | Path, run_id: str) -> dict[str, Any]:
    """Resume lo persistido sin recalcular zonas ni scores."""

    leido = leer_manifiesto_alertas(root, run_id)
    manifiesto = leido["manifest"]
    alertas = leido["alerts"]
    return {
        "run_id": manifiesto["run_id"],
        "policy_id": manifiesto["policy_id"],
        "policy_status": manifiesto["policy_status"],
        "policy_reason": manifiesto["policy_reason"],
        "alert_count": manifiesto["alert_count"],
        "counts_by_rule": dict(manifiesto["counts_by_rule"]),
        "counts_by_severity": dict(manifiesto["counts_by_severity"]),
        "event_count": manifiesto["event_count"],
        "first_occurred_at": min(
            (alerta["occurred_at"] for alerta in alertas if alerta["occurred_at"]),
            default=None,
        ),
        "latest_occurred_at": max(
            (alerta["occurred_at"] for alerta in alertas if alerta["occurred_at"]),
            default=None,
        ),
        "not_expiring": manifiesto["persistence"]["automatic_expiration"] is False,
        "alert_path": leido["path"],
    }


def verificar_evidencia_alertas(
    root: str | Path, run_id: str
) -> dict[str, Any]:
    """Comprueba que cada alerta siga apuntando a eventos y capturas reales."""

    leido = leer_manifiesto_alertas(root, run_id)
    manifiesto = leido["manifest"]
    reglas = [_regla_desde_dict(regla) for regla in manifiesto["rules"]]
    alertas = _validar_alertas(leido["alerts"], run_id, reglas)
    try:
        eventos = _validar_eventos(consultar_eventos(root, run_id), run_id)
    except (ErrorEventos, OSError, json.JSONDecodeError) as exc:
        raise ErrorAlertas(f"eventos P6-05 invalidos: {exc}") from exc
    indice = {evento["event_id"]: evento for evento in eventos}
    fallos: list[str] = []
    capturas: set[str] = set()
    for alerta in alertas:
        for referencia in alerta["window"]["event_refs"]:
            event_id = referencia["event_id"]
            evento = indice.get(event_id)
            if evento is None:
                fallos.append(f"{alerta['alert_id']}: evento inexistente {event_id}")
                continue
            for campo in (
                "observation_id",
                "frame_id",
                "status",
                "occurred_at",
                "policy_ref",
            ):
                if referencia.get(campo) != evento.get(campo):
                    fallos.append(
                        f"{alerta['alert_id']}: {campo} no coincide en {event_id}"
                    )
            captura = referencia.get("capture")
            if captura is None:
                continue
            try:
                abrir_captura(root, {"capture": captura})
            except (ErrorEventos, OSError, KeyError, TypeError) as exc:
                fallos.append(
                    f"{alerta['alert_id']}: captura no verificable en "
                    f"{event_id}: {exc}"
                )
            else:
                capturas.add(captura["path"])
    policies = {evento["policy_ref"] for evento in eventos}
    if manifiesto.get("policy_ref") not in policies:
        fallos.append("policy_ref del manifiesto no aparece en los eventos")
    if {evento["policy_id"] for evento in eventos} != {manifiesto["policy_id"]}:
        fallos.append("policy_id del manifiesto no coincide con los eventos")
    return {
        "ok": not fallos,
        "run_id": run_id,
        "alert_count": len(alertas),
        "event_count": len(eventos),
        "checked_events": len(indice),
        "checked_captures": len(capturas),
        "policy_id": manifiesto["policy_id"],
        "policy_status": manifiesto["policy_status"],
        "alert_path": leido["path"],
        "events_ref": manifiesto["events_ref"],
        "failures": fallos,
    }
