"""P6-05: fuente -> detector -> P6-03 -> P6-04 -> eventos/capturas.

El grabador de pantalla sigue siendo una herramienta externa. Solo se guardan
los cuadros muestreados que pasaron por el detector, no todos los del stream.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any, Callable, Iterator, Mapping

import cv2
import numpy as np

from detector_yolox import RUTA_PESOS, cargar_detector
from eventos import consultar_eventos, persistir_eventos
from fuente import ObservacionFuente
from observaciones import ejecutar_pipeline, nuevo_run_id
from politica_confianza import (
    ConfiguracionConfianza,
    UmbralesConfianza,
    escribir_politica,
    evaluar_archivo,
)


def ejecutar_monitoreo(
    source: str,
    detector: Any,
    *,
    source_id: str,
    root: str | Path = Path("datos_locales"),
    reference: str | Path | None = None,
    lower: float = 0.30,
    upper: float = 0.70,
    threshold_justification: str | None = None,
    sample_every: int = 15,
    max_frames: int | None = None,
    seconds: float = 0.0,
    interval_ms: float = 200.0,
    run_id: str | None = None,
    lector: Callable[..., Iterator[ObservacionFuente]] | None = None,
) -> dict[str, Any]:
    """Ejecuta una corrida; las imagenes y eventos permanecen tras el proceso."""

    run_id = run_id or nuevo_run_id()
    root = Path(root)
    observaciones_path = root / "observaciones" / f"{run_id}.jsonl"
    politica_path = root / "politicas" / f"{run_id}-p6-04.json"
    if politica_path.exists():
        raise FileExistsError(f"ya existe politica de esta corrida: {politica_path}")
    configuracion = ConfiguracionConfianza(umbrales=UmbralesConfianza(lower, upper))
    with TemporaryDirectory(prefix="p6-05-capturas-") as temporal:
        imagenes: dict[str, Path] = {}

        def conservar(registro: Mapping[str, Any], imagen: np.ndarray | None) -> None:
            if registro["source_status"] != "sampled" or imagen is None:
                return
            if registro["status"] not in {"processed", "detector_error"}:
                return
            ok, codificada = cv2.imencode(".jpg", imagen)
            if not ok:
                raise RuntimeError(f"no se pudo codificar el cuadro {registro['observation_id']}")
            ruta = Path(temporal) / f"{registro['sequence']:06d}.jpg"
            ruta.write_bytes(codificada.tobytes())
            imagenes[registro["observation_id"]] = ruta

        resumen = ejecutar_pipeline(
            source, detector, source_id=source_id,
            salida=observaciones_path, sample_every=sample_every,
            interval_ms=interval_ms, max_frames=max_frames,
            max_seconds=seconds, run_id=run_id, lector=lector,
            on_observation=conservar,
        )
        politica = evaluar_archivo(
            observaciones_path, referencia=reference,
            configuracion=configuracion,
            justificacion_umbrales=threshold_justification,
        )
        escribir_politica(politica, politica_path)
        eventos_path = persistir_eventos(
            observaciones_path, politica_path, imagenes, root=root,
        )
    return {
        "run_id": run_id,
        "technical_result": resumen["technical_result"],
        "observations": str(observaciones_path),
        "policy": str(politica_path),
        "events": str(eventos_path),
        "event_count": len(consultar_eventos(root, run_id)),
        "policy_status": politica["sustained_change"]["status"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="http://127.0.0.1:8765/stream.mjpg")
    parser.add_argument("--source-id", default="monitor-video")
    parser.add_argument("--root", type=Path, default=Path("datos_locales"))
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--lower", type=float, default=0.30)
    parser.add_argument("--upper", type=float, default=0.70)
    parser.add_argument("--threshold-justification")
    parser.add_argument("--sample-every", type=int, default=15)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--interval-ms", type=float, default=200.0)
    parser.add_argument("--run-id")
    parser.add_argument("--weights", type=Path, default=RUTA_PESOS)
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args()
    try:
        detector = cargar_detector(args.weights, descargar=not args.no_download)
        resultado = ejecutar_monitoreo(
            args.source, detector, source_id=args.source_id,
            root=args.root, reference=args.reference,
            lower=args.lower, upper=args.upper,
            threshold_justification=args.threshold_justification,
            sample_every=args.sample_every, max_frames=args.max_frames,
            seconds=args.seconds, interval_ms=args.interval_ms,
            run_id=args.run_id,
        )
    except (ValueError, OSError, RuntimeError) as error:
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(resultado, ensure_ascii=False, sort_keys=True))
    return 0 if resultado["technical_result"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
