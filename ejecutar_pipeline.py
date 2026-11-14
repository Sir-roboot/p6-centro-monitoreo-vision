"""Ejecuta fuente -> YOLOX-Tiny -> observaciones primarias versionadas.

Escribe un JSONL con cabecera, una observacion por cuadro y un resumen. No
calcula indicadores G1-G7, no crea eventos, capturas ni alertas.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from detector_yolox import (
    RUTA_PESOS,
    UMBRAL_NMS_PREDETERMINADO,
    UMBRAL_SCORE_PREDETERMINADO,
    cargar_detector,
)
from observaciones import (
    ErrorObservaciones,
    ejecutar_pipeline,
    ruta_salida_predeterminada,
)


def _imprimir_error(error: BaseException) -> None:
    print(
        json.dumps({"error": f"{type(error).__name__}: {error}"}, ensure_ascii=False),
        file=sys.stderr,
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", "--url", default="http://127.0.0.1:8765/stream.mjpg")
    parser.add_argument("--source-id", default="monitor-local")
    parser.add_argument("--sample-every", type=int, default=1)
    parser.add_argument("--interval-ms", type=float, default=200.0)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--weights", type=Path, default=RUTA_PESOS)
    parser.add_argument("--score-threshold", type=float, default=UMBRAL_SCORE_PREDETERMINADO)
    parser.add_argument("--nms-threshold", type=float, default=UMBRAL_NMS_PREDETERMINADO)
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.seconds < 0:
        parser.error("--seconds no puede ser negativo")
    if args.sample_every < 1:
        parser.error("--sample-every debe ser >= 1")
    if args.interval_ms < 0:
        parser.error("--interval-ms no puede ser negativo")
    if args.max_frames is not None and args.max_frames < 1:
        parser.error("--max-frames debe ser >= 1")

    try:
        detector = cargar_detector(
            args.weights,
            score_threshold=args.score_threshold,
            nms_threshold=args.nms_threshold,
            descargar=not args.no_download,
        )
        resumen = ejecutar_pipeline(
            args.source,
            detector,
            source_id=args.source_id,
            salida=args.output,
            sample_every=args.sample_every,
            interval_ms=args.interval_ms,
            max_frames=args.max_frames,
            max_seconds=args.seconds,
            run_id=args.run_id,
            overwrite=args.overwrite,
        )
    except KeyboardInterrupt:
        _imprimir_error(KeyboardInterrupt())
        return 130
    except (ValueError, OSError, RuntimeError, ErrorObservaciones) as error:
        _imprimir_error(error)
        return 1

    salida = args.output or ruta_salida_predeterminada(resumen["run_id"])
    print(
        json.dumps(
            {"output": str(salida), "summary": resumen},
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if resumen["technical_result"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
