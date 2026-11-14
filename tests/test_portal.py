"""P6-08: portal local de solo lectura, escapado y sin traversal.

Las corridas se generan con el detector falso de P6-05 ya usado por P6-06, de
modo que el portal lee artefactos reales de eventos, alertas y capturas.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

from alertas import consultar_alertas
from detector_yolox import Deteccion, ResultadoCuadro
from ejecutar_eventos import ejecutar_monitoreo
from eventos import consultar_eventos
from fuente import ObservacionFuente
from metricas import GRUPOS, calcular_metricas
from observaciones import ejecutar_pipeline
from portal import (
    ErrorPortal,
    crear_servidor,
    listar_runs,
    main,
    metricas_run,
    reporte_metricas,
)

MODELO = {
    "name": "YOLOX-Tiny", "version": "test", "sha256": "a" * 64,
    "classes": {"count": 80},
}
FILTROS = {"score_threshold": 0.1, "nms_threshold": 0.45}
INSTANTE = "2026-11-13T12:00:00+00:00"
RUN = "portal-run"


class DetectorSecuencia:
    model = MODELO
    filters = FILTROS

    def __init__(self, scores):
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


def lector(total):
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


class PortalTests(unittest.TestCase):
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
        ejecutar_monitoreo(
            "video.mp4", DetectorSecuencia({n: (0.15,) for n in range(1, 11)}),
            source_id="test", root=self.root, reference=self.ref,
            sample_every=1, interval_ms=0, run_id=RUN, lector=lector(10),
        )
        self.eventos = consultar_eventos(self.root, RUN)
        self.alertas = consultar_alertas(self.root, RUN)
        self.url = self._iniciar(metricas=True)

    def _iniciar(self, *, metricas, metrics_dir=None):
        servidor = crear_servidor(
            self.root, 0, metricas=metricas, metrics_dir=metrics_dir
        )
        hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
        hilo.start()
        # Los addCleanup se ejecutan en orden inverso: primero shutdown, luego
        # join y al final server_close, para no bloquear el hilo del servidor.
        self.addCleanup(servidor.server_close)
        self.addCleanup(hilo.join, 5)
        self.addCleanup(servidor.shutdown)
        host, puerto = servidor.server_address[:2]
        self.assertEqual(host, "127.0.0.1")
        return f"http://{host}:{puerto}"

    def _get(self, ruta, *, base=None, method="GET"):
        solicitud = Request((base or self.url) + ruta, method=method)
        try:
            with urlopen(solicitud, timeout=5) as respuesta:
                return respuesta.status, respuesta.headers, respuesta.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def _escribir_reporte(self):
        """Genera un reporte P6-07 real con los artefactos de la corrida."""

        reporte = calcular_metricas(
            ruta_observaciones=self.root / "observaciones" / f"{RUN}.jsonl",
            ruta_eventos=self.root / "eventos" / f"{RUN}.jsonl",
            ruta_alertas=self.root / "alertas" / f"{RUN}.jsonl",
            root=self.root,
            generated_at=INSTANTE,
        )
        ruta = self.root / "metricas" / f"{RUN}-final.json"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(
            json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return reporte, ruta

    def test_indice_y_detalle_muestran_eventos_alertas_y_capturas(self):
        estado, cabeceras, cuerpo = self._get("/")
        self.assertEqual(estado, 200)
        self.assertIn("text/html", cabeceras["Content-Type"])
        self.assertIn(RUN, cuerpo.decode("utf-8"))
        estado, _, detalle = self._get(f"/portal/runs/{RUN}")
        self.assertEqual(estado, 200)
        html = detalle.decode("utf-8")
        self.assertIn(str(len(self.eventos)), html)
        for alerta in self.alertas[:3]:
            self.assertIn(alerta["rule_id"], html)
            self.assertIn(alerta["occurrence_state"], html)
        self.assertIn("Metricas derivadas", html)

    def test_captura_se_entrega_verificada_y_solo_por_event_id(self):
        evento = next(e for e in self.eventos if e["capture"])
        estado, cabeceras, datos = self._get(
            f"/portal/runs/{RUN}/captures/{evento['event_id']}"
        )
        self.assertEqual(estado, 200)
        self.assertEqual(cabeceras["Content-Type"], "image/jpeg")
        self.assertTrue(datos.startswith(b"\xff\xd8"))
        self.assertEqual(cabeceras["X-Content-Type-Options"], "nosniff")

    def test_solo_loopback_get_y_head(self):
        estado, _, cuerpo = self._get("/healthz")
        self.assertEqual(estado, 200)
        self.assertTrue(json.loads(cuerpo)["read_only"])
        estado, cabeceras, cuerpo = self._get("/", method="HEAD")
        self.assertEqual(estado, 200)
        self.assertEqual(cuerpo, b"")
        self.assertGreater(int(cabeceras["Content-Length"]), 0)
        estado, _, _ = self._get(f"/portal/runs/{RUN}", method="POST")
        self.assertEqual(estado, 405)

    def test_traversal_y_identificadores_invalidos_se_rechazan(self):
        for ruta in (
            "/portal/runs/..%2f..%2fetc/captures/00000000000000000000000000000000",
            "/portal/runs/..%5c..%5cwindows",
            "/portal/runs/%2e%2e",
            "/portal/runs/portal-run/captures/..%2f..%2fdatos",
        ):
            estado, _, cuerpo = self._get(ruta)
            self.assertEqual(estado, 400, ruta)
            self.assertNotIn(b"root:", cuerpo)
        estado, _, _ = self._get("/portal/runs/../eventos")
        self.assertEqual(estado, 404)
        estado, _, _ = self._get(
            "/portal/runs/portal-run/captures/00000000000000000000000000000000"
        )
        self.assertEqual(estado, 404)

    def test_html_escapa_contenido_persistido(self):
        ruta = self.root / "eventos" / f"{RUN}.jsonl"
        lineas = ruta.read_text(encoding="utf-8").splitlines()
        evento = json.loads(lineas[0])
        evento["reason"] = '<script>alert("x")</script>'
        lineas[0] = json.dumps(evento, ensure_ascii=False)
        ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        estado, _, cuerpo = self._get(
            f"/portal/runs/{RUN}/events/{evento['event_id']}"
        )
        self.assertEqual(estado, 200)
        html = cuerpo.decode("utf-8")
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;alert", html)
        self.assertIn("\\&quot;x\\&quot;", html)

    def test_metricas_opcionales_deshabilitadas_y_habilitadas(self):
        estado, _, cuerpo = self._get(f"/portal/runs/{RUN}/metrics")
        self.assertEqual(estado, 200)
        datos = json.loads(cuerpo)
        self.assertEqual(datos["schema"], "p6-08/metrics/1")
        self.assertEqual(datos["event_count"], len(self.eventos))
        self.assertEqual(datos["alert_count"], len(self.alertas))
        self.assertIn("no son metricas G1-G7", datos["limitations"][0])
        base_sin = self._iniciar(metricas=False)
        estado, _, _ = self._get(f"/portal/runs/{RUN}/metrics", base=base_sin)
        self.assertEqual(estado, 404)

    def test_reporte_g1_g7_real_html_y_api(self):
        reporte, _ = self._escribir_reporte()
        estado, cabeceras, cuerpo = self._get(
            f"/portal/runs/{RUN}/metrics/g1-g7"
        )
        self.assertEqual(estado, 200)
        self.assertIn("text/html", cabeceras["Content-Type"])
        html = cuerpo.decode("utf-8")
        for grupo in GRUPOS:
            self.assertIn(grupo, html)
            self.assertIn(reporte["metricas"][grupo]["nombre"], html)
        self.assertIn(reporte["timestamp"], html)
        self.assertIn("Numerador", html)
        self.assertIn("Denominador", html)
        self.assertIn("Estado", html)
        self.assertIn(f"/portal/runs/{RUN}/metrics/g1-g7.json", html)

        estado, cabeceras, cuerpo = self._get(
            f"/portal/runs/{RUN}/metrics/g1-g7.json"
        )
        self.assertEqual(estado, 200)
        self.assertEqual(cabeceras["Content-Type"], "application/json; charset=utf-8")
        datos = json.loads(cuerpo)
        self.assertEqual(datos["schema"], "p6-07/1")
        self.assertEqual(datos["run_id"], RUN)
        self.assertEqual(list(datos["metricas"]), list(GRUPOS))
        for grupo in GRUPOS:
            metrica = datos["metricas"][grupo]
            for campo in (
                "valor", "unidad", "numerador", "denominador", "estado",
                "timestamp",
            ):
                self.assertIn(campo, metrica)
            self.assertEqual(metrica["timestamp"], reporte["timestamp"])

        # Los conteos auxiliares siguen siendo un recurso distinto y no se
        # presentan como G1-G7.
        estado, _, cuerpo = self._get(f"/portal/runs/{RUN}/metrics")
        self.assertEqual(estado, 200)
        conteos = json.loads(cuerpo)
        self.assertEqual(conteos["schema"], "p6-08/metrics/1")
        self.assertNotIn("metricas", conteos)
        self.assertNotIn("G1", conteos)

    def test_sin_reporte_g1_g7_no_fabrica_metricas(self):
        self.assertIsNone(reporte_metricas(self.root, RUN))
        estado, _, cuerpo = self._get(f"/portal/runs/{RUN}/metrics/g1-g7")
        self.assertEqual(estado, 200)
        html = cuerpo.decode("utf-8")
        self.assertIn("Sin reporte G1-G7", html)
        self.assertNotIn("<td>G1</td>", html)
        self.assertIn("no fabrica metricas", html)

        estado, _, cuerpo = self._get(f"/portal/runs/{RUN}/metrics/g1-g7.json")
        self.assertEqual(estado, 404)
        datos = json.loads(cuerpo)
        self.assertFalse(datos["disponible"])
        self.assertEqual(datos["schema"], "p6-08/g1-g7/1")
        self.assertIn("no hay reporte", datos["motivo"])
        self.assertNotIn("metricas", datos)
        self.assertNotIn("G1", datos)

        base_sin = self._iniciar(metricas=False)
        estado, _, cuerpo = self._get(
            f"/portal/runs/{RUN}/metrics/g1-g7.json", base=base_sin
        )
        self.assertEqual(estado, 404)
        self.assertFalse(json.loads(cuerpo)["disponible"])

    def test_reporte_g1_g7_json_invalido_o_schema_incorrecto(self):
        ruta = self.root / "metricas" / f"{RUN}-final.json"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text("{esto no es json", encoding="utf-8")
        with self.assertRaises(ErrorPortal):
            reporte_metricas(self.root, RUN)
        estado, _, cuerpo = self._get(f"/portal/runs/{RUN}/metrics/g1-g7.json")
        self.assertEqual(estado, 422)
        datos = json.loads(cuerpo)
        self.assertFalse(datos["disponible"])
        self.assertIn("no legible", datos["error"])
        self.assertNotIn("metricas", datos)
        estado, _, cuerpo = self._get(f"/portal/runs/{RUN}/metrics/g1-g7")
        self.assertEqual(estado, 422)
        self.assertIn("reporte de metricas", cuerpo.decode("utf-8"))

        invalido = {
            "schema": "otro/1",
            "run_id": RUN,
            "metricas": {grupo: {} for grupo in GRUPOS},
        }
        ruta.write_text(json.dumps(invalido), encoding="utf-8")
        estado, _, cuerpo = self._get(f"/portal/runs/{RUN}/metrics/g1-g7.json")
        self.assertEqual(estado, 422)
        self.assertIn("schema", json.loads(cuerpo)["error"])

        invalido["schema"] = "p6-07/1"
        invalido["run_id"] = "otra-corrida"
        ruta.write_text(json.dumps(invalido), encoding="utf-8")
        estado, _, cuerpo = self._get(f"/portal/runs/{RUN}/metrics/g1-g7.json")
        self.assertEqual(estado, 422)
        self.assertIn("run_id", json.loads(cuerpo)["error"])

    def test_metrics_dir_alternativo(self):
        _, ruta = self._escribir_reporte()
        externo = Path(tempfile.mkdtemp(dir=self.root.parent))
        self.addCleanup(shutil.rmtree, externo, True)
        ruta.replace(externo / ruta.name)
        self.assertIsNone(reporte_metricas(self.root, RUN))
        base = self._iniciar(metricas=True, metrics_dir=externo)
        estado, _, cuerpo = self._get(
            f"/portal/runs/{RUN}/metrics/g1-g7.json", base=base
        )
        self.assertEqual(estado, 200)
        self.assertEqual(json.loads(cuerpo)["run_id"], RUN)

    def test_alertas_y_eventos_individuales(self):
        alerta = self.alertas[0]
        estado, _, cuerpo = self._get(
            f"/portal/runs/{RUN}/alerts/{alerta['alert_id']}"
        )
        self.assertEqual(estado, 200)
        self.assertIn(alerta["rule_id"], cuerpo.decode("utf-8"))
        estado, _, _ = self._get(
            f"/portal/runs/{RUN}/alerts/p6-06-{'0' * 32}"
        )
        self.assertEqual(estado, 404)
        estado, _, _ = self._get(
            f"/portal/runs/{RUN}/events/{'0' * 32}"
        )
        self.assertEqual(estado, 404)

    def test_api_y_validaciones_locales(self):
        self.assertEqual(listar_runs(self.root), [RUN])
        self.assertEqual(
            metricas_run(self.root, RUN)["capture_count"], len(self.eventos)
        )
        with self.assertRaises(ErrorPortal):
            metricas_run(self.root, "../etc")
        with self.assertRaises(ValueError):
            crear_servidor(self.root, 70000)
        self.assertEqual(
            main(["--root", str(self.root), "--port", "70000"]), 1
        )


if __name__ == "__main__":
    unittest.main()

