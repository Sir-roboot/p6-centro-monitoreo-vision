"""Pruebas del pipeline progresivo P6-03: fuente -> detector -> JSONL versionado.

Todas las pruebas son offline: usan fuentes y detectores falsos, salvo la
integracion con ``generar_secuencia_demo`` que escribe imagenes temporales.
"""

from __future__ import annotations

from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np

import ejecutar_pipeline as cli_pipeline
import recalcular_observaciones as cli_recalculo
from detector_yolox import Deteccion, ResultadoCuadro
from fuente import ObservacionFuente
from generar_secuencia_demo import generar
from observaciones import (
    ESQUEMA,
    VERSION_PIPELINE,
    ErrorObservaciones,
    ejecutar_pipeline,
    leer_registros,
    recalcular_resumen,
    validar_registros,
)

INSTANTE = "2026-11-13T12:00:00+00:00"

MODELO = {
    "name": "YOLOX-Tiny",
    "dataset": "COCO",
    "version": "p6-02",
    "sha256": "0" * 64,
    "size_bytes": 123,
    "input": {"width": 416, "height": 416},
    "classes": {"count": 80},
}

FILTROS = {"score_threshold": 0.25, "nms_threshold": 0.45}

PERRO = Deteccion(class_id=16, class_name="dog", confidence=0.9, bbox_xyxy=(1.0, 2.0, 3.0, 4.0))

CAMPOS_OBSERVACION = {
    "schema", "record", "run_id", "observation_id", "sequence", "source_id",
    "frame_id", "frame_number", "received_at", "started_at", "finished_at",
    "duration_ms", "source_status", "status", "technical_result", "reason",
    "image", "detections", "class_counts", "detector_timings_ms", "model", "error",
}

CAMPOS_RESUMEN = {
    "schema", "record", "run_id", "started_at", "finished_at", "duration_ms",
    "end_state", "technical_result", "source", "config", "model", "filters",
    "counts", "versions",
}

CAMPOS_CABECERA = {
    "schema", "record", "run_id", "created_at", "pipeline", "source", "config",
    "model", "filters", "versions",
}

CLAVES_CONTADORES = {
    "frames_received", "frames_sampled", "frames_discarded",
    "discarded_by_reason", "read_errors", "read_errors_by_reason",
    "frames_processed", "frames_with_detections", "frames_without_detections",
    "detector_calls", "detector_errors", "detector_errors_by_reason",
    "detections_total", "detections_by_class", "end_of_stream_events",
}

def imagen(ancho: int = 32, alto: int = 24) -> np.ndarray:
    return np.full((alto, ancho, 3), 7, dtype=np.uint8)


def observacion(
    status: str = "sampled",
    numero: int = 1,
    *,
    source_id: str = "demo",
    discard_reason: str | None = None,
    pixeles: np.ndarray | None = None,
) -> ObservacionFuente:
    if status == "end_of_stream":
        return ObservacionFuente(source_id, None, None, None, status, None, None, None)
    if pixeles is None and status == "sampled":
        pixeles = imagen()
    return ObservacionFuente(
        source_id, f"{source_id}-{numero:06d}", numero, INSTANTE, status,
        discard_reason, 32, 24, pixeles,
    )


class DetectorFalso:
    """Detector minimo que respeta el contrato de P6-02 sin cargar pesos."""

    model = MODELO
    filters = FILTROS

    def __init__(self, detecciones: tuple[Deteccion, ...] = (), *, error: Exception | None = None):
        self.detecciones = detecciones
        self.error = error
        self.llamadas: list[dict[str, object]] = []

    def detectar(
        self,
        pixeles: np.ndarray,
        *,
        source_id: str,
        frame_id: str,
        frame_number: int | None = None,
        received_at: str | None = None,
    ) -> ResultadoCuadro:
        self.llamadas.append({"frame_id": frame_id, "shape": tuple(pixeles.shape)})
        if self.error is not None:
            raise self.error
        return ResultadoCuadro(
            source_id=source_id,
            frame_id=frame_id,
            frame_number=frame_number,
            received_at=received_at,
            status="ok",
            image_width=pixeles.shape[1],
            image_height=pixeles.shape[0],
            detections=self.detecciones,
            class_counts=dict(Counter(d.class_name for d in self.detecciones)),
            error=None,
            timings_ms={"preprocess": 1.0, "inference": 2.0, "postprocess": 0.5, "total": 3.5},
            model=self.model,
            filters=self.filters,
        )


class DetectorEstadoError(DetectorFalso):
    def detectar(self, pixeles, **identidad) -> ResultadoCuadro:
        return replace(
            super().detectar(pixeles, **identidad),
            status="error", detections=(), class_counts={}, error="fallo simulado",
        )


class DetectorIdentidadIncorrecta(DetectorFalso):
    def detectar(self, pixeles, **identidad) -> ResultadoCuadro:
        return replace(super().detectar(pixeles, **identidad), frame_id="otro-000001")


class DetectorConteoIncoherente(DetectorFalso):
    def detectar(self, pixeles, **identidad) -> ResultadoCuadro:
        return replace(super().detectar(pixeles, **identidad), class_counts={"dog": 2})


class DetectorInvalido(DetectorFalso):
    def detectar(self, pixeles, **identidad):
        return object()


class DetectorConteoNoIterable(DetectorFalso):
    def detectar(self, pixeles, **identidad) -> ResultadoCuadro:
        return replace(super().detectar(pixeles, **identidad), class_counts=42)


class DetectorProcedenciaIncorrecta(DetectorFalso):
    def detectar(self, pixeles, **identidad) -> ResultadoCuadro:
        return replace(
            super().detectar(pixeles, **identidad),
            filters={"score_threshold": 0.01},
        )


def crear_lote(
    carpeta: Path,
    *,
    cantidad: int = 3,
    detector: DetectorFalso | None = None,
    sample_every: int = 1,
    max_frames: int | None = None,
    run_id: str = "run-prueba",
) -> tuple[Path, dict]:
    secuencia = carpeta / "secuencia"
    generar(secuencia, cantidad=cantidad)
    salida = carpeta / "observaciones.jsonl"
    resumen = ejecutar_pipeline(
        str(secuencia),
        detector or DetectorFalso(),
        source_id="demo",
        salida=salida,
        sample_every=sample_every,
        interval_ms=0,
        max_frames=max_frames,
        run_id=run_id,
    )
    return salida, resumen


def reescribir(ruta: Path, registros: list[dict]) -> None:
    ruta.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in registros),
        encoding="utf-8",
    )


class PipelineSecuenciaTests(unittest.TestCase):
    def test_lote_completo_respeta_contrato(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            salida, resumen = crear_lote(carpeta, cantidad=6, sample_every=2)
            contenido = salida.read_text(encoding="utf-8")
            registros = leer_registros(salida)
            cabecera, observaciones, resumen_guardado = validar_registros(registros)

        self.assertEqual(len(registros), 9)
        self.assertEqual(set(cabecera), CAMPOS_CABECERA)
        self.assertEqual(set(resumen_guardado), CAMPOS_RESUMEN)
        self.assertEqual(cabecera["schema"], ESQUEMA)
        self.assertEqual(cabecera["pipeline"], VERSION_PIPELINE)
        self.assertEqual(cabecera["run_id"], "run-prueba")
        self.assertEqual(cabecera["source"]["id"], "demo")
        self.assertEqual(cabecera["source"]["kind"], "directory")
        self.assertEqual(cabecera["config"]["sample_every"], 2)
        self.assertEqual(cabecera["config"]["max_seconds"], None)
        self.assertEqual(resumen, resumen_guardado)
        self.assertEqual(resumen["schema"], ESQUEMA)
        self.assertEqual(resumen["run_id"], "run-prueba")
        self.assertEqual(resumen["versions"]["pipeline"], VERSION_PIPELINE)
        self.assertEqual(resumen["end_state"], "end_of_stream")
        self.assertEqual(resumen["technical_result"], "ok")
        self.assertEqual(set(resumen["counts"]), CLAVES_CONTADORES)
        self.assertEqual(resumen["counts"], {
            "frames_received": 6,
            "frames_sampled": 3,
            "frames_discarded": 3,
            "discarded_by_reason": {"sampling_interval": 3},
            "read_errors": 0,
            "read_errors_by_reason": {},
            "frames_processed": 3,
            "frames_with_detections": 0,
            "frames_without_detections": 3,
            "detector_calls": 3,
            "detector_errors": 0,
            "detector_errors_by_reason": {},
            "detections_total": 0,
            "detections_by_class": {},
            "end_of_stream_events": 1,
        })
        self.assertEqual([r["sequence"] for r in observaciones], list(range(1, 8)))
        self.assertEqual([r["status"] for r in observaciones], [
            "processed", "discarded", "processed", "discarded", "processed",
            "discarded", "end_of_stream",
        ])
        for registro in observaciones:
            self.assertEqual(set(registro), CAMPOS_OBSERVACION)
            if registro["image"] is not None:
                self.assertEqual(set(registro["image"]), {"width", "height"})
        self.assertEqual(resumen["config"]["max_frames"], None)
        self.assertNotIn("base64", contenido.lower())
        self.assertNotIn("b64", contenido.lower())

    def test_detecciones_y_conteo_por_clase(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            detector = DetectorFalso(detecciones=(PERRO,))
            salida, resumen = crear_lote(Path(temporal), cantidad=2, detector=detector)
            observaciones = validar_registros(leer_registros(salida))[1]

        self.assertEqual(len(detector.llamadas), 2)
        self.assertEqual(resumen["counts"]["frames_with_detections"], 2)
        self.assertEqual(resumen["counts"]["frames_without_detections"], 0)
        self.assertEqual(resumen["counts"]["detections_total"], 2)
        self.assertEqual(resumen["counts"]["detections_by_class"], {"dog": 2})
        self.assertEqual(resumen["technical_result"], "ok")
        primera = observaciones[0]
        self.assertEqual(primera["technical_result"], "ok")
        self.assertEqual(primera["class_counts"], {"dog": 1})
        self.assertEqual(primera["detections"][0]["class_name"], "dog")
        self.assertAlmostEqual(primera["detections"][0]["confidence"], 0.9)
        self.assertEqual(primera["detector_timings_ms"]["total"], 3.5)

    def test_max_frames_detiene_sin_inventar_fin_de_flujo(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            salida, resumen = crear_lote(Path(temporal), cantidad=5, max_frames=2)
            registros = leer_registros(salida)
            validar_registros(registros)

        self.assertEqual(len(registros), 4)
        self.assertEqual(resumen["end_state"], "max_frames")
        self.assertEqual(resumen["counts"]["frames_received"], 2)
        self.assertEqual(resumen["counts"]["end_of_stream_events"], 0)

    def test_max_seconds_detiene_por_duracion(self) -> None:
        def lector_lento(source, **configuracion):
            numero = 0
            while True:
                numero += 1
                time.sleep(0.01)
                yield observacion(numero=numero)

        with tempfile.TemporaryDirectory() as temporal:
            salida = Path(temporal) / "tiempo.jsonl"
            resumen = ejecutar_pipeline(
                "ignorado", DetectorFalso(), source_id="demo", salida=salida,
                max_seconds=0.001, run_id="run-tiempo", lector=lector_lento,
            )
            validar_registros(leer_registros(salida))

        self.assertEqual(resumen["end_state"], "max_duration")
        self.assertEqual(resumen["config"]["max_seconds"], 0.001)
        self.assertGreaterEqual(resumen["counts"]["frames_received"], 1)

    def test_salida_existente_requiere_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            generar(carpeta / "secuencia", cantidad=1)
            salida = carpeta / "observaciones.jsonl"
            salida.write_text("previo\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                ejecutar_pipeline(
                    str(carpeta / "secuencia"), DetectorFalso(), source_id="demo", salida=salida
                )
            ejecutar_pipeline(
                str(carpeta / "secuencia"), DetectorFalso(), source_id="demo",
                salida=salida, overwrite=True,
            )
            registros = leer_registros(salida)
        self.assertEqual(registros[0]["record"], "cabecera")

    def test_salida_no_puede_ser_directorio(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            generar(carpeta / "secuencia", cantidad=1)
            destino = carpeta / "directorio"
            destino.mkdir()
            with self.assertRaises(IsADirectoryError):
                ejecutar_pipeline(
                    str(carpeta / "secuencia"), DetectorFalso(),
                    source_id="demo", salida=destino, overwrite=True,
                )

    def test_salida_no_puede_reemplazar_la_entrada(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            entrada = generar(carpeta / "secuencia", cantidad=1)[0]
            contenido = entrada.read_bytes()
            with self.assertRaises(ValueError):
                ejecutar_pipeline(
                    str(entrada), DetectorFalso(), source_id="demo",
                    salida=entrada, overwrite=True,
                )
            self.assertEqual(entrada.read_bytes(), contenido)
            with self.assertRaises(ValueError):
                ejecutar_pipeline(
                    str(entrada), DetectorFalso(), source_id="demo",
                    salida=carpeta / "salida.mp4", overwrite=True,
                )

    def test_fallo_al_publicar_conserva_temporal_completo(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            generar(carpeta / "secuencia", cantidad=1)
            salida = carpeta / "observaciones.jsonl"
            with patch("observaciones.os.replace", side_effect=OSError("bloqueado")):
                with self.assertRaisesRegex(OSError, "temporal completo conservado"):
                    ejecutar_pipeline(
                        str(carpeta / "secuencia"), DetectorFalso(),
                        source_id="demo", salida=salida,
                    )
            self.assertFalse(salida.exists())
            temporales = list(carpeta.glob(".*.tmp"))
            self.assertEqual(len(temporales), 1)
            self.assertEqual(leer_registros(temporales[0])[-1]["record"], "resumen")

    def test_configuracion_invalida_no_crea_salida(self) -> None:
        casos = [
            {"sample_every": 0},
            {"interval_ms": -1},
            {"max_frames": 0},
            {"max_seconds": -1},
            {"source_id": "demo/extra"},
            {"run_id": "run con espacios"},
        ]
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            generar(carpeta / "secuencia", cantidad=1)
            salida = carpeta / "observaciones.jsonl"
            for cambios in casos:
                argumentos = {
                    "source_id": "demo", "salida": salida, "run_id": "run-valido",
                }
                argumentos.update(cambios)
                with self.subTest(cambios=cambios), self.assertRaises(ValueError):
                    ejecutar_pipeline(str(carpeta / "secuencia"), DetectorFalso(), **argumentos)
                self.assertFalse(salida.exists())

    def test_detector_invalido_se_rechaza_antes_de_escribir(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            generar(carpeta / "secuencia", cantidad=1)
            salida = carpeta / "observaciones.jsonl"
            with self.assertRaises(ValueError):
                ejecutar_pipeline(
                    str(carpeta / "secuencia"), object(), source_id="demo", salida=salida
                )
            self.assertFalse(salida.exists())

class ErroresTests(unittest.TestCase):
    def test_url_y_excepcion_no_persisten_credenciales(self) -> None:
        secreto = "clave-super-secreta"
        url = f"RTSP://usuario:{secreto}@ejemplo.test/live?token={secreto}#fragmento"

        def lector_roto(source, **configuracion):
            raise RuntimeError(f"fallo al leer {source}")
            yield  # conserva la semantica de iterador

        with tempfile.TemporaryDirectory() as temporal:
            salida = Path(temporal) / "observaciones.jsonl"
            resumen = ejecutar_pipeline(
                url, DetectorFalso(), source_id="demo", salida=salida,
                lector=lector_roto,
            )
            contenido = salida.read_text(encoding="utf-8")
            registros = leer_registros(salida)
            validar_registros(registros)
        self.assertNotIn(secreto, contenido)
        self.assertNotIn("usuario", contenido)
        self.assertEqual(resumen["source"]["ref"], "rtsp://ejemplo.test/live")
        self.assertEqual(resumen["source"]["kind"], "stream")
        self.assertEqual(registros[1]["reason"], "source_exception")

    def test_imagen_corrupta_no_oculta_cuadros_posteriores(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            secuencia = carpeta / "secuencia"
            generar(secuencia, cantidad=4)
            (secuencia / "frame_0002.png").write_bytes(b"imagen corrupta")
            salida = carpeta / "observaciones.jsonl"
            detector = DetectorFalso()
            resumen = ejecutar_pipeline(
                str(secuencia), detector, source_id="demo", salida=salida,
                interval_ms=0, run_id="run-corrupta",
            )
            observaciones = validar_registros(leer_registros(salida))[1]

        self.assertEqual([r["frame_number"] for r in observaciones], [1, 2, 3, 4, None])
        self.assertEqual([r["status"] for r in observaciones], [
            "processed", "read_error", "processed", "processed", "end_of_stream",
        ])
        self.assertEqual(resumen["end_state"], "end_of_stream")
        self.assertEqual(resumen["technical_result"], "with_errors")
        self.assertEqual(resumen["counts"]["read_errors_by_reason"], {"invalid_image": 1})
        self.assertEqual(resumen["counts"]["frames_processed"], 3)
        self.assertEqual(len(detector.llamadas), 3)

    def test_excepcion_de_fuente_cierra_el_lote_sin_temporal(self) -> None:
        def lector_roto(source, **configuracion):
            yield observacion(numero=1)
            raise RuntimeError("fallo de fuente")

        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            salida = carpeta / "observaciones.jsonl"
            with patch("observaciones.logger.exception"):
                resumen = ejecutar_pipeline(
                    "ignorado", DetectorFalso(), source_id="demo", salida=salida,
                    run_id="run-fuente", lector=lector_roto,
                )
            registros = leer_registros(salida)
            validar_registros(registros)
            sobrantes = [p.name for p in carpeta.iterdir() if p.name.endswith(".tmp")]

        self.assertEqual(resumen["end_state"], "source_exception")
        self.assertEqual(resumen["technical_result"], "with_errors")
        self.assertEqual(resumen["counts"]["read_errors"], 1)
        self.assertEqual(resumen["counts"]["read_errors_by_reason"], {"source_exception": 1})
        terminal = registros[2]
        self.assertEqual(terminal["status"], "read_error")
        self.assertEqual(terminal["source_status"], "read_error")
        self.assertEqual(terminal["reason"], "source_exception")
        self.assertIn("RuntimeError", terminal["error"])
        self.assertIsNone(terminal["frame_id"])
        self.assertEqual(sobrantes, [])

    def test_fuente_inexistente_se_documenta_como_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            salida = carpeta / "observaciones.jsonl"
            resumen = ejecutar_pipeline(
                str(carpeta / "no-existe"), DetectorFalso(), source_id="demo",
                salida=salida, run_id="run-ausente",
            )
            registros = leer_registros(salida)
            validar_registros(registros)

        self.assertEqual(resumen["end_state"], "source_not_found")
        self.assertEqual(resumen["technical_result"], "with_errors")
        self.assertEqual(resumen["counts"]["read_errors_by_reason"], {"source_not_found": 1})
        self.assertEqual(registros[1]["reason"], "source_not_found")
        self.assertEqual(registros[1]["status"], "read_error")

    def test_excepcion_del_detector_no_inventa_detecciones(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            with patch("observaciones.logger.exception"):
                salida, resumen = crear_lote(
                    Path(temporal), cantidad=1,
                    detector=DetectorFalso(error=RuntimeError("modelo roto")),
                )
            observaciones = validar_registros(leer_registros(salida))[1]

        self.assertEqual(resumen["technical_result"], "with_errors")
        self.assertEqual(resumen["counts"]["detector_calls"], 1)
        self.assertEqual(resumen["counts"]["detector_errors"], 1)
        self.assertEqual(
            resumen["counts"]["detector_errors_by_reason"], {"detector_exception": 1}
        )
        self.assertEqual(observaciones[0]["status"], "detector_error")
        self.assertEqual(observaciones[0]["reason"], "detector_exception")
        self.assertIsNone(observaciones[0]["detections"])
        self.assertIn("RuntimeError", observaciones[0]["error"])

    def test_resultados_de_detector_incoherentes_se_registran(self) -> None:
        casos = [
            ("resultado_invalido", DetectorInvalido(), "invalid_detector_result"),
            ("conteo_no_iterable", DetectorConteoNoIterable(), "invalid_detector_result"),
            ("procedencia", DetectorProcedenciaIncorrecta(), "detector_provenance_mismatch"),
            ("estado_error", DetectorEstadoError(), "detector_status_error"),
            ("identidad", DetectorIdentidadIncorrecta(), "detector_identity_mismatch"),
            ("conteo", DetectorConteoIncoherente(), "inconsistent_class_counts"),
        ]
        for nombre, detector, motivo in casos:
            with self.subTest(caso=nombre), tempfile.TemporaryDirectory() as temporal:
                with patch("observaciones.logger.exception"):
                    salida, resumen = crear_lote(Path(temporal), cantidad=1, detector=detector)
                observaciones = validar_registros(leer_registros(salida))[1]

                self.assertEqual(observaciones[0]["status"], "detector_error")
                self.assertEqual(observaciones[0]["reason"], motivo)
                self.assertIsNone(observaciones[0]["detections"])
                self.assertEqual(resumen["counts"]["detector_errors"], 1)
                self.assertEqual(
                    resumen["counts"]["detector_errors_by_reason"], {motivo: 1}
                )
                self.assertEqual(resumen["technical_result"], "with_errors")

    def test_cuadro_sampled_sin_imagen_es_error_de_lectura(self) -> None:
        def lector_sin_pixeles(source, **configuracion):
            yield ObservacionFuente(
                "demo", "demo-000001", 1, INSTANTE, "sampled", None, 32, 24, None
            )
            yield observacion(status="end_of_stream")

        with tempfile.TemporaryDirectory() as temporal:
            salida = Path(temporal) / "sin-pixeles.jsonl"
            resumen = ejecutar_pipeline(
                "ignorado", DetectorFalso(), source_id="demo", salida=salida,
                run_id="run-sin-pixeles", lector=lector_sin_pixeles,
            )
            observaciones = validar_registros(leer_registros(salida))[1]

        self.assertEqual(observaciones[0]["status"], "read_error")
        self.assertEqual(observaciones[0]["reason"], "missing_image")
        self.assertEqual(resumen["counts"]["read_errors"], 1)
        self.assertEqual(resumen["end_state"], "missing_image")

class ValidacionRecalculoTests(unittest.TestCase):
    def test_recalculo_coincide_y_no_abre_la_fuente(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            salida, resumen = crear_lote(
                Path(temporal), cantidad=4, sample_every=2,
                detector=DetectorFalso(detecciones=(PERRO,)),
            )
            resultado = recalcular_resumen(salida)
            with patch(
                "observaciones.leer_fuente",
                side_effect=AssertionError("el recalculo no debe abrir la fuente"),
            ):
                repetido = recalcular_resumen(salida)

        self.assertTrue(resultado["coincide"])
        self.assertEqual(resultado["run_id"], resumen["run_id"])
        self.assertEqual(resultado["recalculado"]["counts"], resumen["counts"])
        self.assertEqual(resultado["recalculado"]["technical_result"], "ok")
        self.assertEqual(resultado["recalculado"]["observaciones"], 5)
        self.assertEqual(repetido, resultado)

    def test_resumen_alterado_no_coincide(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            salida, resumen = crear_lote(Path(temporal), cantidad=2, sample_every=2)
            registros = leer_registros(salida)
            original = json.loads(json.dumps(resumen))
            registros[-1]["counts"]["frames_received"] += 1
            registros[-1]["technical_result"] = "with_errors"
            reescribir(salida, registros)
            resultado = recalcular_resumen(salida)

        self.assertFalse(resultado["coincide"])
        self.assertEqual(resultado["recalculado"]["counts"], original["counts"])
        self.assertEqual(resultado["recalculado"]["technical_result"], "ok")
        self.assertEqual(
            resultado["resumen_almacenado"]["counts"]["frames_received"],
            original["counts"]["frames_received"] + 1,
        )

    def test_validar_registros_rechaza_alteraciones(self) -> None:
        def alterar(cambios):
            def aplicar(registros):
                cambios(registros)
                return registros
            return aplicar

        casos = {
            "secuencia": alterar(lambda r: r[2].__setitem__("sequence", 99)),
            "source_id": alterar(lambda r: r[1].__setitem__("source_id", "otro")),
            "observation_id duplicado": alterar(
                lambda r: r[2].__setitem__("observation_id", r[1]["observation_id"])
            ),
            "frame_id duplicado": alterar(
                lambda r: r[2].__setitem__("frame_id", r[1]["frame_id"])
            ),
            "frame_number no creciente": alterar(
                lambda r: r[2].__setitem__("frame_number", r[1]["frame_number"])
            ),
            "class_counts incoherente": alterar(
                lambda r: r[1].__setitem__("class_counts", {"dog": 1})
            ),
            "technical_result incoherente": alterar(
                lambda r: r[1].__setitem__("technical_result", "ok")
            ),
            "status invalido": alterar(lambda r: r[1].__setitem__("status", "otro")),
            "estado de fuente incoherente": alterar(
                lambda r: r[1].__setitem__("source_status", "discarded")
            ),
            "run_id del resumen": alterar(lambda r: r[-1].__setitem__("run_id", "otro")),
            "fuente del resumen": alterar(
                lambda r: r[-1]["source"].__setitem__("id", "otra")
            ),
            "modelo del resumen": alterar(
                lambda r: r[-1]["model"].__setitem__("version", "otra")
            ),
            "counts incompletos": alterar(lambda r: r[-1]["counts"].pop("frames_received")),
        }
        with tempfile.TemporaryDirectory() as temporal:
            salida, _ = crear_lote(Path(temporal), cantidad=3)
            base = leer_registros(salida)
        for nombre, aplicar in casos.items():
            with self.subTest(caso=nombre):
                alterados = aplicar(json.loads(json.dumps(base)))
                with self.assertRaises(ErrorObservaciones):
                    validar_registros(alterados)

    def test_validar_registros_rechaza_lote_corto(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            salida, _ = crear_lote(Path(temporal), cantidad=1)
            cabecera = leer_registros(salida)[0]
        with self.assertRaises(ErrorObservaciones):
            validar_registros([])
        with self.assertRaises(ErrorObservaciones):
            validar_registros([cabecera])

    def test_leer_registros_rechaza_lineas_invalidas(self) -> None:
        contenidos = {
            "vacia": '{"record": "cabecera"}\n\n',
            "json": "no es json\n",
            "no-objeto": "[1, 2, 3]\n",
        }
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            for nombre, contenido in contenidos.items():
                with self.subTest(caso=nombre):
                    ruta = carpeta / f"{nombre}.jsonl"
                    ruta.write_text(contenido, encoding="utf-8")
                    with self.assertRaises(ErrorObservaciones):
                        leer_registros(ruta)
            with self.assertRaises(FileNotFoundError):
                leer_registros(carpeta / "ausente.jsonl")

class CliTests(unittest.TestCase):
    def test_cli_ejecuta_pipeline_y_reporta_salida(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            secuencia = carpeta / "secuencia"
            generar(secuencia, cantidad=3)
            salida = carpeta / "cli.jsonl"
            argumentos = [
                "ejecutar_pipeline.py", "--source", str(secuencia), "--source-id", "demo",
                "--sample-every", "1", "--interval-ms", "0", "--output", str(salida),
                "--run-id", "run-cli", "--no-download",
            ]
            texto = io.StringIO()
            with patch.object(cli_pipeline, "cargar_detector", return_value=DetectorFalso()):
                with patch.object(sys, "argv", argumentos), redirect_stdout(texto):
                    codigo = cli_pipeline.main()
            lote = leer_registros(salida)

        self.assertEqual(codigo, 0)
        respuesta = json.loads(texto.getvalue())
        self.assertEqual(respuesta["output"], str(salida))
        self.assertEqual(respuesta["summary"]["run_id"], "run-cli")
        self.assertEqual(respuesta["summary"]["counts"]["frames_received"], 3)
        self.assertEqual(lote[0]["record"], "cabecera")

    def test_cli_ejecutar_pipeline_reporta_error(self) -> None:
        texto = io.StringIO()
        errores = io.StringIO()
        argumentos = ["ejecutar_pipeline.py", "--source", "x", "--no-download"]
        with patch.object(
            cli_pipeline, "cargar_detector", side_effect=RuntimeError("modelo ausente")
        ):
            with patch.object(sys, "argv", argumentos), redirect_stdout(texto), redirect_stderr(errores):
                codigo = cli_pipeline.main()

        self.assertEqual(codigo, 1)
        self.assertEqual(texto.getvalue(), "")
        detalle = json.loads(errores.getvalue())
        self.assertIn("RuntimeError", detalle["error"])
        self.assertIn("modelo ausente", detalle["error"])

    def test_cli_recalculo_ok_y_detecta_resumen_alterado(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            salida, _ = crear_lote(Path(temporal), cantidad=2, sample_every=2)
            argumentos = ["recalcular_observaciones.py", "--input", str(salida)]

            texto = io.StringIO()
            with patch.object(sys, "argv", argumentos), redirect_stdout(texto):
                codigo = cli_recalculo.main()
            self.assertEqual(codigo, 0)
            self.assertTrue(json.loads(texto.getvalue())["coincide"])

            registros = leer_registros(salida)
            registros[-1]["counts"]["frames_received"] += 1
            reescribir(salida, registros)
            texto = io.StringIO()
            with patch.object(sys, "argv", argumentos), redirect_stdout(texto):
                codigo = cli_recalculo.main()

        self.assertEqual(codigo, 1)
        self.assertFalse(json.loads(texto.getvalue())["coincide"])

    def test_cli_recalculo_reporta_archivo_ausente(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            argumentos = [
                "recalcular_observaciones.py", "--input", str(Path(temporal) / "ausente.jsonl")
            ]
            errores = io.StringIO()
            with patch.object(sys, "argv", argumentos), redirect_stderr(errores):
                codigo = cli_recalculo.main()

        self.assertEqual(codigo, 1)
        self.assertIn("FileNotFoundError", json.loads(errores.getvalue())["error"])


if __name__ == "__main__":
    unittest.main()
