"""Pruebas del contrato, errores y geometria del detector YOLOX-Tiny."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import numpy as np

from detector_yolox import (
    CELDAS_SALIDA,
    CLASES_COCO,
    ErrorDetector,
    DetectorYolox,
    _nms,
    metadatos_filtros,
    metadatos_modelo,
    preprocesar,
)

PERSONA = CLASES_COCO.index("person")
PERRO = CLASES_COCO.index("dog")


class SesionFalsa:
    def __init__(self, raw: np.ndarray | None = None, error: Exception | None = None) -> None:
        self.raw = np.zeros((1, CELDAS_SALIDA, 85), dtype=np.float32) if raw is None else raw
        self.error = error
        self.tensores: list[np.ndarray] = []

    def inferir(self, tensor: np.ndarray) -> np.ndarray:
        self.tensores.append(tensor.copy())
        if self.error is not None:
            raise self.error
        return self.raw.copy()


def salida_cruda(*cajas: tuple[int, float, float, float, float, float, int, float]) -> np.ndarray:
    """Salida con cajas en pixeles de la entrada y celdas de stride 8."""
    raw = np.zeros((1, CELDAS_SALIDA, 85), dtype=np.float32)
    for celda, cx, cy, ancho, alto, objectness, clase, score in cajas:
        fila, columna = divmod(celda, 52)
        raw[0, celda, 0] = cx / 8 - columna
        raw[0, celda, 1] = cy / 8 - fila
        raw[0, celda, 2] = np.log(ancho / 8)
        raw[0, celda, 3] = np.log(alto / 8)
        raw[0, celda, 4] = objectness
        raw[0, celda, 5 + clase] = score
    return raw


def imagen(ancho: int = 416, alto: int = 416) -> np.ndarray:
    return np.full((alto, ancho, 3), 200, dtype=np.uint8)


def caja_casi_igual(caso: unittest.TestCase, obtenida, esperada) -> None:
    for valor, referencia in zip(obtenida, esperada, strict=True):
        caso.assertAlmostEqual(valor, referencia, places=4)


class GeometriaTests(unittest.TestCase):
    def test_preprocesar_usa_letterbox_abajo_derecha_sin_normalizar(self) -> None:
        pixeles = np.zeros((100, 200, 3), dtype=np.uint8)
        pixeles[..., 0] = 10
        pixeles[..., 1] = 20
        pixeles[..., 2] = 30

        tensor, ratio = preprocesar(pixeles)

        self.assertAlmostEqual(ratio, 2.08)
        self.assertEqual(tensor.shape, (3, 416, 416))
        self.assertEqual(tensor.dtype, np.float32)
        # BGR y valores 0-255, sin media ni desviacion.
        self.assertEqual(tensor[:, 0, 0].tolist(), [10.0, 20.0, 30.0])
        self.assertEqual(tensor[:, 207, 415].tolist(), [10.0, 20.0, 30.0])
        self.assertEqual(tensor[:, 208, 0].tolist(), [114.0, 114.0, 114.0])

    def test_preprocesar_conserva_al_menos_un_pixel_en_aspectos_extremos(self) -> None:
        tensor, _ = preprocesar(np.zeros((1, 5000, 3), dtype=np.uint8))
        self.assertEqual(tensor.shape, (3, 416, 416))

    def test_preprocesar_rechaza_imagenes_invalidas(self) -> None:
        for pixeles, mensaje in (
            (np.zeros((10, 10), dtype=np.uint8), "HxWx3"),
            (np.zeros((0, 10, 3), dtype=np.uint8), "HxWx3"),
            (np.zeros((10, 10, 3), dtype=np.float32), "uint8"),
            (None, "numpy array"),
        ):
            with self.subTest(mensaje=mensaje):
                with self.assertRaisesRegex(ErrorDetector, mensaje):
                    preprocesar(pixeles)  # type: ignore[arg-type]

    def test_nms_conserva_la_mejor_caja_y_separa_cajas_distintas(self) -> None:
        cajas = np.array(
            [
                [0.0, 0.0, 10.0, 10.0],
                [1.0, 1.0, 10.0, 10.0],
                [100.0, 100.0, 120.0, 120.0],
            ]
        )
        scores = np.array([0.9, 0.8, 0.7])

        self.assertEqual(_nms(cajas, scores, 0.45), [0, 2])
        self.assertEqual(_nms(cajas, scores, 0.0), [0, 2])
        self.assertEqual(_nms(np.empty((0, 4)), np.empty((0,)), 0.45), [])


class DetectorTests(unittest.TestCase):
    def test_metadatos_identifican_modelo_entrada_salida_y_clases(self) -> None:
        modelo = metadatos_modelo(incluir_clases=True)

        self.assertEqual(modelo["name"], "YOLOX-Tiny")
        self.assertEqual(modelo["dataset"], "COCO")
        self.assertEqual(modelo["version"], "0.1.1rc0")
        self.assertEqual(modelo["output"]["shape"], [1, 3549, 85])
        self.assertEqual(modelo["input"]["shape"], [1, 3, 416, 416])
        self.assertEqual(modelo["classes"]["count"], 80)
        self.assertEqual(modelo["classes"]["names"][0], "person")
        self.assertEqual(modelo["classes"]["names"][-1], "toothbrush")
        self.assertEqual(modelo["runtime"]["target"], "cpu")
        self.assertEqual(len(CLASES_COCO), 80)

    def test_metadatos_de_filtros_declaran_score_y_nms(self) -> None:
        filtros = metadatos_filtros(0.2, 0.5)

        self.assertEqual(filtros["score"], "objectness*class_probability")
        self.assertEqual(filtros["score_threshold"], 0.2)
        self.assertEqual(filtros["nms"], "class-agnostic")
        self.assertEqual(filtros["nms_threshold"], 0.5)
        self.assertEqual(filtros["input_size"], [416, 416])

    def test_detecta_clase_confianza_y_caja_en_pixeles_originales(self) -> None:
        raw = salida_cruda((0, 104, 104, 52, 104, 0.9, PERSONA, 0.8))
        detector = DetectorYolox(SesionFalsa(raw))

        # Imagen de 832x832: la entrada es la mitad, las cajas se duplican.
        resultado = detector.detectar(
            imagen(832, 832), source_id="demo", frame_id="demo-000001", frame_number=1
        )

        self.assertEqual(resultado.status, "ok")
        self.assertEqual(resultado.frame_number, 1)
        self.assertEqual(resultado.class_counts, {"person": 1})
        self.assertIsNone(resultado.error)
        self.assertEqual(len(resultado.detections), 1)

        deteccion = resultado.detections[0]
        self.assertEqual(deteccion.class_name, "person")
        self.assertEqual(deteccion.class_id, PERSONA)
        self.assertAlmostEqual(deteccion.confidence, 0.72)
        caja_casi_igual(self, deteccion.bbox_xyxy, (156.0, 104.0, 260.0, 312.0))
        self.assertEqual(resultado.model, metadatos_modelo())
        self.assertEqual(resultado.filters, metadatos_filtros(0.10, 0.45))

    def test_escena_sin_objetos_es_ok_y_no_un_error(self) -> None:
        detector = DetectorYolox(SesionFalsa())

        resultado = detector.detectar(imagen(), source_id="demo", frame_id="demo-000001")

        self.assertEqual(resultado.status, "ok")
        self.assertIsNone(resultado.error)
        self.assertEqual(resultado.detections, ())
        self.assertEqual(resultado.class_counts, {})
        self.assertEqual(
            set(resultado.timings_ms), {"preprocess", "inference", "postprocess", "total"}
        )
        self.assertIsNotNone(resultado.timings_ms["total"])

    def test_limita_cajas_a_la_imagen_y_descarta_cajas_en_el_relleno(self) -> None:
        raw = salida_cruda((0, 10, 400, 100, 100, 1.0, PERSONA, 0.9))
        detector = DetectorYolox(SesionFalsa(raw))

        [deteccion] = detector.detectar(imagen(), source_id="demo", frame_id="f").detections

        caja_casi_igual(self, deteccion.bbox_xyxy, (0.0, 350.0, 60.0, 416.0))

        # Imagen de 416x100: la caja cae debajo de la imagen real, en el relleno.
        resultado = detector.detectar(imagen(416, 100), source_id="demo", frame_id="g")
        self.assertEqual(resultado.status, "ok")
        self.assertEqual(resultado.detections, ())

    def test_varias_clases_y_conteo_ordenado(self) -> None:
        raw = salida_cruda(
            (0, 100, 100, 40, 40, 0.9, PERSONA, 0.9),
            (1, 300, 100, 40, 40, 0.8, PERRO, 0.9),
        )
        detector = DetectorYolox(SesionFalsa(raw))

        resultado = detector.detectar(imagen(), source_id="demo", frame_id="f")

        self.assertEqual([d.class_name for d in resultado.detections], ["person", "dog"])
        self.assertEqual(resultado.class_counts, {"dog": 1, "person": 1})

    def test_error_de_inferencia_es_un_estado_tecnico_no_una_escena_vacia(self) -> None:
        detector = DetectorYolox(SesionFalsa(error=RuntimeError("boom")))

        with self.assertLogs("detector_yolox", level="ERROR"):
            resultado = detector.detectar(imagen(), source_id="demo", frame_id="f")

        self.assertEqual(resultado.status, "error")
        self.assertEqual(resultado.detections, ())
        self.assertIn("inference", resultado.error or "")
        self.assertIn("boom", resultado.error or "")

    def test_salidas_invalidas_del_modelo_se_reportan_como_error(self) -> None:
        casos = (
            (np.zeros((1, 10, 85), dtype=np.float32), "shape"),
            (np.full((1, CELDAS_SALIDA, 85), np.nan, dtype=np.float32), "non-finite"),
            (np.full((1, CELDAS_SALIDA, 85), 2.0, dtype=np.float32), "outside"),
        )
        for raw, mensaje in casos:
            with self.subTest(mensaje=mensaje):
                detector = DetectorYolox(SesionFalsa(raw))
                with self.assertLogs("detector_yolox", level="ERROR"):
                    resultado = detector.detectar(imagen(), source_id="demo", frame_id="f")
                self.assertEqual(resultado.status, "error")
                self.assertIn(mensaje, resultado.error or "")
                self.assertEqual(resultado.detections, ())

    def test_imagen_invalida_se_reporta_en_el_contrato(self) -> None:
        detector = DetectorYolox(SesionFalsa())

        with self.assertLogs("detector_yolox", level="ERROR"):
            resultado = detector.detectar(None, source_id="demo", frame_id="f")  # type: ignore[arg-type]

        self.assertEqual(resultado.status, "error")
        self.assertIn("numpy array", resultado.error or "")
        self.assertIsInstance(resultado.error, str)

    def test_parametros_invalidos_se_rechazan_antes_de_inferir(self) -> None:
        for kwargs in (
            {"score_threshold": 1.5},
            {"score_threshold": True},
            {"nms_threshold": -0.1},
            {"nms_threshold": float("nan")},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    DetectorYolox(SesionFalsa(), **kwargs)

        detector = DetectorYolox(SesionFalsa())
        with self.assertRaises(ValueError):
            detector.detectar(imagen(), source_id="", frame_id="f")
        with self.assertRaises(ValueError):
            detector.detectar(imagen(), source_id="demo", frame_id="f", frame_number=0)
        with self.assertRaises(ValueError):
            detector.detectar(imagen(), source_id="demo", frame_id="f", frame_number=True)

    def test_to_dict_es_serializable_y_excluye_pixeles(self) -> None:
        raw = salida_cruda((0, 100, 100, 40, 40, 1.0, PERSONA, 0.9))
        detector = DetectorYolox(SesionFalsa(raw))
        resultado = detector.detectar(
            imagen(), source_id="demo", frame_id="demo-000001", received_at="2026-01-01T00:00:00+00:00"
        )

        datos = resultado.to_dict()
        json.dumps(datos)
        self.assertEqual(datos["status"], "ok")
        self.assertEqual(datos["source_id"], "demo")
        self.assertEqual(datos["frame_id"], "demo-000001")
        self.assertIsInstance(datos["detections"][0]["bbox_xyxy"], list)
        caja_casi_igual(self, datos["detections"][0]["bbox_xyxy"], [80.0, 80.0, 120.0, 120.0])
        self.assertNotIn("image", datos)
        self.assertEqual(datos["timings_ms"].keys(), {"preprocess", "inference", "postprocess", "total"})
