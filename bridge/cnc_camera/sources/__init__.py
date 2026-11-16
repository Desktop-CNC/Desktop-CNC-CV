from __future__ import annotations
from ..config import CameraConfig
from ..events import EventBus
from ..frames import FrameHub
from .base import CameraSource


def create_source(cfg: CameraConfig, hub: FrameHub, events: EventBus) -> CameraSource:
    if cfg.type == "webcam":
        from .webcam import WebcamSource
        return WebcamSource(cfg, hub, events)
    if cfg.type == "openmv":
        from .openmv import OpenMVSource
        return OpenMVSource(cfg, hub, events)
    if cfg.type == "test":
        from .testpattern import TestPatternSource
        return TestPatternSource(cfg, hub, events)
    raise ValueError(f"Unknown camera type {cfg.type!r} (expected webcam | openmv | test)")


__all__ = ["CameraSource", "create_source"]
