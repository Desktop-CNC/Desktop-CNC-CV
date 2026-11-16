"""
Renders the 'camera offline / connecting' card shown in the stream.
This part was written by claude
"""
from __future__ import annotations

import functools
import time

import cv2
import numpy as np


@functools.lru_cache(maxsize=32)
def _render(title: str, message: str, width: int, height: int) -> bytes:
    width, height = max(width, 320), max(height, 240)
    img = np.full((height, width, 3), (47, 37, 26), np.uint8)  # site navy (BGR)
    scale = width / 640
    cv2.putText(img, title, (int(24 * scale), int(60 * scale)), cv2.FONT_HERSHEY_SIMPLEX,
                0.9 * scale, (18, 156, 243), max(1, int(2 * scale)), cv2.LINE_AA)
    y = int(110 * scale)
    for line in _wrap(message, 44):
        cv2.putText(img, line, (int(24 * scale), y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6 * scale, (240, 240, 240), max(1, int(1.5 * scale)), cv2.LINE_AA)
        y += int(30 * scale)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes()


def _wrap(text: str, n: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:6]


def placeholder_jpeg(title: str, message: str, width: int, height: int) -> tuple[bytes, int, int]:
    # the time bucket makes the card refresh at most every 10 s with a new timestamp
    stamp = time.strftime("%H:%M:%S", time.localtime((int(time.time()) // 10) * 10))
    jpeg = _render(title, f"{message} ({stamp})", width, height)
    return jpeg, max(width, 320), max(height, 240)
