"""Consulta alertas P6-06 persistidas y verifica su evidencia de origen."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from alertas import (
    ErrorAlertas,
    leer_manifiesto_alertas,
    listar_runs_con_alertas,
    resumen_alertas,
    verificar_evidencia_alertas,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("datos_locales"))
    parser.add_argument("--run-id")
    parser.add_argument("--list-runs", action="store_true")
    parser.add_argument("--severity")
    parser.add_argument("--rule-id")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit debe ser >= 1")
    if args.list_runs:
        print(
            json.dumps(
                {"runs": listar_runs_con_alertas(args.root)}, ensure_ascii=False
            )
        )
        return 0
    if not args.run_id:
        parser.error("--run-id es obligatorio salvo con --list-runs")
    try:
        leido = leer_manifiesto_alertas(args.root, args.run_id)
        alertas = leido["alerts"]
        if args.severity:
            alertas = [a for a in alertas if a["severity"] == args.severity]
        if args.rule_id:
            alertas = [a for a in alertas if a["rule_id"] == args.rule_id]
        verificacion = None
        if args.verify:
            verificacion = verificar_evidencia_alertas(args.root, args.run_id)
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
                "total": len(alertas),
                "alerts": alertas[: args.limit],
                "manifest": leido["manifest"],
                "summary": resumen_alertas(args.root, args.run_id),
                "verification": verificacion,
            },
            ensure_ascii=False,
        )
    )
    return 0 if verificacion is None or verificacion["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
