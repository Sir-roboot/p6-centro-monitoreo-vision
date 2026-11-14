"""P6-09: comprobacion local de las demostraciones C1/C2 ya persistidas.

Estas pruebas no capturan video, no ejecutan el detector y no modifican
artefactos. Se saltan en un clon limpio porque ``datos_locales/`` esta ignorado
por Git y los JPEG/JSONL permanecen solo en la laptop.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from verificar_demostracion import DEMOSTRACIONES, verificar_corrida

ROOT = Path(__file__).resolve().parents[1] / "datos_locales"
RUN_IDS = tuple(DEMOSTRACIONES)


def _artefactos_disponibles() -> bool:
    requeridos = []
    for run_id in RUN_IDS:
        requeridos.extend(
            [
                ROOT / "observaciones" / f"{run_id}.jsonl",
                ROOT / "eventos" / f"{run_id}.jsonl",
                ROOT / "alertas" / f"{run_id}.jsonl",
                ROOT / "reportes" / f"p6-07-{run_id}.json",
                ROOT / "politicas" / f"{run_id}-p6-04.json",
            ]
        )
    return all(ruta.is_file() for ruta in requeridos)


@unittest.skipUnless(
    _artefactos_disponibles(),
    "datos_locales/ local no disponible; evidencia P6-09 no versionada",
)
class VerificacionDemostracionesP609(unittest.TestCase):
    def test_c1_documentada_es_coherente(self) -> None:
        resultado = verificar_corrida(ROOT, "p6-09-c1-20261113-232205")
        self.assertTrue(resultado["ok"], resultado["errors"])
        self.assertEqual(resultado["event_count"], 10)
        self.assertEqual(resultado["alert_count"], 10)
        self.assertEqual(resultado["capture_count"], 10)
        self.assertEqual(resultado["zones"]["low"], 33)
        self.assertEqual(resultado["g4"], (33, 156))
        self.assertEqual(resultado["g5"], (10, 10))
        self.assertGreater(resultado["zones"]["low"], 0)
        self.assertGreater(resultado["summary"]["counts_by_rule"]["p6-06-low-confidence"], 0)

    def test_c2_documentada_es_coherente(self) -> None:
        resultado = verificar_corrida(ROOT, "p6-09-video-20261113-230341")
        self.assertTrue(resultado["ok"], resultado["errors"])
        self.assertEqual(resultado["event_count"], 10)
        self.assertEqual(resultado["alert_count"], 10)
        self.assertEqual(resultado["capture_count"], 10)
        self.assertEqual(resultado["zones"]["low"], 60)
        self.assertEqual(resultado["g4"], (60, 217))
        self.assertEqual(resultado["g5"], (10, 10))
        self.assertGreater(resultado["zones"]["low"], 0)
        self.assertGreater(resultado["summary"]["counts_by_rule"]["p6-06-low-confidence"], 0)

    def test_expectativa_alterada_se_detecta(self) -> None:
        esperado = dict(DEMOSTRACIONES["p6-09-c1-20261113-232205"])
        esperado["g4"] = (0, 156)
        resultado = verificar_corrida(
            ROOT, "p6-09-c1-20261113-232205", expected=esperado
        )
        self.assertFalse(resultado["ok"])
        self.assertTrue(
            any("G4 numerador/denominador" in error for error in resultado["errors"])
        )


if __name__ == "__main__":
    unittest.main()
