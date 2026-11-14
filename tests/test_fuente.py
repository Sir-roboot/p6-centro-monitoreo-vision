"""Pruebas de lectura, muestreo y estados de la fuente visual."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from fuente import leer_fuente
from generar_secuencia_demo import generar


class FuenteTests(unittest.TestCase):
    def test_secuencia_y_muestreo(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "demo"
            generar(carpeta, cantidad=7)
            observaciones = list(leer_fuente(str(carpeta), source_id="demo", sample_every=3, interval_ms=0))

        self.assertEqual([o.status for o in observaciones], [
            "sampled", "discarded", "discarded", "sampled",
            "discarded", "discarded", "sampled", "end_of_stream",
        ])
        self.assertEqual([o.frame_id for o in observaciones[:7]], [f"demo-{n:06d}" for n in range(1, 8)])
        self.assertTrue(all(o.image is None for o in observaciones if o.status == "discarded"))
        self.assertTrue(all(o.discard_reason == "sampling_interval" for o in observaciones if o.status == "discarded"))
        self.assertEqual(observaciones[0].to_dict()["width"], 320)
        self.assertNotIn("image", observaciones[0].to_dict())

    def test_termino_por_limite_no_se_confunde_con_eof(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "demo"
            generar(carpeta, cantidad=4)
            observaciones = list(leer_fuente(str(carpeta), source_id="demo", max_frames=2, interval_ms=0))
        self.assertEqual([o.status for o in observaciones], ["sampled", "sampled"])

    def test_imagen_invalida_se_identifica(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            (carpeta / "frame_0001.png").write_bytes(b"no es una imagen")
            observaciones = list(leer_fuente(str(carpeta), source_id="demo", interval_ms=0))
        self.assertEqual(observaciones[0].status, "read_error")
        self.assertEqual(observaciones[0].discard_reason, "invalid_image")
        self.assertEqual(observaciones[1].status, "end_of_stream")

    def test_fuente_ausente_y_secuencia_vacia(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            vacia = list(leer_fuente(str(carpeta), source_id="demo"))
            ausente = list(leer_fuente(str(carpeta / "ausente"), source_id="demo"))
        self.assertEqual(vacia[0].discard_reason, "empty_sequence")
        self.assertEqual(ausente[0].discard_reason, "source_not_found")

    def test_stream_fallido_no_se_confunde_con_fin_de_archivo(self) -> None:
        class CapturaFalsa:
            def __init__(self) -> None:
                self.lecturas = 0

            def isOpened(self) -> bool:
                return True

            def read(self):
                self.lecturas += 1
                if self.lecturas == 1:
                    return True, np.zeros((4, 6, 3), dtype=np.uint8)
                return False, None

            def release(self) -> None:
                pass

        with patch("fuente.cv2.VideoCapture", return_value=CapturaFalsa()):
            stream = list(leer_fuente("http://127.0.0.1:8765/stream.mjpg", source_id="vivo"))
        self.assertEqual([o.status for o in stream], ["sampled", "read_error"])
        self.assertEqual(stream[-1].discard_reason, "stream_read_failed")

        with tempfile.TemporaryDirectory() as temporal:
            archivo = Path(temporal) / "video.mp4"
            archivo.touch()
            with patch("fuente.cv2.VideoCapture", return_value=CapturaFalsa()):
                video = list(leer_fuente(str(archivo), source_id="archivo", interval_ms=0))
        self.assertEqual([o.status for o in video], ["sampled", "end_of_stream"])

    def test_configuracion_invalida(self) -> None:
        with self.assertRaises(ValueError):
            list(leer_fuente("inexistente", source_id="demo", sample_every=0))
        with self.assertRaises(ValueError):
            list(leer_fuente("inexistente", source_id="demo/extra"))

    def test_generador_no_sobrescribe(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            generar(carpeta, cantidad=2)
            with self.assertRaises(FileExistsError):
                generar(carpeta, cantidad=2)


if __name__ == "__main__":
    unittest.main()
