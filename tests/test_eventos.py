"""P6-05: eventos y JPEG reales de la fuente, sin red ni pesos descargados."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np

from detector_yolox import Deteccion, ResultadoCuadro
from ejecutar_eventos import ejecutar_monitoreo
from eventos import ErrorEventos, abrir_captura, consultar_eventos, persistir_eventos
from fuente import ObservacionFuente
from observaciones import ejecutar_pipeline
from politica_confianza import evaluar_archivo, escribir_politica


MODELO = {
    "name": "YOLOX-Tiny", "version": "test", "sha256": "a" * 64,
    "classes": {"count": 80},
}
FILTROS = {"score_threshold": 0.1, "nms_threshold": 0.45}
INSTANTE = "2026-11-13T12:00:00+00:00"


class DetectorPrueba:
    model = MODELO
    filters = FILTROS

    def __init__(self, scores: dict[int, tuple[float, ...]], error_frame: int | None = None):
        self.scores = scores
        self.error_frame = error_frame

    def detectar(self, imagen, *, source_id, frame_id, frame_number, received_at):
        if frame_number == self.error_frame:
            raise RuntimeError("fallo de detector controlado")
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


def lector_falso(source, *, source_id, sample_every, interval_ms, max_frames):
    for numero in range(1, 5):
        imagen = np.full((24, 32, 3), numero * 25, dtype=np.uint8)
        yield ObservacionFuente(
            source_id, f"{source_id}-{numero:06d}", numero, INSTANTE,
            "sampled", None, 32, 24, imagen,
        )
    yield ObservacionFuente(source_id, None, None, None, "end_of_stream", None, None, None)


def lector_referencia(source, *, source_id, sample_every, interval_ms, max_frames):
    for numero in range(1, 4):
        yield ObservacionFuente(
            source_id, f"{source_id}-{numero:06d}", numero, INSTANTE,
            "sampled", None, 32, 24,
            np.full((24, 32, 3), 100, dtype=np.uint8),
        )
    yield ObservacionFuente(source_id, None, None, None, "end_of_stream", None, None, None)


def lector_con_descarte_y_error(source, *, source_id, sample_every, interval_ms, max_frames):
    yield ObservacionFuente(
        source_id, f"{source_id}-000001", 1, INSTANTE,
        "sampled", None, 32, 24, np.full((24, 32, 3), 80, dtype=np.uint8),
    )
    yield ObservacionFuente(
        source_id, f"{source_id}-000002", 2, INSTANTE,
        "discarded", "sampling_interval", 32, 24,
        np.full((24, 32, 3), 90, dtype=np.uint8),
    )
    yield ObservacionFuente(
        source_id, f"{source_id}-000003", 3, INSTANTE,
        "read_error", "invalid_image", None, None, None,
    )
    yield ObservacionFuente(source_id, None, None, None, "end_of_stream", None, None, None)


class EventosTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.root = Path(self.temporal.name)
        self.ref = self.root / "referencia.jsonl"
        ejecutar_pipeline(
            "referencia.mp4", DetectorPrueba({1: (0.9, 0.9), 2: (0.9, 0.9), 3: (0.9, 0.9)}),
            source_id="test", salida=self.ref, run_id="ref",
            interval_ms=0, lector=lector_referencia,
        )

    def _ejecutar(self):
        return ejecutar_monitoreo(
            "video.mp4", DetectorPrueba({1: (0.9,), 2: (0.15,), 3: ()}, error_frame=4),
            source_id="test", root=self.root, reference=self.ref,
            lower=0.3, upper=0.7, threshold_justification="referencia de seis scores",
            sample_every=1, interval_ms=0, run_id="escena",
            lector=lector_falso,
        )

    def test_eventos_reinician_y_capturas_resuelven(self):
        resultado = self._ejecutar()
        self.assertEqual(resultado["technical_result"], "with_errors")
        eventos = consultar_eventos(self.root, "escena")
        self.assertEqual([e["status"] for e in eventos], [
            "scored", "scored", "no_detections", "technical_error",
        ])
        self.assertEqual([e["zone"] for e in eventos], ["accepted", "low", None, None])
        self.assertEqual([e["prediction"] for e in eventos][2:], [None, None])
        self.assertTrue(all(e["normality_established"] is False for e in eventos))
        for evento in eventos:
            datos = abrir_captura(self.root, evento)
            self.assertIsNotNone(cv2.imdecode(np.frombuffer(datos, np.uint8), cv2.IMREAD_COLOR))
            self.assertEqual(evento["source"]["id"], "test")
            self.assertEqual(evento["policy_id"], eventos[0]["policy_id"])
            self.assertEqual(evento["model"]["sha256"], MODELO["sha256"])

        # Un proceso nuevo consulta el manifiesto y verifica los cuatro JPEG.
        consulta = subprocess.run(
            [sys.executable, "consultar_eventos.py", "--root", str(self.root),
             "--run-id", "escena", "--verify-captures"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            check=True,
        )
        salida = json.loads(consulta.stdout)
        self.assertEqual(salida["total"], 4)
        self.assertEqual(len(salida["captures_verified"]), 4)

    def test_no_sobrescribe_una_corrida(self):
        self._ejecutar()
        with self.assertRaises(FileExistsError):
            self._ejecutar()

    def test_rechaza_puntuacion_de_politica_alterada(self):
        observaciones = self.root / "observaciones.jsonl"
        ejecutar_pipeline(
            "video.mp4", DetectorPrueba({1: (0.9,), 2: (0.15,), 3: ()}, error_frame=4),
            source_id="test", salida=observaciones, run_id="otra",
            interval_ms=0, lector=lector_falso,
        )
        politica = evaluar_archivo(observaciones, referencia=self.ref)
        politica["evaluation"]["scores"][0]["confidence"] = 0.999
        ruta_politica = self.root / "alterada.json"
        escribir_politica(politica, ruta_politica)
        with self.assertRaises(ErrorEventos):
            persistir_eventos(observaciones, ruta_politica, {}, root=self.root)

    def test_captura_corrupta_no_se_abre(self):
        self._ejecutar()
        evento = consultar_eventos(self.root, "escena")[0]
        ruta = self.root / evento["capture"]["path"]
        ruta.write_bytes(b"no es el JPEG original")
        with self.assertRaises(ErrorEventos):
            abrir_captura(self.root, evento)

    def test_descartado_no_es_evento_y_error_sin_pixeles_no_tiene_captura(self):
        resultado = ejecutar_monitoreo(
            "video.mp4", DetectorPrueba({1: (0.9,)}),
            source_id="test", root=self.root, reference=self.ref,
            sample_every=1, interval_ms=0, run_id="mixta",
            lector=lector_con_descarte_y_error,
        )
        self.assertEqual(resultado["event_count"], 2)
        eventos = consultar_eventos(self.root, "mixta")
        self.assertEqual([e["status"] for e in eventos], ["scored", "technical_error"])
        self.assertIsNotNone(eventos[0]["capture"])
        self.assertIsNone(eventos[1]["capture"])

    def test_consulta_rechaza_ruta_de_corrida_insegura(self):
        with self.assertRaises(ErrorEventos):
            consultar_eventos(self.root, "../otra")


if __name__ == "__main__":
    unittest.main()
