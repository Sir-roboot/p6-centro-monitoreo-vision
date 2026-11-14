"""P6-06: genera y persiste alertas desde eventos P6-05 y una politica P6-04.

Solo lee artefactos ya persistidos. No ejecuta el detector, no aplica NMS, no
reclasifica scores, no modifica eventos ni capturas y no trata la falta de
evidencia como normalidad.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from alertas import (
    ConfiguracionAlertas,
    ErrorAlertas,
    ejecutar_alertas,
    resumen_alertas,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("datos_locales"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--low-min-detections", type=int, default=1)
    parser.add_argument("--low-window-events", type=int, default=1)
    parser.add_argument("--low-min-events-with-low", type=int, default=1)
    parser.add_argument("--generated-at")
    args = parser.parse_args()
    try:
        configuracion = ConfiguracionAlertas(
            low_min_detections=args.low_min_detections,
            low_window_events=args.low_window_events,
            low_min_events_with_low=args.low_min_events_with_low,
        )
        ruta = ejecutar_alertas(
            args.root,
            args.run_id,
            policy_path=args.policy,
            configuracion=configuracion,
            generado_en=args.generated_at,
        )
        resumen = resumen_alertas(args.root, args.run_id)
    except (ErrorAlertas, ValueError, OSError, json.JSONDecodeError) as error:
        print(
            json.dumps(
                {"error": f"{type(error).__name__}: {error}"}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "alerts": str(ruta),
                "summary": resumen,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
