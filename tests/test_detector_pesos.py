"""Pruebas de descarga, tamano y SHA256 de los pesos, sin red real."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2

from detector_yolox import (
    SHA256_PESOS,
    TAMANO_PESOS,
    URL_PESOS,
    ErrorDetector,
    ErrorPesos,
    _descargar_pesos,
    asegurar_pesos,
    cargar_detector,
    sha256_archivo,
    verificar_pesos,
)

CONTENIDO = b"modelo de prueba" * 1000
SHA_CONTENIDO = hashlib.sha256(CONTENIDO).hexdigest()


class RespuestaFalsa:
    def __init__(self, cuerpo: bytes) -> None:
        self.cuerpo = cuerpo
        self.posicion = 0

    def read(self, cantidad: int = -1) -> bytes:
        if self.posicion >= len(self.cuerpo):
            return b""
        if cantidad < 0:
            cantidad = len(self.cuerpo) - self.posicion
        bloque = self.cuerpo[self.posicion : self.posicion + cantidad]
        self.posicion += len(bloque)
        return bloque

    def __enter__(self) -> RespuestaFalsa:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class PesosTests(unittest.TestCase):
    def test_constantes_identifican_el_artefacto_publicado(self) -> None:
        self.assertEqual(
            URL_PESOS,
            "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx",
        )
        self.assertEqual(TAMANO_PESOS, 20219662)
        self.assertEqual(
            SHA256_PESOS, "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"
        )

    def test_sha256_de_un_archivo(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            archivo = Path(temporal) / "pesos.onnx"
            archivo.write_bytes(CONTENIDO)
            self.assertEqual(sha256_archivo(archivo), SHA_CONTENIDO)

    def test_verificar_acepta_tamano_y_sha_correctos(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            archivo = Path(temporal) / "pesos.onnx"
            archivo.write_bytes(CONTENIDO)
            digest = verificar_pesos(
                archivo, sha256_esperado=SHA_CONTENIDO, tamano_esperado=len(CONTENIDO)
            )
        self.assertEqual(digest, SHA_CONTENIDO)

    def test_verificar_rechaza_archivo_ausente_tamano_o_sha_incorrectos(self) -> None:
        casos = (
            (None, SHA_CONTENIDO, len(CONTENIDO), "not found"),
            (CONTENIDO, SHA_CONTENIDO, len(CONTENIDO) + 1, "size"),
            (CONTENIDO, "0" * 64, len(CONTENIDO), "sha256"),
        )
        for contenido, sha_esperado, tamano, mensaje in casos:
            with self.subTest(mensaje=mensaje), tempfile.TemporaryDirectory() as temporal:
                archivo = Path(temporal) / "pesos.onnx"
                if contenido is not None:
                    archivo.write_bytes(contenido)
                with self.assertRaisesRegex(ErrorPesos, mensaje):
                    verificar_pesos(
                        archivo, sha256_esperado=sha_esperado, tamano_esperado=tamano
                    )

    def test_asegurar_pesos_usa_el_archivo_valido_sin_descargar(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            archivo = Path(temporal) / "pesos.onnx"
            archivo.write_bytes(CONTENIDO)
            with patch("detector_yolox.verificar_pesos") as verificar, patch(
                "detector_yolox._descargar_pesos"
            ) as descargar:
                resultado = asegurar_pesos(archivo)
        self.assertEqual(resultado, archivo)
        verificar.assert_called_once_with(archivo)
        descargar.assert_not_called()

    def test_asegurar_pesos_sin_archivo_y_sin_descarga_falla(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            archivo = Path(temporal) / "pesos.onnx"
            with self.assertRaisesRegex(ErrorPesos, "download disabled"):
                asegurar_pesos(archivo, descargar=False)
            self.assertFalse(archivo.exists())

    def test_asegurar_pesos_conserva_un_archivo_corrupto_si_no_descarga(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            archivo = Path(temporal) / "pesos.onnx"
            archivo.write_bytes(b"corrupto")
            with self.assertLogs("detector_yolox", level="WARNING"):
                with self.assertRaisesRegex(ErrorPesos, "download disabled"):
                    asegurar_pesos(archivo, descargar=False)
            self.assertEqual(archivo.read_bytes(), b"corrupto")

    def test_descarga_escribe_de_forma_atomica_sin_dejar_temporales(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            archivo = Path(temporal) / "modelos" / "pesos.onnx"
            with patch(
                "detector_yolox.urllib.request.urlopen", return_value=RespuestaFalsa(CONTENIDO)
            ), patch("detector_yolox.verificar_pesos"):
                _descargar_pesos(archivo)

            self.assertEqual(archivo.read_bytes(), CONTENIDO)
            self.assertEqual(sorted(p.name for p in archivo.parent.iterdir()), ["pesos.onnx"])

    def test_descarga_rechazada_no_deja_archivo_ni_temporal(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            archivo = Path(temporal) / "pesos.onnx"
            with patch(
                "detector_yolox.urllib.request.urlopen", return_value=RespuestaFalsa(CONTENIDO)
            ), patch("detector_yolox.verificar_pesos", side_effect=ErrorPesos("sha incorrecto")):
                with self.assertRaisesRegex(ErrorPesos, "verification failed"):
                    _descargar_pesos(archivo)

            self.assertEqual(list(Path(temporal).iterdir()), [])

    def test_error_de_red_no_deja_archivo(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            archivo = Path(temporal) / "pesos.onnx"
            with patch(
                "detector_yolox.urllib.request.urlopen", side_effect=OSError("sin red")
            ):
                with self.assertRaisesRegex(ErrorPesos, "download failed"):
                    _descargar_pesos(archivo)

            self.assertEqual(list(Path(temporal).iterdir()), [])


class RedFalsa:
    def __init__(self, salidas: tuple[str, ...] = ("output",)) -> None:
        self.salidas = salidas
        self.backend: int | None = None
        self.target: int | None = None

    def setPreferableBackend(self, backend: int) -> None:
        self.backend = backend

    def setPreferableTarget(self, target: int) -> None:
        self.target = target

    def getUnconnectedOutLayersNames(self):
        return list(self.salidas)


class CargaTests(unittest.TestCase):
    def test_cargar_detector_configura_opencv_dnn_en_cpu(self) -> None:
        red = RedFalsa()
        with tempfile.TemporaryDirectory() as temporal:
            archivo = Path(temporal) / "pesos.onnx"
            with patch("detector_yolox.asegurar_pesos", return_value=archivo), patch(
                "detector_yolox.cv2.dnn.readNetFromONNX", return_value=red
            ):
                detector = cargar_detector(
                    archivo, score_threshold=0.2, nms_threshold=0.5, descargar=False
                )

        self.assertEqual(red.backend, cv2.dnn.DNN_BACKEND_OPENCV)
        self.assertEqual(red.target, cv2.dnn.DNN_TARGET_CPU)
        self.assertEqual(detector.score_threshold, 0.2)
        self.assertEqual(detector.nms_threshold, 0.5)

    def test_cargar_detector_exige_una_sola_salida(self) -> None:
        red = RedFalsa(("a", "b"))
        with tempfile.TemporaryDirectory() as temporal:
            archivo = Path(temporal) / "pesos.onnx"
            with patch("detector_yolox.asegurar_pesos", return_value=archivo), patch(
                "detector_yolox.cv2.dnn.readNetFromONNX", return_value=red
            ):
                with self.assertRaisesRegex(ErrorDetector, "one output"):
                    cargar_detector(archivo, descargar=False)

    def test_cargar_detector_valida_umbral_antes_de_tocar_los_pesos(self) -> None:
        with patch("detector_yolox.asegurar_pesos") as asegurar:
            with self.assertRaises(ValueError):
                cargar_detector("pesos.onnx", score_threshold=1.5)
        asegurar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
