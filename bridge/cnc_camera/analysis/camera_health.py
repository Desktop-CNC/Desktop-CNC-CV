"""
Reference analyzer:
Flags a covered lens, no lights, or a frozen camera
exists mainly as an example of the Analyzer API.
"""
from __future__ import annotations

import cv2
import numpy as np

from .base import AnalysisContext, Analyzer, register


@register("camera_health")
class CameraHealth(Analyzer):
    interval_s = 2.0

    def __init__(self, **options):
        super().__init__(**options)
        self.dark_level = options.get("dark_level", 25)
        self.blur_threshold = options.get("blur_threshold", 15)
        self.frozen_frames = options.get("frozen_frames", 5)
        self._prev = None
        self._same = 0
        self._last_state = None

    def process(self, frame, image, ctx: AnalysisContext):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (160, 120), interpolation=cv2.INTER_AREA)
        mean = float(small.mean())
        sharpness = float(cv2.Laplacian(small, cv2.CV_64F).var())

        if self._prev is not None and np.abs(small.astype(np.int16) - self._prev).mean() < 0.5:
            self._same += 1
        else:
            self._same = 0
        self._prev = small.astype(np.int16)

        if mean < self.dark_level:
            state = "dark"
        elif sharpness < self.blur_threshold:
            state = "obstructed_or_blurred"
        elif self._same >= self.frozen_frames:
            state = "frozen"
        else:
            state = "ok"

        if state == self._last_state:
            return []
        self._last_state = state
        return [{"kind": "camera_health", "state": state,
                 "severity": "info" if state == "ok" else "warn",
                 "brightness": round(mean, 1), "sharpness": round(sharpness, 1)}]
