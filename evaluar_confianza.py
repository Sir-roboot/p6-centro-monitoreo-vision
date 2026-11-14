"""Construye la politica de confianza P6-04 desde observaciones P6-03.

Solo lee JSONL ya producidos y escribe un artefacto JSON versionado. No carga
el modelo, no vuelve a inferir, no aplica NMS y no crea eventos, capturas ni
alertas. La insuficiencia de evidencia se publica como estado, no como error de
ejecucion ni como normalidad.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from politica_confianza import (
    CAIDA_MEDIA_MINIMA_PREDETERMINADA,
    ConfiguracionConfianza,
    ErrorConfianza,
    FRACCION_BAJA_MINIMA_PREDETERMINADA,
    MIN_FRAMES_REFERENCIA_PREDETERMINADOS,
    MIN_PUNTUACIONES_REFERENCIA_PREDETERMINADAS,
    MIN_PUNTUACIONES_VENTANA_PREDETERMINADA,
    MinimosReferencia,
    ReglaCambioSostenido,
    UMBRAL_INFERIOR_PREDETERMINADO,
    UMBRAL_SUPERIOR_PREDETERMINADO,
    UmbralesConfianza,
    VENTANA_FRAMES_PREDETERMINADA,
    VENTANAS_CONSECUTIVAS_PREDETERMINADAS,
    escribir_politica,
    evaluar_archivo,
    resumen_politica,
    ruta_politica_predeterminada,
)


def _imprimir_error(error: BaseException) -> None:
    print(
        json.dumps({"error": f"{type(error).__name__}: {error}"}, ensure_ascii=False),
        file=sys.stderr,
        flush=True,
    )


def _misma_ruta(primera: Path, segunda: Path) -> bool:
    try:
        return primera.resolve() == segunda.resolve()
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", "-o", type=Path)
    parser.add_argument(
        "--lower", type=float, default=UMBRAL_INFERIOR_PREDETERMINADO
    )
    parser.add_argument(
        "--upper", type=float, default=UMBRAL_SUPERIOR_PREDETERMINADO
    )
    parser.add_argument(
        "--window-frames", type=int, default=VENTANA_FRAMES_PREDETERMINADA
    )
    parser.add_argument(
        "--window-min-scores",
        type=int,
        default=MIN_PUNTUACIONES_VENTANA_PREDETERMINADA,
    )
    parser.add_argument(
        "--consecutive-windows",
        type=int,
        default=VENTANAS_CONSECUTIVAS_PREDETERMINADAS,
    )
    parser.add_argument(
        "--low-fraction-min",
        type=float,
        default=FRACCION_BAJA_MINIMA_PREDETERMINADA,
    )
    parser.add_argument(
        "--mean-drop-min", type=float, default=CAIDA_MEDIA_MINIMA_PREDETERMINADA
    )
    parser.add_argument(
        "--reference-min-frames",
        type=int,
        default=MIN_FRAMES_REFERENCIA_PREDETERMINADOS,
    )
    parser.add_argument(
        "--reference-min-scores",
        type=int,
        default=MIN_PUNTUACIONES_REFERENCIA_PREDETERMINADAS,
    )
    parser.add_argument("--threshold-justification")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    try:
        configuracion = ConfiguracionConfianza(
            umbrales=UmbralesConfianza(args.lower, args.upper),
            regla=ReglaCambioSostenido(
                ventana_frames=args.window_frames,
                min_puntuaciones_ventana=args.window_min_scores,
                ventanas_consecutivas=args.consecutive_windows,
                fraccion_baja_minima=args.low_fraction_min,
                caida_media_minima=args.mean_drop_min,
            ),
            minimos_referencia=MinimosReferencia(
                frames=args.reference_min_frames,
                puntuaciones=args.reference_min_scores,
            ),
        )
    except (ValueError, TypeError) as error:
        _imprimir_error(error)
        return 1

    try:
        politica = evaluar_archivo(
            args.input,
            referencia=args.reference,
            configuracion=configuracion,
            justificacion_umbrales=args.threshold_justification,
        )
        salida = args.output or ruta_politica_predeterminada(
            politica["input"]["run_id"]
        )
        for nombre, origen in (
            ("entrada", args.input),
            ("referencia", args.reference),
        ):
            if origen is not None and _misma_ruta(salida, origen):
                raise ErrorConfianza(
                    f"la salida no puede reemplazar la {nombre}: {salida}"
                )
        escribir_politica(politica, salida, overwrite=args.overwrite)
    except KeyboardInterrupt:
        _imprimir_error(KeyboardInterrupt())
        return 130
    except (OSError, ValueError) as error:
        _imprimir_error(error)
        return 1

    print(
        json.dumps(
            {"output": str(salida), "summary": resumen_politica(politica)},
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
