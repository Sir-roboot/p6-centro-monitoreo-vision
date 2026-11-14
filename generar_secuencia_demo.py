"""Genera una secuencia sintetica propia para probar la entrada visual.

No representa escenas reales ni acredita la calidad de un detector.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def generar(destino: Path, *, cantidad: int = 12) -> list[Path]:
    if cantidad < 1:
        raise ValueError("cantidad debe ser positiva")
    destino.mkdir(parents=True, exist_ok=True)
    archivos = [destino / f"frame_{numero:04d}.png" for numero in range(1, cantidad + 1)]
    existentes = [str(archivo) for archivo in archivos if archivo.exists()]
    if existentes:
        raise FileExistsError(f"No se sobrescriben archivos existentes: {existentes[0]}")

    for numero, archivo in enumerate(archivos, start=1):
        imagen = np.full((240, 320, 3), (125, 155, 180), dtype=np.uint8)
        cv2.rectangle(imagen, (0, 125), (319, 239), (65, 65, 65), -1)
        cv2.line(imagen, (160, 130), (160, 239), (215, 215, 215), 2)
        desplazamiento = numero * 12
        cv2.rectangle(imagen, (20 + desplazamiento, 170), (82 + desplazamiento, 204), (10, 40, 220), -1)
        cv2.putText(imagen, "DEMO SINTETICA", (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
        cv2.putText(imagen, f"frame {numero:04d}", (15, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        if not cv2.imwrite(str(archivo), imagen):
            raise OSError(f"No se pudo escribir {archivo}")
    return archivos


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("datos_locales/secuencia_demo"))
    parser.add_argument("--count", type=int, default=12)
    args = parser.parse_args()
    archivos = generar(args.output, cantidad=args.count)
    print(f"Generados {len(archivos)} cuadros sinteticos en {args.output.resolve()}")


if __name__ == "__main__":
    main()
