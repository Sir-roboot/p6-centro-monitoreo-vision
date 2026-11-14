"""Lectura progresiva de MJPEG, video o secuencias de imagenes.

Cada entrada produce metadatos trazables. El muestreo no inventa cuadros ni
confianzas: los cuadros descartados se leen y se contabilizan por separado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Iterator

import cv2
import numpy as np


EXTENSIONES_IMAGEN = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass(frozen=True)
class ObservacionFuente:
    source_id: str
    frame_id: str | None
    frame_number: int | None
    received_at: str | None
    status: str
    discard_reason: str | None
    width: int | None
    height: int | None
    image: np.ndarray | None = None

    def to_dict(self) -> dict:
        """Contrato serializable; el arreglo de pixeles permanece en memoria."""
        return {
            "source_id": self.source_id,
            "frame_id": self.frame_id,
            "frame_number": self.frame_number,
            "received_at": self.received_at,
            "status": self.status,
            "discard_reason": self.discard_reason,
            "width": self.width,
            "height": self.height,
        }


def _instante() -> str:
    return datetime.now(timezone.utc).isoformat()


def _observacion(
    source_id: str, number: int | None, image: np.ndarray | None,
    sample_every: int, *, error: str | None = None,
) -> ObservacionFuente:
    if number is None:
        return ObservacionFuente(source_id, None, None, None, error or "end_of_stream", None, None, None)

    received_at = _instante()
    frame_id = f"{source_id}-{number:06d}"
    if error is not None:
        return ObservacionFuente(source_id, frame_id, number, received_at, "read_error", error, None, None)

    assert image is not None
    height, width = image.shape[:2]
    selected = (number - 1) % sample_every == 0
    return ObservacionFuente(
        source_id, frame_id, number, received_at,
        "sampled" if selected else "discarded",
        None if selected else "sampling_interval",
        width, height, image if selected else None,
    )


def leer_fuente(
    source: str, *, source_id: str, sample_every: int = 1,
    interval_ms: float = 200.0, max_frames: int | None = None,
) -> Iterator[ObservacionFuente]:
    """Lee cada cuadro; devuelve pixeles solo para los cuadros seleccionados.

    Las secuencias y archivos se acompasan con interval_ms. Un stream ya llega
    con su propio ritmo, por lo que no se retrasa artificialmente. Un EOF de
    archivo y una falla de lectura del stream son estados diferentes.
    """
    if not source_id or not source_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("source_id debe contener solo letras, digitos, guion o guion bajo")
    if sample_every < 1:
        raise ValueError("sample_every debe ser >= 1")
    if interval_ms < 0:
        raise ValueError("interval_ms no puede ser negativo")
    if max_frames is not None and max_frames < 1:
        raise ValueError("max_frames debe ser >= 1")

    path = Path(source)
    is_stream = source.startswith(("http://", "https://", "rtsp://"))

    if path.is_dir():
        images = sorted(p for p in path.iterdir() if p.suffix.lower() in EXTENSIONES_IMAGEN)
        if not images:
            yield _observacion(source_id, 1, None, sample_every, error="empty_sequence")
            return
        for number, image_path in enumerate(images, start=1):
            if max_frames is not None and number > max_frames:
                return
            image = cv2.imread(str(image_path))
            if image is None:
                yield _observacion(source_id, number, None, sample_every, error="invalid_image")
            else:
                yield _observacion(source_id, number, image, sample_every)
            if interval_ms and number < len(images):
                time.sleep(interval_ms / 1000)
        if max_frames is None or max_frames >= len(images):
            yield _observacion(source_id, None, None, sample_every)
        return

    if not is_stream and not path.is_file():
        yield _observacion(source_id, 1, None, sample_every, error="source_not_found")
        return

    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        yield _observacion(source_id, 1, None, sample_every, error="open_failed")
        return
    try:
        number = 0
        while max_frames is None or number < max_frames:
            ok, image = capture.read()
            if not ok:
                if is_stream:
                    yield _observacion(source_id, number + 1, None, sample_every, error="stream_read_failed")
                else:
                    yield _observacion(source_id, None, None, sample_every)
                break
            number += 1
            yield _observacion(source_id, number, image, sample_every)
            if not is_stream and interval_ms:
                time.sleep(interval_ms / 1000)
    finally:
        capture.release()
