"""Consulta eventos P6-05 y verifica que sus capturas siguen disponibles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from eventos import abrir_captura, consultar_eventos


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("datos_locales"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--event-id")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--verify-captures", action="store_true")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit debe ser >= 1")
    try:
        todos = consultar_eventos(args.root, args.run_id)
        seleccionados = [e for e in todos if e["event_id"] == args.event_id] if args.event_id else todos[:args.limit]
        if args.event_id and not seleccionados:
            raise ValueError(f"event_id no encontrado: {args.event_id}")
        verificacion = []
        if args.verify_captures:
            for evento in seleccionados:
                if evento["capture"] is not None:
                    datos = abrir_captura(args.root, evento)
                    verificacion.append({"event_id": evento["event_id"], "bytes": len(datos), "ok": True})
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"run_id": args.run_id, "total": len(todos), "events": seleccionados, "captures_verified": verificacion}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
