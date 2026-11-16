"""
Base class for camera sources, see webcam.py or openmv.py for more examples
"""
from __future__ import annotations

import abc
import asyncio
import logging
import threading
import time
from typing import Any, Optional

from ..config import CameraConfig
from ..events import EventBus
from ..frames import Frame, FrameHub
from .placeholder import placeholder_jpeg

log = logging.getLogger(__name__)


class SourceUnavailable(RuntimeError):
    """runs if the camera isn't detected"""


class CameraSource(abc.ABC):
    kind = "base"

    def __init__(self, cfg: CameraConfig, hub: FrameHub, events: EventBus):
        self.cfg = cfg
        self.hub = hub
        self.events = events
        self.state = "stopped"  # stopped | connecting | streaming | error
        self.error: Optional[str] = None
        self.info: dict[str, Any] = {}  # device details reported by the source
        self._seq = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # To implement yourself
    @abc.abstractmethod
    def open(self) -> None: ...

    @abc.abstractmethod
    def read_frame(self) -> Optional[Frame]:
        """Block until the next frame (or return None to just loop again).
        Raise on fatal errors to force a reconnect."""

    @abc.abstractmethod
    def close(self) -> None: ...

    # optional parts
    capabilities: tuple[str, ...] = ()

    async def command(self, cmd: dict) -> dict:
        raise NotImplementedError(f"{self.kind} source does not accept commands")

    async def snapshot_hires(self, **kwargs) -> Frame:
        raise NotImplementedError(f"{self.kind} source has no high-resolution snapshot")

    # Lifecycle
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"cam-{self.cfg.id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self._set_state("stopped")

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def make_frame(self, jpeg: bytes, width: int, height: int, **meta) -> Frame:
        return Frame(jpeg=jpeg, width=width, height=height, seq=self.next_seq(),
                     timestamp=time.time(), camera_id=self.cfg.id, meta=meta)

    def status(self) -> dict:
        latest = self.hub.latest
        return {
            "id": self.cfg.id,
            "name": self.cfg.name,
            "type": self.kind,
            "state": self.state,
            "error": self.error,
            "fps": self.hub.fps,
            "viewers": self.hub.consumers,
            "width": latest.width if latest and not latest.placeholder else None,
            "height": latest.height if latest and not latest.placeholder else None,
            "last_frame_ts": latest.timestamp if latest and not latest.placeholder else None,
            "capabilities": list(self.capabilities),
            "info": self.info,
        }

    def _set_state(self, state: str, error: Optional[str] = None) -> None:
        if state == self.state and error == self.error:
            return
        self.state, self.error = state, error
        self.events.emit_threadsafe({"type": "camera.status", "camera": self.cfg.id,
                                     "state": state, "error": error})
        if state in ("connecting", "error"):
            msg = "Connecting to camera..." if state == "connecting" else f"Camera offline: {error}"
            jpeg, w, h = placeholder_jpeg(self.cfg.name, msg, self.cfg.width, self.cfg.height)
            self.hub.publish_threadsafe(Frame(jpeg, w, h, self.next_seq(), time.time(),
                                              self.cfg.id, placeholder=True))

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            if self.state != "error":
                self._set_state("connecting")
            try:
                self.open()
                self._set_state("streaming")
                backoff = 1.0
                while not self._stop.is_set():
                    frame = self.read_frame()
                    if frame is not None:
                        self.hub.publish_threadsafe(frame)
            except Exception as exc:
                msg = str(exc) or exc.__class__.__name__
                if not isinstance(exc, SourceUnavailable):
                    log.warning("[%s] capture error: %s", self.cfg.id, msg)
                self._set_state("error", msg)
            finally:
                try:
                    self.close()
                except Exception:
                    pass
            if not self._stop.is_set():
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 10.0)

    # helper for async callers
    @staticmethod
    async def _await_future(fut, timeout: float):
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout)
