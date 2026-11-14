"""P6-06: reglas explicitas, alertas persistidas y evidencia verificable.

Las corridas usan el detector falso de P6-05 para fijar puntuaciones reales
persistidas. P6-06 no vuelve a inferir ni reclasifica ninguna puntuacion.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from alertas import (
    ErrorAlertas,
    RULE_BAJA_CONFIANZA,
    RULE_CAMBIO_SOSTENIDO,
    RULE_EVIDENCIA_INSUFICIENTE,
    construir_alertas,
    consultar_alertas,
    ejecutar_alertas,
    leer_manifiesto_alertas,
    listar_runs_con_alertas,
    resumen_alertas,
    verificar_evidencia_alertas,
)
from detector_yolox import Deteccion, ResultadoCuadro
from ejecutar_eventos import ejecutar_monitoreo
from fuente import ObservacionFuente
from observaciones import ejecutar_pipeline

MODELO = {
    "name": "YOLOX-Tiny", "version": "test", "sha256": "a" * 64,
    "classes": {"count": 80},
}
FILTROS = {"score_threshold": 0.1, "nms_threshold": 0.45}
INSTANTE = "2026-11-13T12:00:00+00:00"


class DetectorSecuencia:
    model = MODELO
    filters = FILTROS

    def __init__(self, scores: dict[int, tuple[float, ...]]):
        self.scores = scores

    def detectar(self, imagen, *, source_id, frame_id, frame_number, received_at):
        detecciones = tuple(
            Deteccion(2, "car", score, (1.0, 1.0, 10.0, 10.0))
            for score in self.scores.get(frame_number, ())
        )
        return ResultadoCuadro(
            source_id=source_id, frame_id=frame_id, frame_number=frame_number,
            received_at=received_at, status="ok", image_width=32, image_height=24,
            detections=detecciones,
            class_counts=dict(Counter(d.class_name for d in detecciones)),
            error=None,
            timings_ms={"preprocess": 0.1, "inference": 0.2, "postprocess": 0.1, "total": 0.4},
            model=self.model, filters=self.filters,
        )


def lector(total: int):
    def leer(source, *, source_id, sample_every, interval_ms, max_frames):
        for numero in range(1, total + 1):
            yield ObservacionFuente(
                source_id, f"{source_id}-{numero:06d}", numero, INSTANTE,
                "sampled", None, 32, 24,
                np.full((24, 32, 3), numero * 20, dtype=np.uint8),
            )
        yield ObservacionFuente(
            source_id, None, None, None, "end_of_stream", None, None, None
        )
    return leer


class AlertasTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.root = Path(self.temporal.name)
        self.ref = self.root / "referencia.jsonl"
        ejecutar_pipeline(
            "referencia.mp4",
            DetectorSecuencia({n: (0.9, 0.9) for n in range(1, 6)}),
            source_id="test", salida=self.ref, run_id="ref",
            interval_ms=0, lector=lector(5),
        )

    def _monitorear(self, run_id: str, scores: dict[int, tuple[float, ...]]):
        return ejecutar_monitoreo(
            "video.mp4", DetectorSecuencia(scores),
            source_id="test", root=self.root, reference=self.ref,
            sample_every=1, interval_ms=0, run_id=run_id,
            lector=lector(len(scores)),
        )

    def _asignar(self, alertas):
        return {
            regla: [a for a in alertas if a["rule_id"] == regla]
            for regla in (
                RULE_BAJA_CONFIANZA,
                RULE_CAMBIO_SOSTENIDO,
                RULE_EVIDENCIA_INSUFICIENTE,
            )
        }

    def test_dispara_baja_confianza_y_cambio_sostenido(self):
        resultado = self._monitorear(
            "sostenido", {n: (0.15,) for n in range(1, 11)}
        )
        self.assertEqual(resultado["policy_status"], "sustained_change")
        leido = leer_manifiesto_alertas(self.root, "sostenido")
        alertas = leido["alerts"]
        por_regla = self._asignar(alertas)
        self.assertEqual(len(por_regla[RULE_BAJA_CONFIANZA]), 10)
        self.assertEqual(len(por_regla[RULE_CAMBIO_SOSTENIDO]), 1)
        self.assertEqual(len(por_regla[RULE_EVIDENCIA_INSUFICIENTE]), 0)
        self.assertTrue(
            all(a["severity"] == "warning" for a in por_regla[RULE_BAJA_CONFIANZA])
        )
        sostenida = por_regla[RULE_CAMBIO_SOSTENIDO][0]
        self.assertEqual(sostenida["severity"], "critical")
        self.assertEqual(sostenida["window"]["policy_window_indices"], [1, 2])
        self.assertTrue(sostenida["evidence"]["depends_on_sustained_change"])
        self.assertEqual(sostenida["policy_status"], "sustained_change")

    def test_baja_confianza_no_depende_del_cambio_sostenido(self):
        scores = {n: (0.15,) for n in range(1, 6)}
        scores.update({n: (0.9,) for n in range(6, 11)})
        resultado = self._monitorear("independiente", scores)
        self.assertEqual(resultado["policy_status"], "no_sustained_change")
        alertas = consultar_alertas(self.root, "independiente")
        por_regla = self._asignar(alertas)
        self.assertEqual(len(por_regla[RULE_BAJA_CONFIANZA]), 5)
        self.assertEqual(len(por_regla[RULE_CAMBIO_SOSTENIDO]), 0)
        self.assertEqual(len(por_regla[RULE_EVIDENCIA_INSUFICIENTE]), 0)
        for alerta in por_regla[RULE_BAJA_CONFIANZA]:
            self.assertEqual(alerta["policy_status"], "no_sustained_change")
            self.assertIs(alerta["evidence"]["depends_on_sustained_change"], False)

    def test_insuficiencia_no_es_normalidad(self):
        self._monitorear("corta", {1: (0.9,), 2: (0.9,)})
        alertas = consultar_alertas(self.root, "corta")
        por_regla = self._asignar(alertas)
        self.assertEqual(len(por_regla[RULE_BAJA_CONFIANZA]), 0)
        self.assertEqual(len(por_regla[RULE_CAMBIO_SOSTENIDO]), 0)
        self.assertEqual(len(por_regla[RULE_EVIDENCIA_INSUFICIENTE]), 1)
        alerta = por_regla[RULE_EVIDENCIA_INSUFICIENTE][0]
        self.assertEqual(alerta["severity"], "info")
        self.assertIs(alerta["evidence_sufficient"], False)
        self.assertIs(alerta["normality_established"], False)
        self.assertIs(alerta["evidence"]["insufficiency_is_not_normality"], True)
        self.assertIn("no acredita normalidad", alerta["reason"])

    def test_sin_alertas_cuando_todo_es_aceptado(self):
        resultado = self._monitorear(
            "aceptada", {n: (0.9,) for n in range(1, 11)}
        )
        self.assertEqual(resultado["policy_status"], "no_sustained_change")
        leido = leer_manifiesto_alertas(self.root, "aceptada")
        self.assertEqual(leido["manifest"]["alert_count"], 0)
        self.assertEqual(leido["alerts"], [])
        self.assertEqual(
            leido["manifest"]["counts_by_rule"],
            {
                RULE_BAJA_CONFIANZA: 0,
                RULE_CAMBIO_SOSTENIDO: 0,
                RULE_EVIDENCIA_INSUFICIENTE: 0,
            },
        )
        verificacion = verificar_evidencia_alertas(self.root, "aceptada")
        self.assertTrue(verificacion["ok"])
        self.assertEqual(verificacion["alert_count"], 0)

    def test_persistencia_resumen_y_verificacion(self):
        resultado = self._monitorear(
            "reinicio", {n: (0.15,) for n in range(1, 11)}
        )
        self.assertTrue(Path(resultado["alerts"]).is_file())
        self.assertEqual(resultado["alert_count"], 11)
        resumen = resumen_alertas(self.root, "reinicio")
        self.assertEqual(resumen["alert_count"], 11)
        self.assertEqual(resumen["counts_by_rule"][RULE_BAJA_CONFIANZA], 10)
        self.assertEqual(resumen["counts_by_rule"][RULE_CAMBIO_SOSTENIDO], 1)
        self.assertTrue(resumen["not_expiring"])
        self.assertEqual(resumen["event_count"], 10)
        verificacion = verificar_evidencia_alertas(self.root, "reinicio")
        self.assertTrue(verificacion["ok"])
        self.assertEqual(verificacion["checked_events"], 10)
        self.assertEqual(verificacion["checked_captures"], 10)
        self.assertEqual(verificacion["failures"], [])
        self.assertIn("reinicio", listar_runs_con_alertas(self.root))

    def test_no_sobrescribe_alertas_de_una_corrida(self):
        self._monitorear("unica", {n: (0.15,) for n in range(1, 11)})
        with self.assertRaises(FileExistsError):
            ejecutar_alertas(self.root, "unica")
        self.assertEqual(len(consultar_alertas(self.root, "unica")), 11)

    def test_generacion_es_determinista_y_deduplica(self):
        self._monitorear("dedupe", {n: (0.15,) for n in range(1, 11)})
        primero = construir_alertas(
            self.root, "dedupe", generado_en="2026-11-13T12:00:00+00:00"
        )
        segundo = construir_alertas(
            self.root, "dedupe", generado_en="2026-11-13T12:00:00+00:00"
        )
        self.assertEqual(primero["alerts"], segundo["alerts"])
        self.assertEqual(
            primero["manifest"]["alerts_digest"],
            segundo["manifest"]["alerts_digest"],
        )
        claves = [alerta["dedupe_key"] for alerta in primero["alerts"]]
        self.assertEqual(len(claves), len(set(claves)))
        self.assertEqual(
            len({alerta["alert_id"] for alerta in primero["alerts"]}),
            len(primero["alerts"]),
        )

    def test_archivo_corrupto_se_rechaza(self):
        resultado = self._monitorear(
            "corrupta", {n: (0.15,) for n in range(1, 11)}
        )
        ruta = Path(resultado["alerts"])
        lineas = ruta.read_text(encoding="utf-8").splitlines()
        primera = json.loads(lineas[1])
        primera["severity"] = "critical"
        lineas[1] = json.dumps(primera, ensure_ascii=False, sort_keys=True)
        ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        with self.assertRaises(ErrorAlertas):
            consultar_alertas(self.root, "corrupta")

    def test_rechaza_run_id_inseguro_y_corrida_inexistente(self):
        with self.assertRaises(ErrorAlertas):
            consultar_alertas(self.root, "../otra")
        with self.assertRaises(ErrorAlertas):
            construir_alertas(self.root, "no-existe")

    def test_cli_genera_y_consulta(self):
        resultado = self._monitorear(
            "cli", {n: (0.15,) for n in range(1, 11)}
        )
        Path(resultado["alerts"]).unlink()
        raiz = Path(__file__).resolve().parents[1]
        generacion = subprocess.run(
            [
                sys.executable, str(raiz / "ejecutar_alertas.py"),
                "--root", str(self.root), "--run-id", "cli",
            ],
            cwd=raiz, capture_output=True, text=True, check=False,
        )
        self.assertEqual(generacion.returncode, 0, generacion.stderr)
        salida = json.loads(generacion.stdout)
        self.assertEqual(salida["summary"]["alert_count"], 11)
        consulta = subprocess.run(
            [
                sys.executable, str(raiz / "consultar_alertas.py"),
                "--root", str(self.root), "--run-id", "cli", "--verify",
                "--rule-id", RULE_CAMBIO_SOSTENIDO,
            ],
            cwd=raiz, capture_output=True, text=True, check=False,
        )
        self.assertEqual(consulta.returncode, 0, consulta.stderr)
        cuerpo = json.loads(consulta.stdout)
        self.assertEqual(cuerpo["total"], 1)
        self.assertTrue(cuerpo["verification"]["ok"])
        self.assertEqual(cuerpo["alerts"][0]["rule_id"], RULE_CAMBIO_SOSTENIDO)


if __name__ == "__main__":
    unittest.main()
