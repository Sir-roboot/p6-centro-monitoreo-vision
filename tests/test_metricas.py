"""P6-07: metricas G1-G7, cero seguro, errores y recalculo manual."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from calcular_metricas import main
from metricas import (
    ErrorMetricas,
    GRUPOS,
    calcular_metricas,
    leer_jsonl,
    metricas_por_grupo,
)

INSTANTE = "2026-11-14T04:26:00+00:00"


def _escribir(ruta: Path, registros: list[dict]) -> Path:
    ruta.write_text(
        "".join(json.dumps(fila, ensure_ascii=False) + "\n" for fila in registros),
        encoding="utf-8",
    )
    return ruta


def _evento(event_id: str, zonas: list[str], *, capture: bool = True) -> dict:
    return {
        "record": "event",
        "event_id": event_id,
        "run_id": "run-test",
        "frame_id": f"frame-{event_id}",
        "observation_id": f"run-test:{event_id}",
        "occurred_at": "2026-11-14T04:25:20+00:00",
        "status": "scored",
        "capture": (
            {"path": f"capturas/{event_id}.jpg", "sha256": "a" * 64}
            if capture
            else None
        ),
        "detections": [{"class_name": "person"} for _ in zonas],
        "zones": [
            {"zone": zona, "confidence": 0.2, "class_name": "person"}
            for zona in zonas
        ],
    }


def _alerta(alert_id: str, estado: str, severidad: str) -> dict:
    return {
        "record": "alert",
        "alert_id": alert_id,
        "run_id": "run-test",
        "occurrence_state": estado,
        "severity": severidad,
        "rule_id": "p6-06-low-confidence",
    }


class Artefactos:
    def __init__(self, base: Path):
        self.base = base
        self.observaciones = base / "observaciones.jsonl"
        self.eventos = base / "eventos.jsonl"
        self.alertas = base / "alertas.jsonl"

    def escribir(
        self,
        *,
        counts: dict | None = None,
        recibidos: tuple[str, ...] = ("2026-11-14T04:25:20+00:00",),
        eventos: list[dict] | None = None,
        alertas: list[dict] | None = None,
    ) -> "Artefactos":
        if counts is None:
            counts = {
                "frames_received": max(len(recibidos), 1),
                "frames_sampled": max(len(recibidos), 1),
                "frames_processed": max(len(recibidos), 1),
                "frames_discarded": 0,
                "detector_errors": 0,
                "detector_calls": max(len(recibidos), 1),
            }
        cabecera = {"record": "cabecera", "schema": "p6-03/1", "run_id": "run-test"}
        observaciones = [
            {
                "record": "observacion",
                "run_id": "run-test",
                "observation_id": f"run-test:frame-{indice}",
                "received_at": instante,
                "source_status": "sampled",
                "status": "processed",
            }
            for indice, instante in enumerate(recibidos, start=1)
        ]
        resumen = {
            "record": "resumen",
            "run_id": "run-test",
            "counts": counts,
            "finished_at": recibidos[-1] if recibidos else None,
        }
        _escribir(self.observaciones, [cabecera, *observaciones, resumen])
        _escribir(self.eventos, eventos if eventos is not None else [])
        manifiesto = {"record": "alert_manifest", "run_id": "run-test"}
        _escribir(self.alertas, [manifiesto, *(alertas or [])])
        return self

    def calcular(self, root: Path | None = None, generated_at: str = INSTANTE):
        return calcular_metricas(
            ruta_observaciones=self.observaciones,
            ruta_eventos=self.eventos,
            ruta_alertas=self.alertas,
            root=root or self.base,
            generated_at=generated_at,
        )


class MetricasSieteGrupos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.artefactos = Artefactos(self.base).escribir(
            counts={
                "frames_received": 4,
                "frames_sampled": 2,
                "frames_processed": 1,
                "frames_discarded": 2,
                "detector_errors": 1,
                "detector_calls": 2,
            },
            recibidos=(
                "2026-11-14T04:25:20+00:00",
                "2026-11-14T04:25:33+00:00",
            ),
            eventos=[
                _evento("e1", ["accepted", "doubt", "low"]),
                _evento("e2", ["low", "low"]),
            ],
            alertas=[
                _alerta("a1", "triggered", "warning"),
                _alerta("a2", "informational", "info"),
            ],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_reporta_una_metrica_base_por_grupo(self):
        reporte = self.artefactos.calcular()
        metricas = metricas_por_grupo(reporte)
        self.assertEqual(list(metricas), list(GRUPOS))
        self.assertEqual(reporte["schema"], "p6-07/1")
        self.assertEqual(reporte["timestamp"], INSTANTE)
        for grupo, metrica in metricas.items():
            self.assertEqual(metrica["id"], grupo)
            self.assertTrue(metrica["denominador_explicito"])
            self.assertTrue(metrica["zero_safe"])
            self.assertEqual(metrica["timestamp"], INSTANTE)
            self.assertIn("formula", metrica)
            self.assertIn("poblacion", metrica)
            self.assertIn("ventana", metrica)
            self.assertIn("exclusiones", metrica)
            self.assertIn("ausencias", metrica)

    def test_g1_errores_de_inferencia(self):
        g1 = self.artefactos.calcular()["metricas"]["G1"]
        self.assertEqual((g1["numerador"], g1["denominador"]), (1, 2))
        self.assertEqual(g1["valor"], 0.5)
        self.assertEqual(g1["estado"], "ok")

    def test_g2_descartes_del_stream(self):
        g2 = self.artefactos.calcular()["metricas"]["G2"]
        self.assertEqual((g2["numerador"], g2["denominador"]), (2, 4))
        self.assertEqual(g2["valor"], 0.5)
        self.assertEqual(g2["estado"], "ok")

    def test_g3_disco_disponible(self):
        g3 = self.artefactos.calcular()["metricas"]["G3"]
        self.assertEqual(g3["numerador"], g3["evidencia"]["free_bytes"])
        self.assertEqual(g3["denominador"], g3["evidencia"]["total_bytes"])
        self.assertGreaterEqual(g3["valor"], 0.0)
        self.assertLessEqual(g3["valor"], 1.0)

    def test_g4_baja_confianza(self):
        g4 = self.artefactos.calcular()["metricas"]["G4"]
        self.assertEqual((g4["numerador"], g4["denominador"]), (3, 5))
        self.assertEqual(g4["valor"], 0.6)
        self.assertEqual(
            g4["evidencia"]["zonas"], {"accepted": 1, "doubt": 1, "low": 3}
        )

    def test_g5_trazabilidad(self):
        g5 = self.artefactos.calcular()["metricas"]["G5"]
        self.assertEqual((g5["numerador"], g5["denominador"]), (2, 2))
        self.assertEqual(g5["valor"], 1.0)

    def test_g6_antiguedad_del_ultimo_reporte(self):
        g6 = self.artefactos.calcular()["metricas"]["G6"]
        self.assertEqual(g6["numerador"], 27.0)
        self.assertEqual(g6["denominador"], 1)
        self.assertEqual(g6["valor"], 27.0)
        self.assertEqual(g6["unidad"], "segundos")

    def test_g7_alertas_triggered(self):
        g7 = self.artefactos.calcular()["metricas"]["G7"]
        self.assertEqual((g7["numerador"], g7["denominador"]), (1, 2))
        self.assertEqual(g7["valor"], 0.5)
        self.assertEqual(g7["evidencia"]["alertas"], 2)


class CeroSeguro(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_denominadores_cero_no_dividen_y_reportan_sin_datos(self):
        artefactos = Artefactos(self.base).escribir(
            counts={
                "frames_received": 0,
                "frames_sampled": 0,
                "frames_processed": 0,
                "frames_discarded": 0,
                "detector_errors": 0,
                "detector_calls": 0,
            },
            recibidos=(),
            eventos=[],
            alertas=[],
        )
        reporte = artefactos.calcular()
        for grupo in ("G1", "G2", "G4", "G5", "G6", "G7"):
            metrica = reporte["metricas"][grupo]
            self.assertIsNone(metrica["valor"], grupo)
            self.assertEqual(metrica["estado"], "sin_datos", grupo)
            self.assertTrue(metrica["ausencias"], grupo)

    def test_numerador_cero_con_denominador_positivo_es_cero_seguro(self):
        artefactos = Artefactos(self.base).escribir(
            counts={
                "frames_received": 4,
                "frames_sampled": 4,
                "frames_processed": 4,
                "frames_discarded": 0,
                "detector_errors": 0,
                "detector_calls": 4,
            },
            eventos=[_evento("e1", ["accepted"])],
            alertas=[_alerta("a1", "informational", "info")],
        )
        reporte = artefactos.calcular()
        self.assertEqual(reporte["metricas"]["G1"]["valor"], 0.0)
        self.assertEqual(reporte["metricas"]["G2"]["valor"], 0.0)
        self.assertEqual(reporte["metricas"]["G4"]["valor"], 0.0)
        self.assertEqual(reporte["metricas"]["G7"]["valor"], 0.0)
        for grupo in ("G1", "G2", "G4", "G7"):
            self.assertEqual(reporte["metricas"][grupo]["estado"], "ok", grupo)

    def test_g3_sin_disco_reporta_sin_datos(self):
        artefactos = Artefactos(self.base).escribir()
        with mock.patch("metricas.shutil.disk_usage", side_effect=OSError("sin disco")):
            g3 = artefactos.calcular()["metricas"]["G3"]
        self.assertIsNone(g3["valor"])
        self.assertEqual(g3["estado"], "sin_datos")
        self.assertIn("sin disco", g3["ausencias"][0])

    def test_sin_resumen_deriva_conteos_de_las_observaciones(self):
        cabecera = {"record": "cabecera", "schema": "p6-03/1", "run_id": "run-test"}
        observaciones = [
            {
                "record": "observacion",
                "run_id": "run-test",
                "received_at": "2026-11-14T04:25:20+00:00",
                "source_status": "sampled",
                "status": estado,
            }
            for estado in ("processed", "detector_error", "discarded")
        ]
        _escribir(
            self.base / "sin-resumen.jsonl",
            [cabecera, *observaciones],
        )
        artefactos = Artefactos(self.base).escribir()
        reporte = calcular_metricas(
            ruta_observaciones=self.base / "sin-resumen.jsonl",
            ruta_eventos=artefactos.eventos,
            ruta_alertas=artefactos.alertas,
            generated_at=INSTANTE,
        )
        self.assertEqual(reporte["metricas"]["G1"]["denominador"], 3)
        self.assertEqual(reporte["metricas"]["G1"]["numerador"], 1)
        self.assertEqual(reporte["metricas"]["G2"]["denominador"], 3)
        self.assertEqual(reporte["metricas"]["G2"]["numerador"], 1)



class ErroresDeEntrada(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.artefactos = Artefactos(self.base).escribir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_ruta_inexistente_es_error_explicito(self):
        with self.assertRaises(ErrorMetricas):
            calcular_metricas(
                ruta_observaciones=self.base / "no-existe.jsonl",
                ruta_eventos=self.artefactos.eventos,
                ruta_alertas=self.artefactos.alertas,
                generated_at=INSTANTE,
            )

    def test_jsonl_malformado_es_error_explicito(self):
        roto = self.base / "roto.jsonl"
        roto.write_text('{"record": "observacion"}\n{no-json}\n', encoding="utf-8")
        with self.assertRaises(ErrorMetricas) as contexto:
            leer_jsonl(roto)
        self.assertIn("linea 2", str(contexto.exception))

    def test_cli_devuelve_error_sin_traceback(self):
        salida = io.StringIO()
        with contextlib.redirect_stderr(salida):
            codigo = main(
                [
                    "--observaciones",
                    str(self.base / "no-existe.jsonl"),
                    "--eventos",
                    str(self.artefactos.eventos),
                    "--alertas",
                    str(self.artefactos.alertas),
                ]
            )
        self.assertEqual(codigo, 1)
        self.assertIn("ErrorMetricas", salida.getvalue())


class RecalculoManual(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.artefactos = Artefactos(self.base).escribir(
            counts={
                "frames_received": 7,
                "frames_sampled": 5,
                "frames_processed": 4,
                "frames_discarded": 2,
                "detector_errors": 1,
                "detector_calls": 5,
            },
            recibidos=(
                "2026-11-14T04:25:10+00:00",
                "2026-11-14T04:25:30+00:00",
            ),
            eventos=[
                _evento("e1", ["accepted", "low"]),
                _evento("e2", ["low", "low", "doubt", "accepted"]),
                _evento("e3", ["accepted", "low", "low"], capture=False),
            ],
            alertas=[
                _alerta("a1", "triggered", "warning"),
                _alerta("a2", "triggered", "critical"),
                _alerta("a3", "informational", "info"),
            ],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_recalculo_manual_coincide_con_el_reporte(self):
        reporte = self.artefactos.calcular()
        esperado = {
            "G1": (1, 5, 0.2),
            "G2": (2, 7, 0.285714),
            "G4": (5, 9, 0.555556),
            "G5": (2, 3, 0.666667),
            "G6": (30.0, 1, 30.0),
            "G7": (2, 3, 0.666667),
        }
        for grupo, (numerador, denominador, valor) in esperado.items():
            metrica = reporte["metricas"][grupo]
            self.assertEqual(metrica["numerador"], numerador, grupo)
            self.assertEqual(metrica["denominador"], denominador, grupo)
            self.assertEqual(metrica["valor"], valor, grupo)
        self.assertEqual(
            reporte["metricas"]["G3"]["denominador"],
            reporte["metricas"]["G3"]["evidencia"]["total_bytes"],
        )

    def test_recalculo_con_mismo_instante_es_reproducible(self):
        primero = self.artefactos.calcular()
        segundo = self.artefactos.calcular()
        for grupo in ("G1", "G2", "G4", "G5", "G6", "G7"):
            self.assertEqual(
                primero["metricas"][grupo], segundo["metricas"][grupo], grupo
            )
        self.assertEqual(primero["inputs"], segundo["inputs"])

    def test_cli_escribe_json_con_timestamp(self):
        salida = self.base / "reporte.json"
        with contextlib.redirect_stdout(io.StringIO()):
            codigo = main(
                [
                    "--observaciones",
                    str(self.artefactos.observaciones),
                    "--eventos",
                    str(self.artefactos.eventos),
                    "--alertas",
                    str(self.artefactos.alertas),
                    "--root",
                    str(self.base),
                    "--generated-at",
                    INSTANTE,
                    "--output",
                    str(salida),
                ]
            )
        self.assertEqual(codigo, 0)
        reporte = json.loads(salida.read_text(encoding="utf-8"))
        self.assertEqual(reporte["timestamp"], INSTANTE)
        self.assertEqual(len(reporte["metricas"]), 7)

