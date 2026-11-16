"""Synthetic camera for development without hardware (and for automated tests). This section was written by claude"""
from __future__ import annotations
import math
import time
import cv2
import numpy as np
from .base import CameraSource


class TestPatternSource(CameraSource):
    kind = "test"

    def open(self) -> None:
        self._t0 = time.monotonic()
        self._next = time.monotonic()
        self.info = {"pattern": "moving tool + grid"}

    def read_frame(self):
        now = time.monotonic()
        if now < self._next:
            time.sleep(min(self._next - now, 0.05))
            return None
        self._next = now + 1.0 / max(self.cfg.fps, 0.1)

        w, h = self.cfg.width, self.cfg.height
        img = np.full((h, w, 3), 35, np.uint8)
        for x in range(0, w, 40):
            cv2.line(img, (x, 0), (x, h), (60, 60, 60), 1)
        for y in range(0, h, 40):
            cv2.line(img, (0, y), (w, y), (60, 60, 60), 1)
        t = now - self._t0
        cx = int(w / 2 + (w / 3) * math.sin(t * 0.8))
        cy = int(h / 2 + (h / 4) * math.sin(t * 1.3))
        # a crude "end mill" so the pattern looks like the spindle moving
        cv2.rectangle(img, (cx - 12, 0), (cx + 12, cy - 40), (160, 160, 170), -1)
        cv2.rectangle(img, (cx - 6, cy - 40), (cx + 6, cy), (200, 200, 210), -1)
        cv2.putText(img, f"TEST PATTERN  {time.strftime('%H:%M:%S')}  #{self._seq + 1}", (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (18, 156, 243), 1, cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(self.cfg.jpeg_quality)])
        return self.make_frame(buf.tobytes(), w, h)

    def close(self) -> None:
        pass
