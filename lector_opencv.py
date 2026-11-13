"""Adaptador mínimo de OpenCV para la señal local de captura de pantalla.

Todavía no realiza inferencia: será la entrada del pipeline del Proyecto 6.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import time


def leer_cuadros(url: str):
    """Entrega (número, instante de recepción UTC, cuadro BGR) a la aplicación."""
    import cv2

    video = cv2.VideoCapture(url)
    if not video.isOpened():
        raise RuntimeError(f"No se pudo abrir la señal local: {url}")
    try:
        numero = 0
        while True:
            correcto, cuadro = video.read()
            if not correcto:
                break
            numero += 1
            yield numero, datetime.now(timezone.utc), cuadro
    finally:
        video.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765/stream.mjpg")
    parser.add_argument(
        "--seconds", type=float, default=0.0,
        help="Duración de la prueba; 0 significa hasta pulsar q o Ctrl+C",
    )
    parser.add_argument(
        "--no-preview", action="store_true",
        help="No abrir ventana; mostrar solo el conteo de cuadros",
    )
    args = parser.parse_args()
    if args.seconds < 0:
        parser.error("--seconds no puede ser negativo")

    import cv2

    inicio = time.monotonic()
    recibidos = 0
    ventana = "Proyecto 6 - video recibido por OpenCV"
    ventana_abierta = False
    try:
        for numero, instante, cuadro in leer_cuadros(args.url):
            recibidos = numero
            alto, ancho = cuadro.shape[:2]
            if numero == 1 or numero % 10 == 0:
                print(f"{numero} | {instante.isoformat()} | {ancho}x{alto}")
            if not args.no_preview:
                if not ventana_abierta:
                    cv2.namedWindow(ventana, cv2.WINDOW_NORMAL)
                    cv2.resizeWindow(ventana, min(ancho, 960), min(alto, 540))
                    ventana_abierta = True
                cv2.imshow(ventana, cuadro)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            if args.seconds and time.monotonic() - inicio >= args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if ventana_abierta:
            cv2.destroyAllWindows()
    print(f"Cuadros recibidos: {recibidos}. No se guardó ningún archivo.")


if __name__ == "__main__":
    main()
