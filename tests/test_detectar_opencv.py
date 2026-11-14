"""Pruebas de la CLI progresiva que conecta la fuente con el detector."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import sys
import unittest
from unittest.mock import patch

import numpy as np

from detectar_opencv import main
from detector_yolox import Deteccion, ResultadoCuadro


def resultado(*, status: str = "ok", detections: tuple[Deteccion, ...] = ()) -> ResultadoCuadro:
    return ResultadoCuadro(
        source_id="demo",
        frame_id="demo-000001",
        frame_number=1,
        received_at=None,
        status=status,
        image_width=4,
        image_height=4,
        detections=detections,
        class_counts={"person": 1} if detections else {},
        error="boom" if status == "error" else None,
        timings_ms={"preprocess": 1.0, "inference": 2.0, "postprocess": 0.5, "total": 3.5},
        model={"name": "YOLOX-Tiny"},
        filters={"score_threshold": 0.1},
    )


class DetectorFalso:
    def __init__(self, respuesta: ResultadoCuadro | None = None) -> None:
        self.respuesta = respuesta or resultado()
        self.llamadas: list[dict] = []

    def detectar(self, _imagen, **kwargs):
        self.llamadas.append(kwargs)
        return self.respuesta


class ObservacionFalsa:
    def __init__(self, status: str, **extra) -> None:
        self.source_id = "demo"
        self.frame_id = "demo-000001"
        self.frame_number = 1
        self.received_at = None
        self.status = status
        self.discard_reason = None
        self.width = 4
        self.height = 4
        self.image = None
        for clave, valor in extra.items():
            setattr(self, clave, valor)

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "frame_id": self.frame_id,
            "frame_number": self.frame_number,
            "received_at": self.received_at,
            "status": self.status,
            "discard_reason": self.discard_reason,
            "width": self.width,
            "height": self.height,
        }


def observacion(status: str, **extra) -> ObservacionFalsa:
    return ObservacionFalsa(status, **extra)


class CliTests(unittest.TestCase):
    def ejecutar(self, detector: DetectorFalso, observaciones, *extra_args: str):
        salida = io.StringIO()
        argv = [
            "detectar_opencv.py",
            "--source",
            "demo",
            "--source-id",
            "demo",
            "--no-preview",
            *extra_args,
        ]
        with patch.object(sys, "argv", argv), patch(
            "detectar_opencv.leer_fuente", return_value=iter(observaciones)
        ), patch("detectar_opencv.cargar_detector", return_value=detector), redirect_stdout(salida):
            codigo = main()
        lineas = [json.loads(linea) for linea in salida.getvalue().splitlines()]
        return codigo, lineas

    def test_solo_detecta_cuadros_sampled_y_resume_el_resto(self) -> None:
        detector = DetectorFalso(
            resultado(detections=(Deteccion(0, "person", 0.9, (0.0, 0.0, 4.0, 4.0)),))
        )
        observaciones = [
            observacion("sampled", image=np.zeros((4, 4, 3), dtype=np.uint8)),
            observacion("discarded", discard_reason="sampling_interval"),
            observacion("end_of_stream"),
        ]

        codigo, lineas = self.ejecutar(detector, observaciones)

        self.assertEqual(codigo, 0)
        self.assertEqual(len(detector.llamadas), 1)
        self.assertEqual(detector.llamadas[0]["frame_id"], "demo-000001")
        self.assertEqual(lineas[0]["status"], "ok")
        self.assertEqual(lineas[0]["detections"][0]["class_name"], "person")
        resumen = lineas[-1]["summary"]
        self.assertEqual(resumen["source"], {"received": 2, "sampled": 1, "discarded": 1, "read_errors": 0})
        self.assertEqual(resumen["detector"], {"ok": 1, "error": 0})
        self.assertEqual(resumen["detections"], 1)
        self.assertEqual(resumen["class_counts"], {"person": 1})
        self.assertEqual(resumen["end_state"], "end_of_stream")

    def test_un_error_de_lectura_termina_con_codigo_uno(self) -> None:
        detector = DetectorFalso()
        observaciones = [
            observacion("read_error", discard_reason="stream_read_failed"),
        ]

        codigo, lineas = self.ejecutar(detector, observaciones)

        self.assertEqual(codigo, 1)
        self.assertEqual(detector.llamadas, [])
        self.assertEqual(lineas[-1]["summary"]["source"]["read_errors"], 1)
        self.assertEqual(lineas[-1]["summary"]["end_state"], "stream_read_failed")

    def test_un_error_del_detector_termina_con_codigo_uno(self) -> None:
        detector = DetectorFalso(resultado(status="error"))
        observaciones = [
            observacion("sampled", image=np.zeros((4, 4, 3), dtype=np.uint8)),
            observacion("end_of_stream"),
        ]

        codigo, lineas = self.ejecutar(detector, observaciones)

        self.assertEqual(codigo, 1)
        self.assertEqual(lineas[0]["status"], "error")
        self.assertEqual(lineas[-1]["summary"]["detector"], {"ok": 0, "error": 1})
        self.assertEqual(lineas[-1]["summary"]["detections"], 0)


if __name__ == "__main__":
    unittest.main()
