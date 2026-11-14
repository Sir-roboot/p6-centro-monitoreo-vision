"""Recalcula los contadores primarios de un JSONL de P6-03 sin fuente ni modelo.

Sirve para auditar que el resumen almacenado sigue siendo reproducible a partir
de las observaciones. No calcula indicadores G1-G7 ni crea artefactos nuevos.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from observaciones import ErrorObservaciones, recalcular_resumen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", type=Path, required=True)
    args = parser.parse_args()

    try:
        resultado = recalcular_resumen(args.input)
    except (OSError, ValueError, ErrorObservaciones) as error:
        print(
            json.dumps({"error": f"{type(error).__name__}: {error}"}, ensure_ascii=False),
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(json.dumps(resultado, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if resultado["coincide"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
