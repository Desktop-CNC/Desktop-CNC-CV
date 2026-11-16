from __future__ import annotations
import abc
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional
import cv2
import numpy as np
from ..events import EventBus
from ..frames import Frame, FrameHub

log = logging.getLogger(__name__)
_REGISTRY: dict[str, type["Analyzer"]] = {}


def register(name: str):
    def deco(cls):
        cls.name = name
        _REGISTRY[name] = cls
        return cls
    return deco


def available() -> list[str]:
    return sorted(_REGISTRY)


@dataclass
class AnalysisContext:
    camera_id: str
    machine: dict = field(default_factory=dict)  # latest UGS status (may be empty)


class Analyzer(abc.ABC):
    # Runs on the bridge (Raspberry Pi). the Pi also runs UGS. Heavy analyzers run on the OpenMV or on a separate machine subscribing to the stream.

    name = "base"
    interval_s: float = 1.0

    def __init__(self, **options):
        self.options = options

    @abc.abstractmethod
    def process(self, frame: Frame, image: np.ndarray, ctx: AnalysisContext) -> list[dict]:
        """run events here"""

class AnalysisRunner:
    #One per camera, analyzers run off the event loop."""

    def __init__(self, camera_id: str, hub: FrameHub, events: EventBus,
                 analyzers: list[Analyzer], machine_state=None):
        self.camera_id = camera_id
        self.hub = hub
        self.events = events
        self.analyzers = analyzers
        self.machine_state = machine_state
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self.analyzers:
            self._task = asyncio.create_task(self._run(), name=f"analysis-{self.camera_id}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        tick = min(a.interval_s for a in self.analyzers)
        due = {a.name: 0.0 for a in self.analyzers}
        last_seq = None
        while True:
            frame = await self.hub.wait_next(last_seq, timeout=5)
            if frame is None or frame.placeholder:
                await asyncio.sleep(tick)
                continue
            last_seq = frame.seq
            now = loop.time()
            ready = [a for a in self.analyzers if now >= due[a.name]]
            if ready:
                image = await loop.run_in_executor(None, _decode, frame.jpeg)
                ctx = AnalysisContext(self.camera_id, dict(self.machine_state.latest) if self.machine_state else {})
                for a in ready:
                    due[a.name] = now + a.interval_s
                    try:
                        results = await loop.run_in_executor(None, a.process, frame, image, ctx)
                    except Exception:
                        log.exception("analyzer %s failed", a.name)
                        continue
                    for ev in results or []:
                        self.events.emit({"type": "analysis", "camera": self.camera_id, "origin": "host", "analyzer": a.name, "frame_seq": frame.seq, **ev})
            await asyncio.sleep(tick)


def _decode(jpeg: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)