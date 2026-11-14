"""Pruebas de P6-04: politica de confianza sobre JSONL de P6-03.

Los lotes se construyen a mano para fijar puntuaciones, fronteras y ventanas.
No se carga el modelo ni se ejecuta inferencia: P6-04 solo consume el contrato
persistido por P6-03.
"""

from __future__ import annotations

from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import evaluar_confianza as cli_confianza
from politica_confianza import (
    ConfiguracionConfianza,
    ErrorConfianza,
    MinimosReferencia,
    PUNTUACIONES_DOG,
    ReglaCambioSostenido,
    UmbralesConfianza,
    clasificar_frame,
    clasificar_puntuacion,
    escribir_politica,
    evaluar_archivo,
    evaluar_lote,
    leer_politica,
    resumen_politica,
    resumir_estados,
    ruta_politica_predeterminada,
    validar_politica,
)

INSTANTE = "2026-11-13T12:00:00+00:00"
RUN_ID = "run-prueba"

MODELO = {
    "name": "YOLOX-Tiny",
    "dataset": "COCO",
    "version": "p6-02",
    "sha256": "0" * 64,
    "size_bytes": 123,
    "input": {"width": 416, "height": 416},
    "classes": {"count": 80},
}
MODELO_COMPACTO = {
    clave: MODELO[clave]
    for clave in ("name", "dataset", "version", "sha256", "size_bytes")
}

FILTROS = {
    "score_threshold": 0.10,
    "nms": "class-agnostic",
    "nms_threshold": 0.45,
    "score": "sigmoide del detector",
}
FILTROS_ALTERNOS = {
    "score_threshold": 0.25,
    "nms": "class-agnostic",
    "nms_threshold": 0.45,
    "score": "sigmoide del detector",
}
MODELO_ALTERNO = {**MODELO, "name": "YOLOX-S"}

CONFIG = ConfiguracionConfianza(
    umbrales=UmbralesConfianza(0.30, 0.70),
    regla=ReglaCambioSostenido(
        ventana_frames=2,
        min_puntuaciones_ventana=1,
        ventanas_consecutivas=2,
        fraccion_baja_minima=0.5,
        caida_media_minima=0.10,
    ),
    minimos_referencia=MinimosReferencia(frames=2, puntuaciones=2),
)


def _fuente() -> dict[str, str]:
    return {"id": "demo", "ref": "secuencia", "kind": "directory"}


def _configuracion_lote() -> dict[str, object]:
    return {
        "sample_every": 1,
        "interval_ms": 0,
        "max_frames": None,
        "max_seconds": None,
    }


def _versiones() -> dict[str, str]:
    return {
        "pipeline": "p6-03",
        "schema": "p6-03/1",
        "python": "3.14",
        "opencv": "4.11",
    }


def _resumen(
    observaciones: list[dict],
    run_id: str = RUN_ID,
    *,
    filtros: dict | None = None,
    modelo: dict | None = None,
) -> dict:
    """Calcula el resumen con la misma acumulacion publica de P6-03."""
    conteo: dict[str, object] = {
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
    for registro in observaciones:
        estado_fuente = registro.get("source_status")
        estado = registro.get("status")
        if estado_fuente in {"sampled", "discarded"}:
            conteo["frames_received"] += 1
        if estado_fuente == "sampled":
            conteo["frames_sampled"] += 1
        if estado == "discarded":
            conteo["frames_discarded"] += 1
            conteo["discarded_by_reason"][registro.get("reason") or "unknown"] += 1
        elif estado == "read_error":
            conteo["read_errors"] += 1
            conteo["read_errors_by_reason"][registro.get("reason") or "unknown"] += 1
        elif estado == "processed":
            conteo["frames_processed"] += 1
            clases = registro.get("class_counts") or {}
            if clases:
                conteo["frames_with_detections"] += 1
                for nombre, cantidad in clases.items():
                    conteo["detections_by_class"][nombre] += cantidad
                    conteo["detections_total"] += cantidad
            else:
                conteo["frames_without_detections"] += 1
        elif estado == "detector_error":
            conteo["detector_errors"] += 1
            conteo["detector_errors_by_reason"][registro.get("reason") or "unknown"] += 1
        elif estado == "end_of_stream":
            conteo["end_of_stream_events"] += 1
        if estado in {"processed", "detector_error"}:
            conteo["detector_calls"] += 1

    for clave in (
        "discarded_by_reason",
        "read_errors_by_reason",
        "detector_errors_by_reason",
        "detections_by_class",
    ):
        conteo[clave] = dict(sorted(conteo[clave].items()))
    if conteo["read_errors"] or conteo["detector_errors"]:
        resultado = "with_errors"
    elif conteo["frames_received"] == 0 and conteo["frames_processed"] == 0:
        resultado = "no_data"
    else:
        resultado = "ok"
    return {
        "schema": "p6-03/1",
        "record": "resumen",
        "run_id": run_id,
        "started_at": INSTANTE,
        "finished_at": INSTANTE,
        "duration_ms": 0.0,
        "end_state": "end_of_stream",
        "technical_result": resultado,
        "source": _fuente(),
        "config": _configuracion_lote(),
        "model": modelo or MODELO,
        "filters": filtros or FILTROS,
        "counts": conteo,
        "versions": _versiones(),
    }


def _registro(
    numero: int,
    *,
    scores: tuple[float, ...] = (0.9,),
    status: str = "processed",
    source_status: str | None = None,
    class_name: str = "dog",
    reason: str | None = None,
    error: str | None = None,
) -> dict:
    """Un registro P6-03 valido con una o varias detecciones puntuadas."""
    if source_status is None:
        source_status = {
            "processed": "sampled",
            "discarded": "discarded",
            "read_error": "read_error",
            "detector_error": "sampled",
            "end_of_stream": "end_of_stream",
        }[status]
    frame_id = None if status == "end_of_stream" else f"frame-{numero:06d}"
    frame_number = None if status == "end_of_stream" else numero
    image = None
    detections = None
    class_counts = None
    timings = None
    model = None
    technical_result = None

    if status == "processed":
        image = {"width": 32, "height": 24}
        detections = [
            {
                "class_id": 16,
                "class_name": class_name,
                "confidence": float(score),
                "bbox_xyxy": [1.0, 2.0, 3.0, 4.0],
            }
            for score in scores
        ]
        class_counts = dict(Counter(class_name for _ in scores))
        timings = {"preprocess": 0.1, "inference": 0.2, "postprocess": 0.3}
        model = MODELO_COMPACTO
        technical_result = "ok" if scores else "no_detections"
    elif status == "detector_error":
        image = {"width": 32, "height": 24}
        timings = {"preprocess": 0.1, "inference": 0.2, "postprocess": 0.0}
        model = MODELO_COMPACTO
        technical_result = "error"
    elif status == "discarded":
        image = {"width": 32, "height": 24}
    elif status in {"read_error", "detector_error"}:
        technical_result = "error"

    return {
        "schema": "p6-03/1",
        "record": "observacion",
        "run_id": RUN_ID,
        "observation_id": f"demo-{numero:06d}",
        "sequence": numero,
        "source_id": "demo",
        "frame_id": frame_id,
        "frame_number": frame_number,
        "received_at": INSTANTE,
        "started_at": INSTANTE,
        "finished_at": INSTANTE,
        "duration_ms": 0.0,
        "source_status": source_status,
        "status": status,
        "technical_result": technical_result,
        "reason": reason,
        "image": image,
        "detections": detections,
        "class_counts": class_counts,
        "detector_timings_ms": timings,
        "model": model,
        "error": error,
    }


def _lote(
    observaciones: list[dict],
    *,
    run_id: str = RUN_ID,
    filtros: dict | None = None,
    modelo: dict | None = None,
) -> list[dict]:
    registros = deepcopy(observaciones)
    modelo_compacto = None
    if modelo is not None:
        modelo_compacto = {
            campo: modelo[campo]
            for campo in ("name", "dataset", "version", "sha256", "size_bytes")
            if campo in modelo
        }
    for registro in registros:
        registro["run_id"] = run_id
        if modelo_compacto is not None and registro.get("model") is not None:
            registro["model"] = modelo_compacto
    return [
        _cabecera(run_id, filtros=filtros, modelo=modelo),
        *registros,
        _resumen(registros, run_id, filtros=filtros, modelo=modelo),
    ]


def _escribir_lote(ruta: Path, registros: list[dict]) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        "".join(
            json.dumps(registro, ensure_ascii=False, sort_keys=True) + "\n"
            for registro in registros
        ),
        encoding="utf-8",
    )
    return ruta


def _puntuados(valores: tuple[float, ...], *, inicio: int = 1) -> list[dict]:
    return [
        _registro(inicio + indice, scores=(valor,))
        for indice, valor in enumerate(valores)
    ]


class ClasificacionTests(unittest.TestCase):
    def test_fronteras_exactas_de_las_tres_zonas(self) -> None:
        self.assertEqual(clasificar_puntuacion(0.0), "low")
        self.assertEqual(clasificar_puntuacion(0.299999), "low")
        self.assertEqual(clasificar_puntuacion(0.30), "doubt")
        self.assertEqual(clasificar_puntuacion(0.699999), "doubt")
        self.assertEqual(clasificar_puntuacion(0.70), "accepted")
        self.assertEqual(clasificar_puntuacion(1.0), "accepted")

    def test_definiciones_de_zonas_coinciden_con_los_umbrales(self) -> None:
        self.assertEqual(
            UmbralesConfianza(0.30, 0.70).definiciones(),
            {
                "low": "s < 0.3",
                "doubt": "0.3 <= s < 0.7",
                "accepted": "s >= 0.7",
            },
        )

    def test_puntuacion_invalida_no_se_redondea_ni_se_inventa(self) -> None:
        for valor in (
            None,
            "0.5",
            True,
            float("nan"),
            float("inf"),
            -0.0001,
            1.0001,
        ):
            with self.subTest(valor=valor):
                with self.assertRaises(ErrorConfianza):
                    clasificar_puntuacion(valor)

    def test_umbrales_invalidos(self) -> None:
        for inferior, superior in (
            (0.70, 0.70),
            (0.80, 0.70),
            (-0.01, 0.70),
            (0.30, 1.01),
            (float("nan"), 0.70),
        ):
            with self.subTest(inferior=inferior, superior=superior):
                with self.assertRaises(ErrorConfianza):
                    UmbralesConfianza(inferior, superior)

    def test_regla_de_cambio_invalida(self) -> None:
        for argumentos in (
            {"ventana_frames": 0},
            {"min_puntuaciones_ventana": 0},
            {"ventanas_consecutivas": 1},
            {"fraccion_baja_minima": 1.01},
            {"caida_media_minima": -0.01},
        ):
            with self.subTest(argumentos=argumentos):
                with self.assertRaises(ErrorConfianza):
                    ReglaCambioSostenido(**argumentos)

    def test_minimos_de_referencia_invalidos(self) -> None:
        for argumentos in ({"frames": 0}, {"puntuaciones": 0}):
            with self.subTest(argumentos=argumentos):
                with self.assertRaises(ErrorConfianza):
                    MinimosReferencia(**argumentos)


class EstadosFrameTests(unittest.TestCase):
    def test_estados_y_suma_de_registros(self) -> None:
        observaciones = [
            _registro(1, scores=(0.9,)),
            _registro(2, scores=()),
            _registro(3, status="discarded", reason="sampling_interval"),
            _registro(4, status="read_error", reason="decode_error", error="fallo"),
            _registro(5, status="end_of_stream"),
        ]
        self.assertEqual(clasificar_frame(observaciones[0]), "scored")
        self.assertEqual(clasificar_frame(observaciones[1]), "no_detections")
        self.assertEqual(clasificar_frame(observaciones[2]), "discarded")
        self.assertEqual(clasificar_frame(observaciones[3]), "technical_error")
        self.assertEqual(clasificar_frame(observaciones[4]), "end_of_stream")
        self.assertEqual(
            resumir_estados(observaciones),
            {
                "total": 5,
                "scored": 1,
                "no_detections": 1,
                "discarded": 1,
                "technical_error": 1,
                "no_score": 0,
                "end_of_stream": 1,
            },
        )

    def test_deteccion_sin_confidence_es_no_score_y_mezcla_es_error(self) -> None:
        sin_puntuacion = {
            "status": "processed",
            "detections": [{"class_name": "dog"}],
        }
        self.assertEqual(clasificar_frame(sin_puntuacion), "no_score")
        mezcla = {
            "status": "processed",
            "detections": [
                {"class_name": "dog"},
                {"class_name": "dog", "confidence": 0.5},
            ],
        }
        with self.assertRaises(ErrorConfianza):
            clasificar_frame(mezcla)

    def test_estado_desconocido_y_detecciones_invalidas(self) -> None:
        with self.assertRaises(ErrorConfianza):
            clasificar_frame({"status": "otro"})
        with self.assertRaises(ErrorConfianza):
            clasificar_frame({"status": "processed", "detections": "no-lista"})
        self.assertEqual(
            clasificar_frame(
                {"status": "processed", "detections": [{"class_name": "dog"}]}
            ),
            "no_score",
        )
        with self.assertRaises(ErrorConfianza):
            clasificar_frame(
                {
                    "status": "processed",
                    "detections": [{"class_name": "dog", "confidence": 1.5}],
                }
            )
        with self.assertRaises(ErrorConfianza):
            clasificar_frame(
                {"status": "processed", "detections": [{"confidence": 0.5}]}
            )

class EvaluacionTests(unittest.TestCase):
    def _lote_con_referencia_y_evaluacion(self) -> list[dict]:
        observaciones = _puntuados((0.884, 0.6717))
        observaciones += _puntuados((0.5386, 0.1594, 0.1128, 0.30, 0.70), inicio=3)
        return _lote(observaciones)

    def test_referencia_interna_zonas_y_puntuaciones_sin_redondeo(self) -> None:
        registros = self._lote_con_referencia_y_evaluacion()
        politica = evaluar_lote(
            registros,
            configuracion=CONFIG,
            entrada_etiqueta="lote.jsonl",
            generado_en=INSTANTE,
        )

        self.assertEqual(politica["schema"], "p6-04/1")
        self.assertEqual(politica["record"], "confidence_policy")
        self.assertEqual(politica["policy_version"], "p6-04")
        self.assertTrue(politica["policy_id"].startswith("p6-04-"))
        self.assertEqual(len(politica["configuration_digest"]), 64)
        self.assertEqual(politica["generated_at"], INSTANTE)
        self.assertEqual(politica["consumer"], "P6-05")
        self.assertEqual(politica["input"]["path"], "lote.jsonl")
        self.assertEqual(politica["input"]["records"], 7)
        self.assertEqual(politica["input"]["summary_technical_result"], "ok")

        referencia = politica["reference"]
        self.assertEqual(referencia["origin"], "first_window")
        self.assertEqual(referencia["status"], "ok")
        self.assertEqual(referencia["frame_count"], 2)
        self.assertEqual(referencia["scored_detections"], 2)
        self.assertEqual(referencia["zones"], {"low": 0, "doubt": 1, "accepted": 1})
        self.assertEqual(referencia["mean_confidence"], 0.77785)
        observada = politica["thresholds"]["justification"]["observed_reference"]
        self.assertEqual(observada["run_id"], RUN_ID)
        self.assertEqual(observada["frames"], 2)
        self.assertEqual(observada["quantiles"]["q50"], 0.6717)
        self.assertEqual(
            [puntuacion["confidence"] for puntuacion in referencia["scores"]],
            [0.884, 0.6717],
        )

        evaluacion = politica["evaluation"]
        self.assertEqual(evaluacion["scope"], "after_internal_reference")
        self.assertEqual(evaluacion["batch_scored_frames"], 7)
        self.assertEqual(evaluacion["excluded_as_reference_frames"], 2)
        self.assertEqual(evaluacion["evaluated_frames"], 5)
        self.assertEqual(evaluacion["denominator"]["value"], 5)
        self.assertEqual(evaluacion["zones"], {"low": 2, "doubt": 2, "accepted": 1})
        self.assertEqual(evaluacion["mean_confidence"], 0.36216)
        self.assertEqual(
            [puntuacion["confidence"] for puntuacion in evaluacion["scores"]],
            [0.5386, 0.1594, 0.1128, 0.30, 0.70],
        )
        self.assertEqual(
            [puntuacion["zone"] for puntuacion in evaluacion["scores"]],
            ["doubt", "low", "low", "doubt", "accepted"],
        )
        self.assertEqual(evaluacion["complete_windows"], 2)
        self.assertEqual(evaluacion["incomplete_windows"], 1)
        self.assertEqual(
            evaluacion["windows"][2]["incomplete_reason"], "partial_window"
        )

        cambio = politica["sustained_change"]
        self.assertEqual(cambio["status"], "sustained_change")
        self.assertEqual(cambio["reason"], "consecutive_degraded_windows")
        self.assertEqual(cambio["reference_status"], "ok")
        self.assertEqual(cambio["max_consecutive_degraded_windows"], 2)
        self.assertTrue(cambio["evidence_sufficient"])
        self.assertFalse(cambio["normality_established"])
        self.assertTrue(cambio["insufficiency_is_not_normality"])
        self.assertFalse(politica["normality_established"])

        self.assertEqual(
            politica["thresholds"]["zones"], UmbralesConfianza().definiciones()
        )
        self.assertEqual(
            politica["thresholds"]["justification"]["origin"],
            "exploratory_single_image",
        )
        self.assertEqual(
            politica["thresholds"]["justification"]["evidence"]["scores"],
            list(PUNTUACIONES_DOG),
        )
        self.assertFalse(
            politica["thresholds"]["justification"]["evidence"]["robust_reference"]
        )
        self.assertTrue(politica["detection_contract"]["low_scores_observable"])
        self.assertIsNone(politica["detection_contract"]["warning"])
        self.assertIn("insufficiency_is_not_normality", politica["p6_05_contract"])
        self.assertTrue(politica["p6_05_contract"]["insufficiency_is_not_normality"])
        self.assertGreaterEqual(len(politica["limitations"]), 5)

class ReferenciaExternaTests(unittest.TestCase):
    def _evaluar(
        self,
        referencia: list[dict],
        *,
        observaciones: list[dict] | None = None,
    ) -> dict:
        if observaciones is None:
            observaciones = _puntuados((0.9, 0.9, 0.1, 0.1))
        return evaluar_lote(
            _lote(observaciones),
            referencia_registros=referencia,
            configuracion=CONFIG,
            entrada_etiqueta="lote.jsonl",
            referencia_etiqueta="referencia.jsonl",
            generado_en=INSTANTE,
        )

    def test_referencia_externa_compatible_no_se_excluye_del_denominador(
        self,
    ) -> None:
        politica = self._evaluar(_lote(_puntuados((0.9, 0.9))))

        referencia = politica["reference"]
        self.assertEqual(referencia["origin"], "file")
        self.assertEqual(referencia["path"], "referencia.jsonl")
        self.assertEqual(referencia["status"], "ok")
        self.assertTrue(referencia["identity_matches"])
        self.assertEqual(referencia["frame_count"], 2)
        self.assertEqual(referencia["scored_detections"], 2)
        self.assertEqual(politica["input"]["reference_path"], "referencia.jsonl")

        evaluacion = politica["evaluation"]
        self.assertEqual(evaluacion["scope"], "all_scored_records")
        self.assertEqual(evaluacion["excluded_as_reference_frames"], 0)
        self.assertEqual(evaluacion["batch_scored_frames"], 4)
        self.assertEqual(evaluacion["evaluated_frames"], 4)
        self.assertEqual(evaluacion["denominator"]["value"], 4)
        self.assertEqual(politica["sustained_change"]["reference_status"], "ok")

    def test_referencia_externa_con_modelo_o_filtros_incompatibles(self) -> None:
        casos = {
            "modelo": _lote(_puntuados((0.9, 0.9)), modelo=MODELO_ALTERNO),
            "filtros": _lote(_puntuados((0.9, 0.9)), filtros=FILTROS_ALTERNOS),
        }
        for nombre, referencia_lote in casos.items():
            with self.subTest(caso=nombre):
                politica = self._evaluar(referencia_lote)
                referencia = politica["reference"]
                self.assertEqual(referencia["origin"], "file")
                self.assertEqual(referencia["status"], "incompatible")
                self.assertFalse(referencia["identity_matches"])
                self.assertNotEqual(
                    referencia["identity"]["sha256"],
                    politica["identity"]["sha256"],
                )

                cambio = politica["sustained_change"]
                self.assertEqual(cambio["status"], "reference_incompatible")
                self.assertEqual(cambio["reason"], "reference_identity_mismatch")
                self.assertEqual(cambio["reference_status"], "incompatible")
                self.assertFalse(cambio["evidence_sufficient"])
                self.assertFalse(cambio["normality_established"])
                self.assertEqual(cambio["reference_mean_confidence"], 0.9)
                self.assertFalse(politica["normality_established"])

    def test_referencia_externa_insuficiente_no_es_normalidad(self) -> None:
        politica = self._evaluar(_lote(_puntuados((0.9,))))

        referencia = politica["reference"]
        self.assertEqual(referencia["status"], "insufficient")
        self.assertEqual(referencia["frame_count"], 1)
        self.assertEqual(referencia["scored_detections"], 1)
        self.assertEqual(referencia["minimums"], {"frames": 2, "scored_detections": 2})

        cambio = politica["sustained_change"]
        self.assertEqual(cambio["status"], "insufficient_evidence")
        self.assertEqual(cambio["reason"], "reference_insufficient")
        self.assertEqual(cambio["reference_status"], "insufficient")
        self.assertFalse(cambio["evidence_sufficient"])
        self.assertFalse(cambio["normality_established"])
        self.assertGreater(cambio["complete_windows"], 0)
        self.assertFalse(politica["normality_established"])



class EvidenciaInsuficienteTests(unittest.TestCase):
    def test_lote_sin_detecciones_no_acredita_normalidad(self) -> None:
        observaciones = [_registro(numero, scores=()) for numero in (1, 2, 3)]
        politica = evaluar_lote(
            _lote(observaciones),
            configuracion=CONFIG,
            entrada_etiqueta="lote.jsonl",
            generado_en=INSTANTE,
        )

        self.assertEqual(politica["input"]["summary_technical_result"], "ok")
        self.assertEqual(politica["reference"]["status"], "insufficient")
        self.assertIsNone(
            politica["thresholds"]["justification"]["observed_reference"]
        )
        self.assertEqual(politica["reference"]["frame_count"], 0)
        self.assertEqual(politica["reference"]["scored_detections"], 0)

        evaluacion = politica["evaluation"]
        self.assertEqual(evaluacion["records"]["no_detections"], 3)
        self.assertEqual(evaluacion["records"]["scored"], 0)
        self.assertEqual(evaluacion["denominator"]["value"], 0)
        self.assertEqual(evaluacion["zones"], {"low": 0, "doubt": 0, "accepted": 0})
        self.assertEqual(
            evaluacion["zone_fractions"], {"low": None, "doubt": None, "accepted": None}
        )
        self.assertIsNone(evaluacion["mean_confidence"])
        self.assertEqual(evaluacion["complete_windows"], 0)
        self.assertEqual(evaluacion["scores"], [])

        cambio = politica["sustained_change"]
        self.assertEqual(cambio["status"], "insufficient_evidence")
        self.assertFalse(cambio["evidence_sufficient"])
        self.assertFalse(cambio["normality_established"])
        self.assertFalse(politica["normality_established"])

    def test_errores_y_descartes_no_cuentan_ni_crean_normalidad(self) -> None:
        observaciones = [
            _registro(1, scores=(0.9,)),
            _registro(2, scores=(0.9,)),
            _registro(3, status="discarded", reason="sampling_interval"),
            _registro(4, status="read_error", reason="decode_error", error="fallo"),
            _registro(5, status="detector_error", reason="inference", error="fallo"),
            _registro(6, scores=(0.1,)),
        ]
        politica = evaluar_lote(
            _lote(observaciones),
            configuracion=CONFIG,
            entrada_etiqueta="lote.jsonl",
            generado_en=INSTANTE,
        )

        self.assertEqual(politica["input"]["summary_technical_result"], "with_errors")
        self.assertEqual(politica["reference"]["status"], "ok")
        self.assertEqual(politica["reference"]["frame_count"], 2)

        evaluacion = politica["evaluation"]
        self.assertEqual(evaluacion["records"]["total"], 6)
        self.assertEqual(evaluacion["records"]["scored"], 3)
        self.assertEqual(evaluacion["records"]["discarded"], 1)
        self.assertEqual(evaluacion["records"]["technical_error"], 2)
        self.assertEqual(evaluacion["records"]["no_detections"], 0)
        self.assertEqual(evaluacion["denominator"]["value"], 1)
        self.assertEqual(evaluacion["evaluated_frames"], 1)
        self.assertEqual(evaluacion["complete_windows"], 0)

        cambio = politica["sustained_change"]
        self.assertEqual(cambio["status"], "insufficient_evidence")
        self.assertEqual(cambio["reason"], "complete_windows_below_minimum")
        self.assertFalse(cambio["evidence_sufficient"])
        self.assertFalse(cambio["normality_established"])
        self.assertFalse(politica["normality_established"])



class CambioSostenidoTests(unittest.TestCase):
    def _evaluar(self, observaciones: list[dict]) -> dict:
        return evaluar_lote(
            _lote(observaciones),
            configuracion=CONFIG,
            entrada_etiqueta="lote.jsonl",
            generado_en=INSTANTE,
        )

    def test_cambio_sostenido_requiere_ventanas_consecutivas_degradadas(
        self,
    ) -> None:
        observaciones = _puntuados((0.9, 0.9))
        observaciones += _puntuados((0.1, 0.1, 0.1, 0.1), inicio=3)
        politica = self._evaluar(observaciones)

        self.assertEqual(politica["reference"]["status"], "ok")
        self.assertEqual(politica["reference"]["mean_confidence"], 0.9)
        evaluacion = politica["evaluation"]
        self.assertEqual(evaluacion["complete_windows"], 2)
        self.assertEqual(evaluacion["incomplete_windows"], 0)
        self.assertEqual(evaluacion["zones"], {"low": 4, "doubt": 0, "accepted": 0})
        self.assertEqual(evaluacion["mean_confidence"], 0.1)
        for ventana in evaluacion["windows"]:
            self.assertTrue(ventana["complete"])
            self.assertTrue(ventana["degraded"])
            self.assertEqual(ventana["degradation_reasons"], ["degraded"])
            self.assertEqual(ventana["mean_drop_vs_reference"], 0.8)

        cambio = politica["sustained_change"]
        self.assertEqual(cambio["status"], "sustained_change")
        self.assertEqual(cambio["reason"], "consecutive_degraded_windows")
        self.assertEqual(cambio["max_consecutive_degraded_windows"], 2)
        self.assertTrue(cambio["evidence_sufficient"])
        self.assertFalse(cambio["normality_established"])
        self.assertTrue(cambio["insufficiency_is_not_normality"])
        self.assertFalse(politica["normality_established"])

    def test_ventana_no_degradada_corta_la_racha(self) -> None:
        observaciones = (
            _puntuados((0.9, 0.9))
            + _puntuados((0.1, 0.1), inicio=3)
            + _puntuados((0.9, 0.9), inicio=5)
            + _puntuados((0.1, 0.1), inicio=7)
        )
        politica = self._evaluar(observaciones)

        self.assertEqual(politica["evaluation"]["complete_windows"], 3)
        self.assertEqual(politica["evaluation"]["incomplete_windows"], 0)

        cambio = politica["sustained_change"]
        self.assertEqual(cambio["status"], "no_sustained_change")
        self.assertEqual(cambio["reason"], "no_consecutive_degraded_windows")
        self.assertEqual(cambio["max_consecutive_degraded_windows"], 1)
        self.assertTrue(cambio["evidence_sufficient"])
        self.assertTrue(cambio["normality_established"])
        self.assertTrue(politica["normality_established"])

    def test_cuadro_sin_detecciones_no_une_ventanas_ni_acredita_normalidad(self) -> None:
        observaciones = _puntuados((0.9, 0.9, 0.9, 0.9))
        observaciones.append(_registro(5, scores=()))
        observaciones += _puntuados((0.9, 0.9), inicio=6)
        politica = self._evaluar(observaciones)

        evaluacion = politica["evaluation"]
        self.assertEqual(evaluacion["denominator"]["value"], 4)
        self.assertEqual(evaluacion["timeline_frames"], 5)
        self.assertEqual(evaluacion["complete_windows"], 1)
        self.assertEqual(evaluacion["windows"][1]["incomplete_reason"], "unscored_or_missing_frame")
        self.assertEqual(politica["sustained_change"]["status"], "insufficient_evidence")
        self.assertFalse(politica["normality_established"])

    def test_error_en_referencia_interna_la_deja_insuficiente(self) -> None:
        observaciones = [_registro(1, scores=(0.9,))]
        observaciones.append(_registro(2, status="read_error", reason="decode_error", error="fallo"))
        observaciones += _puntuados((0.9, 0.9, 0.9, 0.9, 0.9), inicio=3)
        politica = self._evaluar(observaciones)

        self.assertEqual(politica["reference"]["status"], "insufficient")
        self.assertTrue(politica["reference"]["interrupted_by_unscored_frame"])
        self.assertEqual(politica["sustained_change"]["status"], "insufficient_evidence")
        self.assertFalse(politica["normality_established"])

    def test_descartes_por_muestreo_no_rompen_ventanas(self) -> None:
        observaciones = [_registro(1, scores=(0.9,))]
        observaciones.append(_registro(2, status="discarded", reason="sampling_interval"))
        observaciones += _puntuados((0.9, 0.9, 0.9, 0.9, 0.9), inicio=3)
        politica = self._evaluar(observaciones)

        self.assertEqual(politica["reference"]["status"], "ok")
        self.assertFalse(politica["reference"]["interrupted_by_unscored_frame"])
        self.assertEqual(politica["evaluation"]["timeline_frames"], 4)
        self.assertEqual(politica["evaluation"]["complete_windows"], 2)
        self.assertEqual(politica["sustained_change"]["status"], "no_sustained_change")
        self.assertTrue(politica["normality_established"])


class EntradaInvalidaTests(unittest.TestCase):
    def test_lote_que_no_es_lista_o_cabecera_invalida(self) -> None:
        for registros in ("no-lista", {"schema": "p6-03/1"}, [{"record": "cabecera"}]):
            with self.subTest(registros=registros):
                with self.assertRaises(ErrorConfianza):
                    evaluar_lote(
                        registros,
                        configuracion=CONFIG,
                        entrada_etiqueta="lote.jsonl",
                    )

    def test_resumen_observacion_o_configuracion_inconsistentes(self) -> None:
        base = _lote(_puntuados((0.9, 0.9)))

        cabecera_alterada = deepcopy(base)
        cabecera_alterada[0]["schema"] = "p6-04/1"

        resumen_alterado = deepcopy(base)
        resumen_alterado[-1]["technical_result"] = "with_errors"

        conteo_alterado = deepcopy(base)
        conteo_alterado[-1]["counts"]["frames_processed"] = 99

        observacion_alterada = deepcopy(base)
        observacion_alterada[1]["model"] = {
            **MODELO_COMPACTO,
            "name": "YOLOX-S",
        }

        for nombre, registros in {
            "cabecera": cabecera_alterada,
            "resumen": resumen_alterado,
            "conteo": conteo_alterado,
            "observacion": observacion_alterada,
        }.items():
            with self.subTest(caso=nombre):
                with self.assertRaises(ErrorConfianza):
                    evaluar_lote(
                        registros,
                        configuracion=CONFIG,
                        entrada_etiqueta="lote.jsonl",
                    )

        with self.assertRaises(ErrorConfianza):
            evaluar_lote(base, configuracion={"umbrales": None})


class RutaSalidaTests(unittest.TestCase):
    def test_referencia_igual_a_entrada_se_rechaza(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = _escribir_lote(
                Path(carpeta) / "lote.jsonl", _lote(_puntuados((0.9, 0.9)))
            )
            with self.assertRaisesRegex(ErrorConfianza, "archivo distinto"):
                evaluar_archivo(ruta, referencia=ruta, configuracion=CONFIG)

    def test_salida_igual_a_entrada_se_rechaza_sin_tocar_el_archivo(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = _escribir_lote(
                Path(carpeta) / "lote.jsonl", _lote(_puntuados((0.9, 0.9)))
            )
            original = ruta.read_bytes()
            salida = io.StringIO()
            errores = io.StringIO()
            argv = [
                "evaluar_confianza.py",
                "--input",
                str(ruta),
                "--output",
                str(ruta),
            ]
            with patch.object(sys, "argv", argv):
                with redirect_stdout(salida), redirect_stderr(errores):
                    codigo = cli_confianza.main()

            self.assertEqual(codigo, 1)
            self.assertEqual(salida.getvalue(), "")
            self.assertIn(
                "la salida no puede reemplazar la entrada", errores.getvalue()
            )
            self.assertEqual(ruta.read_bytes(), original)


class ArtefactoTests(unittest.TestCase):
    def test_json_ida_y_vuelta_conserva_la_politica_validada(self) -> None:
        observaciones = _puntuados((0.884, 0.6717))
        observaciones += _puntuados(
            (0.5386, 0.1594, 0.1128, 0.30, 0.70), inicio=3
        )
        politica = evaluar_lote(
            _lote(observaciones),
            configuracion=CONFIG,
            entrada_etiqueta="lote.jsonl",
            generado_en=INSTANTE,
        )

        with tempfile.TemporaryDirectory() as carpeta:
            destino = Path(carpeta) / "politica.json"
            self.assertEqual(escribir_politica(politica, destino), destino)
            leida = leer_politica(destino)
            self.assertEqual(leida, politica)
            self.assertEqual(
                json.loads(destino.read_text(encoding="utf-8")), politica
            )
            self.assertEqual(
                resumen_politica(leida), resumen_politica(politica)
            )
            self.assertEqual(validar_politica(leida), leida)



def _cabecera(
    run_id: str = RUN_ID,
    *,
    filtros: dict | None = None,
    modelo: dict | None = None,
) -> dict:
    return {
        "schema": "p6-03/1",
        "record": "cabecera",
        "run_id": run_id,
        "created_at": INSTANTE,
        "pipeline": "p6-03",
        "source": _fuente(),
        "config": _configuracion_lote(),
        "model": modelo or MODELO,
        "filters": filtros or FILTROS,
        "versions": _versiones(),
    }
