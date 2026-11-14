"""Verifica la fuente visual y el muestreo antes de conectar el detector."""

from __future__ import annotations

import argparse
import json
import time

from fuente import leer_fuente


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", "--url", default="http://127.0.0.1:8765/stream.mjpg")
    parser.add_argument("--source-id", default="monitor-local")
    parser.add_argument("--sample-every", type=int, default=1)
    parser.add_argument("--interval-ms", type=float, default=200.0)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--seconds", type=float, default=0.0)
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

    counts = {"received": 0, "sampled": 0, "discarded": 0, "read_errors": 0}
    end_state = "max_duration" if args.seconds else "max_frames"
    started = time.monotonic()
    window = "Proyecto 6 - cuadros muestreados"
    preview_open = False
    try:
        for observation in leer_fuente(
            args.source, source_id=args.source_id,
            sample_every=args.sample_every, interval_ms=args.interval_ms,
            max_frames=args.max_frames,
        ):
            if observation.status == "end_of_stream":
                end_state = "end_of_stream"
            elif observation.status == "read_error":
                counts["read_errors"] += 1
                end_state = observation.discard_reason or "read_error"
            else:
                counts["received"] += 1
                counts[observation.status] += 1

            if observation.status != "discarded" or args.show_discards:
                print(json.dumps(observation.to_dict(), ensure_ascii=False), flush=True)

            if observation.image is not None and not args.no_preview:
                if not preview_open:
                    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
                    cv2.resizeWindow(window, min(observation.width or 960, 960), min(observation.height or 540, 540))
                    preview_open = True
                cv2.imshow(window, observation.image)
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
        if preview_open:
            cv2.destroyAllWindows()

    print(json.dumps({"summary": counts, "end_state": end_state}, ensure_ascii=False), flush=True)
    return 1 if counts["read_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
