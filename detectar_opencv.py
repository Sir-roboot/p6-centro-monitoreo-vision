"""Ejecuta YOLOX-Tiny en CPU sobre los cuadros seleccionados de una fuente.

Imprime una linea JSON por cuadro. Solo detecta: no calcula metricas, eventos,
capturas ni alertas, que pertenecen a etapas posteriores.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import time

from detector_yolox import (
    RUTA_PESOS,
    UMBRAL_NMS_PREDETERMINADO,
    UMBRAL_SCORE_PREDETERMINADO,
    ResultadoCuadro,
    cargar_detector,
)
from fuente import leer_fuente


def _dibujar(imagen, resultado: ResultadoCuadro):
    import cv2

    copia = imagen.copy()
    for deteccion in resultado.detections:
        x1, y1, x2, y2 = (int(round(valor)) for valor in deteccion.bbox_xyxy)
        cv2.rectangle(copia, (x1, y1), (x2, y2), (0, 220, 0), 2)
        etiqueta = f"{deteccion.class_name} {deteccion.confidence:.2f}"
        cv2.putText(
            copia,
            etiqueta,
            (x1, max(14, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 220, 0),
            1,
            cv2.LINE_AA,
        )
    return copia


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
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--show-discards", action="store_true")
    args = parser.parse_args()
    if args.seconds < 0:
        parser.error("--seconds no puede ser negativo")
    if args.sample_every < 1:
        parser.error("--sample-every debe ser >= 1")
    if args.interval_ms < 0:
        parser.error("--interval-ms no puede ser negativo")
    if args.max_frames is not None and args.max_frames < 1:
        parser.error("--max-frames debe ser >= 1")

    import cv2

    detector = cargar_detector(
        args.weights,
        score_threshold=args.score_threshold,
        nms_threshold=args.nms_threshold,
        descargar=not args.no_download,
    )

    source_counts = {"received": 0, "sampled": 0, "discarded": 0, "read_errors": 0}
    detector_counts = {"ok": 0, "error": 0}
    class_counts: Counter[str] = Counter()
    total_detections = 0
    end_state = "max_duration" if args.seconds else "max_frames"
    window = "Proyecto 6 - YOLOX-Tiny CPU"
    preview_open = False

    def cerrar_preview() -> None:
        if preview_open:
            cv2.destroyAllWindows()

    started = time.monotonic()
    try:
        for observation in leer_fuente(
            args.source,
            source_id=args.source_id,
            sample_every=args.sample_every,
            interval_ms=args.interval_ms,
            max_frames=args.max_frames,
        ):
            if observation.status == "end_of_stream":
                end_state = "end_of_stream"
            elif observation.status == "read_error":
                source_counts["read_errors"] += 1
                end_state = observation.discard_reason or "read_error"
            else:
                source_counts["received"] += 1
                source_counts[observation.status] += 1

            resultado = None
            if observation.status == "sampled" and observation.image is not None:
                resultado = detector.detectar(
                    observation.image,
                    source_id=observation.source_id,
                    frame_id=observation.frame_id or f"{observation.source_id}-unknown",
                    frame_number=observation.frame_number,
                    received_at=observation.received_at,
                )
                detector_counts[resultado.status] += 1
                if resultado.status == "ok":
                    total_detections += len(resultado.detections)
                    class_counts.update(resultado.class_counts)
                print(json.dumps(resultado.to_dict(), ensure_ascii=False), flush=True)
            elif observation.status != "discarded" or args.show_discards:
                print(json.dumps(observation.to_dict(), ensure_ascii=False), flush=True)

            if resultado is not None and not args.no_preview:
                if not preview_open:
                    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
                    alto = resultado.image_height or 540
                    ancho = resultado.image_width or 960
                    cv2.resizeWindow(window, min(ancho, 960), min(alto, 540))
                    preview_open = True
                assert observation.image is not None
                cv2.imshow(window, _dibujar(observation.image, resultado))
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    end_state = "user_stop"
                    break
            if observation.status in {"end_of_stream", "read_error"}:
                break
            if args.seconds and time.monotonic() - started >= args.seconds:
                end_state = "max_duration"
                break
    except KeyboardInterrupt:
        end_state = "user_stop"
    finally:
        cerrar_preview()

    resumen = {
        "source": source_counts,
        "detector": detector_counts,
        "detections": total_detections,
        "class_counts": dict(sorted(class_counts.items())),
        "end_state": end_state,
    }
    print(json.dumps({"summary": resumen}, ensure_ascii=False), flush=True)
    fallos = source_counts["read_errors"] + detector_counts["error"]
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
