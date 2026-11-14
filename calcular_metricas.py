"""CLI P6-07: calcula G1-G7 sobre JSONL de observaciones, eventos y alertas."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile

from metricas import ESQUEMA, ErrorMetricas, calcular_metricas


def _escribir_atomico(ruta: Path, texto: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(
        prefix=f".{ruta.name}.", suffix=".tmp", dir=ruta.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as archivo:
            archivo.write(texto)
        os.replace(temporal, ruta)
    except BaseException:
        try:
            os.unlink(temporal)
        except OSError:
            pass
        raise


def _error(mensaje: str) -> int:
    print(
        json.dumps({"error": mensaje}, ensure_ascii=False, sort_keys=True),
        file=sys.stderr,
        flush=True,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observaciones", type=Path, required=True)
    parser.add_argument("--eventos", type=Path, required=True)
    parser.add_argument("--alertas", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--generated-at",
        help="instante ISO 8601 para un recálculo reproducible; por omisión, ahora UTC",
    )
    args = parser.parse_args(argv)

    try:
        reporte = calcular_metricas(
            ruta_observaciones=args.observaciones,
            ruta_eventos=args.eventos,
            ruta_alertas=args.alertas,
            root=args.root,
            generated_at=args.generated_at,
        )
    except (ErrorMetricas, OSError, ValueError) as error:
        return _error(f"{type(error).__name__}: {error}")

    texto = json.dumps(reporte, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(texto, end="", flush=True)
        return 0

    try:
        _escribir_atomico(args.output, texto)
    except OSError as error:
        return _error(f"{type(error).__name__}: {error}")

    print(
        json.dumps(
            {
                "output": str(args.output),
                "schema": ESQUEMA,
                "run_id": reporte["run_id"],
                "timestamp": reporte["timestamp"],
                "metricas": {
                    grupo: {
                        "valor": metrica["valor"],
                        "unidad": metrica["unidad"],
                        "estado": metrica["estado"],
                        "numerador": metrica["numerador"],
                        "denominador": metrica["denominador"],
                    }
                    for grupo, metrica in reporte["metricas"].items()
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
