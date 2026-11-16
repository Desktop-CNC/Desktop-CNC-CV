#USB / built-in webcam source via OpenCV. Used for testing
from __future__ import annotations

import glob
import platform
import time
from typing import Optional

import cv2

from .base import CameraSource, SourceUnavailable

_BACKENDS = {
    "auto": cv2.CAP_ANY,
    "v4l2": getattr(cv2, "CAP_V4L2", cv2.CAP_ANY),
    "dshow": getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY),
    "msmf": getattr(cv2, "CAP_MSMF", cv2.CAP_ANY),
    "avfoundation": getattr(cv2, "CAP_AVFOUNDATION", cv2.CAP_ANY),
}
_ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def _parse_device(device):
    if isinstance(device, str) and device.isdigit():
        return int(device)
    return device


def default_backend() -> int:
    if platform.system() == "Windows":
        return _BACKENDS["dshow"]
    if platform.system() == "Linux":
        return _BACKENDS["v4l2"]
    return cv2.CAP_ANY


def list_webcams(max_index: int = 8) -> list[dict]:
    found = []
    if platform.system() == "Linux":
        candidates = sorted(glob.glob("/dev/video*"))
    else:
        candidates = list(range(max_index))
    for dev in candidates:
        cap = cv2.VideoCapture(dev, default_backend())
        try:
            if cap.isOpened():
                ok, frame = cap.read()
                if ok and frame is not None:
                    h, w = frame.shape[:2]
                    found.append({"device": dev, "width": w, "height": h})
        finally:
            cap.release()
    return found


class WebcamSource(CameraSource):
    kind = "webcam"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cap: Optional[cv2.VideoCapture] = None
        self._failures = 0
        self._next_due = 0.0

    def open(self) -> None:
        dev = _parse_device(self.cfg.device)
        backend = _BACKENDS.get(self.cfg.backend, cv2.CAP_ANY) if self.cfg.backend != "auto" else default_backend()
        cap = cv2.VideoCapture(dev, backend)
        if not cap.isOpened():
            cap.release()
            raise SourceUnavailable(f"webcam {dev!r} not found")
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.height)
        cap.set(cv2.CAP_PROP_FPS, self.cfg.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1) # keep low latency
        self._cap = cap
        self._failures = 0
        self.info = {
            "device": str(dev),
            "backend": cap.getBackendName() if hasattr(cap, "getBackendName") else "",
            "negotiated": f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}"
                          f"@{cap.get(cv2.CAP_PROP_FPS):.0f}",
        }

    def read_frame(self):
        assert self._cap is not None
        ok, img = self._cap.read()  # always read never write
        if not ok or img is None:
            self._failures += 1
            if self._failures > 30:
                raise RuntimeError("webcam stopped delivering frames")
            time.sleep(0.03)
            return None
        self._failures = 0

        now = time.monotonic()
        if now < self._next_due:
            return None
        self._next_due = now + 1.0 / max(self.cfg.fps, 0.1)

        if self.cfg.rotate in _ROTATE:
            img = cv2.rotate(img, _ROTATE[self.cfg.rotate])
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(self.cfg.jpeg_quality)])
        if not ok:
            return None
        h, w = img.shape[:2]
        return self.make_frame(buf.tobytes(), w, h)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
