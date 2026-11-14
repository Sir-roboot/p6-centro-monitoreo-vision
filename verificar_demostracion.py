"""Verificador local opcional de las demostraciones P6-09 C1/C2.

Lee los artefactos ya persistidos en ``datos_locales/`` y reutiliza las
funciones de P6-05, P6-06, P6-07 y P6-08. No ejecuta el detector, no vuelve a
puntuar, no modifica scores, umbrales, eventos, alertas ni capturas y no sube
JPEG/JSONL al repositorio. Es una comprobacion de coherencia documental, no una
medicion de calidad predictiva ni una acreditacion de escena saludable.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from alertas import (
    ErrorAlertas,
    leer_manifiesto_alertas,
    resumen_alertas,
    verificar_evidencia_alertas,
)
from eventos import ErrorEventos, abrir_captura, consultar_eventos
from metricas import ErrorMetricas, _sha256_archivo, leer_jsonl
from portal import ErrorPortal, reporte_metricas

# Valores documentados en docs/P6-09_DEMOSTRACIONES.md. Los hashes son los que
# el reporte P6-07 registra para sus entradas, de modo que el verificador
# detecta si un JSONL local fue reemplazado despues de calcular las metricas.
DEMOSTRACIONES: dict[str, dict[str, Any]] = {
    "p6-09-c1-20261113-232205": {
        "technical_result": "ok",
        "observation_records": 138,
        "frames_received": 136,
        "frames_processed": 10,
        "frames_discarded": 126,
        "detector_errors": 0,
        "event_count": 10,
        "capture_count": 10,
        "alert_count": 10,
        "low_confidence_alerts": 10,
        "zones": {"accepted": 59, "doubt": 64, "low": 33},
        "g4": (33, 156),
        "g5": (10, 10),
        "observations_sha256": (
            "d76fe227f85577dd37a1043d48f8a3d8665142488ae3e07a0de7ef8be8fbd437"
        ),
        "events_sha256": (
            "587b3cccd83ed19006c1606de0a49cf2df6f5479f9833701feebd6ecc1cf5f36"
        ),
        "alerts_sha256": (
            "64f15fda531e40356cb06e6ce97477b4e25c0ea33c2c9960a227cf2af8fd2a0c"
        ),
    },
    "p6-09-video-20261113-230341": {
        "technical_result": "ok",
        "observation_records": 138,
        "frames_received": 136,
        "frames_processed": 10,
        "frames_discarded": 126,
        "detector_errors": 0,
        "event_count": 10,
        "capture_count": 10,
        "alert_count": 10,
        "low_confidence_alerts": 10,
        "zones": {"accepted": 53, "doubt": 104, "low": 60},
        "g4": (60, 217),
        "g5": (10, 10),
        "observations_sha256": (
            "5dc7a7ba1e0b72db8db8b8f62b3c0ec8bd06ee93f87b8f647ebcce822171b1a7"
        ),
        "events_sha256": (
            "1b97abc30b25e6685779244a1b45bcb8e8cd125600a933adfca76c297537afc1"
        ),
        "alerts_sha256": (
            "2a448b780107a99946e8a58f0ecfd36c8c8ad2a46dd2c9d7c7200f92bbabb3f6"
        ),
    },
}

MODELO_SHA256 = "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"
POLICY_ID = "p6-04-2a45934f907b96d9"
POLICY_DIGEST = "2a45934f907b96d9ac09557e36a2441cd6c0625246180b250f3e15d2a9a783f2"
POLICY_LOWER = 0.17
POLICY_UPPER = 0.61
REFERENCE_RUN_ID = "monitor-p6-04-20261113-214440"
FILTROS = {
    "input_size": [416, 416],
    "nms": "class-agnostic",
    "nms_threshold": 0.45,
    "normalization": "none",
    "pad_value": 114,
    "preprocess": "letterbox-bottom-right",
    "score": "objectness*class_probability",
    "score_threshold": 0.1,
}


def verificar_corrida(
    root: str | Path,
    run_id: str,
    expected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Valida una corrida P6-09 con las funciones de consulta ya existentes."""

    base = Path(root)
    esperado = dict(expected or DEMOSTRACIONES.get(run_id) or {})
    if not esperado:
        raise ValueError(f"no hay expectativas documentadas para {run_id}")
    errores: list[str] = []
    observado: dict[str, Any] = {"run_id": run_id}

    ruta_observaciones = base / "observaciones" / f"{run_id}.jsonl"
    ruta_eventos = base / "eventos" / f"{run_id}.jsonl"
    ruta_alertas = base / "alertas" / f"{run_id}.jsonl"

    registros = leer_jsonl(ruta_observaciones)
    _comparar(
        errores,
        "registros de observaciones",
        len(registros),
        esperado["observation_records"],
    )
    if not registros or registros[0].get("record") != "cabecera":
        errores.append("observaciones: falta la cabecera P6-03")
        cabecera: dict[str, Any] = {}
        resumen: dict[str, Any] = {}
    else:
        cabecera = registros[0]
        resumen = registros[-1] if registros[-1].get("record") == "resumen" else {}
        if not resumen:
            errores.append("observaciones: falta el resumen P6-03")
    if cabecera:
        _comparar(errores, "schema de cabecera", cabecera.get("schema"), "p6-03/1")
        _comparar(errores, "run_id de cabecera", cabecera.get("run_id"), run_id)
        _comparar(
            errores,
            "modelo sha256",
            (cabecera.get("model") or {}).get("sha256"),
            MODELO_SHA256,
        )
        _comparar(
            errores,
            "modelo nombre/formato/version",
            (
                (cabecera.get("model") or {}).get("name"),
                (cabecera.get("model") or {}).get("format"),
                (cabecera.get("model") or {}).get("version"),
            ),
            ("YOLOX-Tiny", "ONNX", "0.1.1rc0"),
        )
        _comparar(
            errores,
            "modelo runtime",
            (
                ((cabecera.get("model") or {}).get("runtime") or {}).get("backend"),
                ((cabecera.get("model") or {}).get("runtime") or {}).get("target"),
            ),
            ("opencv-dnn", "cpu"),
        )
        config = cabecera.get("config") or {}
        _comparar(errores, "config sample_every", config.get("sample_every"), 15)
        _comparar(errores, "config max_frames", config.get("max_frames"), 136)
        _comparar(errores, "config max_seconds", config.get("max_seconds"), 0.0)
        _comparar(errores, "config interval_ms", config.get("interval_ms"), 200.0)
        _comparar(errores, "filtros de preprocesamiento", cabecera.get("filters"), FILTROS)
        fuente = cabecera.get("source") or {}
        _comparar(errores, "fuente kind", fuente.get("kind"), "stream")
        _comparar(errores, "fuente id", fuente.get("id"), "monitor-video")
        _comparar(
            errores,
            "fuente MJPEG",
            fuente.get("ref"),
            "http://127.0.0.1:8765/stream.mjpg",
        )
    if resumen:
        _comparar(
            errores,
            "technical_result",
            resumen.get("technical_result"),
            esperado["technical_result"],
        )
        conteos = resumen.get("counts") or {}
        for campo in (
            "frames_received",
            "frames_processed",
            "frames_discarded",
            "detector_errors",
        ):
            _comparar(errores, campo, conteos.get(campo), esperado[campo])

    _comprobar_hash(
        errores,
        "observaciones",
        ruta_observaciones,
        esperado["observations_sha256"],
    )
    _comprobar_hash(errores, "eventos", ruta_eventos, esperado["events_sha256"])
    _comprobar_hash(errores, "alertas", ruta_alertas, esperado["alerts_sha256"])

    ruta_politica = base / "politicas" / f"{run_id}-p6-04.json"
    try:
        politica = json.loads(ruta_politica.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errores.append(f"politica P6-04 no legible: {exc}")
    else:
        _comparar(errores, "policy_id de politica", politica.get("policy_id"), POLICY_ID)
        _comparar(
            errores,
            "configuration_digest de politica",
            politica.get("configuration_digest"),
            POLICY_DIGEST,
        )
        umbrales = politica.get("thresholds") or {}
        _comparar(errores, "umbral lower", umbrales.get("lower"), POLICY_LOWER)
        _comparar(errores, "umbral upper", umbrales.get("upper"), POLICY_UPPER)
        _comparar(
            errores,
            "run_id de referencia",
            (politica.get("reference") or {}).get("run_id"),
            REFERENCE_RUN_ID,
        )

    eventos = consultar_eventos(base, run_id)
    _comparar(errores, "eventos", len(eventos), esperado["event_count"])
    capturas = 0
    zonas: Counter[str] = Counter()
    ids_eventos = {evento.get("event_id") for evento in eventos}
    for evento in eventos:
        zonas.update(zona.get("zone") for zona in evento.get("zones", []))
        captura = evento.get("capture")
        if not captura:
            errores.append(f"evento sin captura: {evento.get('event_id')}")
            continue
        try:
            abrir_captura(base, evento)
        except (ErrorEventos, OSError, KeyError, TypeError) as exc:
            errores.append(f"captura no verificable en {evento.get('event_id')}: {exc}")
        else:
            capturas += 1
    _comparar(errores, "capturas verificadas", capturas, esperado["capture_count"])
    _comparar(errores, "zonas", dict(zonas), esperado["zones"])

    leido = leer_manifiesto_alertas(base, run_id)
    alertas = leido["alerts"]
    manifiesto = leido["manifest"]
    _comparar(errores, "alertas", len(alertas), esperado["alert_count"])
    _comparar(
        errores,
        "alert_count del manifiesto",
        manifiesto.get("alert_count"),
        esperado["alert_count"],
    )
    _comparar(
        errores,
        "alertas low-confidence",
        manifiesto.get("counts_by_rule", {}).get("p6-06-low-confidence"),
        esperado["low_confidence_alerts"],
    )
    for alerta in alertas:
        if alerta.get("event_id") not in ids_eventos:
            errores.append(
                f"alerta {alerta.get('alert_id')}: event_id inexistente "
                f"{alerta.get('event_id')}"
            )
    verificacion = verificar_evidencia_alertas(base, run_id)
    if not verificacion.get("ok"):
        errores.extend(
            f"verificacion de alertas: {fallo}"
            for fallo in verificacion.get("failures", [])
        )
    resumen_alertas_run = resumen_alertas(base, run_id)

    reporte = reporte_metricas(base, run_id)
    g4_observado: tuple[Any, Any] | None = None
    g5_observado: tuple[Any, Any] | None = None
    if reporte is None:
        errores.append("no hay reporte P6-07 persistido")
    else:
        _comparar(errores, "schema del reporte", reporte.get("schema"), "p6-07/1")
        _comparar(errores, "run_id del reporte", reporte.get("run_id"), run_id)
        entradas = reporte.get("inputs") or {}
        for nombre, clave_hash, registros in (
            (
                "observaciones",
                "observations_sha256",
                esperado["observation_records"],
            ),
            ("eventos", "events_sha256", esperado["event_count"]),
            ("alertas", "alerts_sha256", esperado["alert_count"] + 1),
        ):
            entrada = entradas.get(nombre) or {}
            _comparar(
                errores,
                f"inputs.{nombre}.sha256",
                entrada.get("sha256"),
                esperado[clave_hash],
            )
            _comparar(
                errores,
                f"inputs.{nombre}.registros",
                entrada.get("registros"),
                registros,
            )
        g4 = reporte["metricas"]["G4"]
        g4_observado = (g4.get("numerador"), g4.get("denominador"))
        _comparar(
            errores,
            "G4 numerador/denominador",
            g4_observado,
            esperado["g4"],
        )
        _comparar(
            errores,
            "G4 evidencia low",
            (g4.get("evidencia") or {}).get("zonas", {}).get("low"),
            zonas.get("low"),
        )
        g5 = reporte["metricas"]["G5"]
        g5_observado = (g5.get("numerador"), g5.get("denominador"))
        _comparar(
            errores,
            "G5 numerador/denominador",
            g5_observado,
            esperado["g5"],
        )
        _comparar(
            errores,
            "G5 evidencia trazables/persistidos",
            (
                (g5.get("evidencia") or {}).get("eventos_trazables"),
                (g5.get("evidencia") or {}).get("eventos"),
            ),
            esperado["g5"],
        )

    observado.update(
        {
            "ok": not errores,
            "event_count": len(eventos),
            "capture_count": capturas,
            "alert_count": len(alertas),
            "zones": dict(zonas),
            "policy_id": manifiesto.get("policy_id"),
            "policy_status": manifiesto.get("policy_status"),
            "alerts_verification": verificacion,
            "summary": resumen_alertas_run,
            "g4": g4_observado,
            "g5": g5_observado,
            "errors": errores,
        }
    )
    return observado


def _comparar(
    errores: list[str], etiqueta: str, observado: Any, esperado: Any
) -> None:
    if observado != esperado:
        errores.append(f"{etiqueta}: observado={observado!r}, esperado={esperado!r}")


def _comprobar_hash(
    errores: list[str], etiqueta: str, ruta: Path, esperado: str
) -> None:
    if not ruta.is_file():
        errores.append(f"{etiqueta}: no existe {ruta}")
        return
    _comparar(errores, f"sha256 {etiqueta}", _sha256_archivo(ruta), esperado)


def verificar_demostraciones(
    root: str | Path,
    run_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Verifica las dos corridas documentadas, o solo las indicadas."""

    seleccion = run_ids or list(DEMOSTRACIONES)
    resultados = []
    for run_id in seleccion:
        try:
            resultados.append(verificar_corrida(root, run_id))
        except (
            ErrorAlertas,
            ErrorEventos,
            ErrorMetricas,
            ErrorPortal,
            OSError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            resultados.append(
                {
                    "run_id": run_id,
                    "ok": False,
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
            )
    return {
        "ok": all(resultado["ok"] for resultado in resultados),
        "root": str(Path(root)),
        "run_ids": seleccion,
        "results": resultados,
        "limitations": [
            "Comprueba coherencia de artefactos locales; no mide precision, recall ni calidad predictiva.",
            "10 alertas low-confidence no acreditan escena saludable ni ausencia de baja confianza.",
            "El commit git no esta persistido en los JSONL; el verificador no lo reconstruye.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("datos_locales"))
    parser.add_argument(
        "--run-id",
        action="append",
        dest="run_ids",
        help="corrida a verificar; repetible. Por omision, las dos de P6-09",
    )
    args = parser.parse_args(argv)
    try:
        resultado = verificar_demostraciones(args.root, args.run_ids)
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 10
    print(json.dumps(resultado, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if resultado["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

