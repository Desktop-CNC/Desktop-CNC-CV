"Hub that that feeds data to the viewers and analyzers."
from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    width: int
    height: int
    seq: int
    timestamp: float  # unix seconds at capture time
    camera_id: str
    placeholder: bool = False  # true when testing without cam
    meta: dict = field(default_factory=dict)


class FrameHub:
    "Single producer, many consumers, always latest-frame. Slow consumers skip frames so that the stream is never stalled"

    def __init__(self, camera_id: str, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.camera_id = camera_id
        self._loop = loop
        self._latest: Optional[Frame] = None
        self._event = asyncio.Event()
        self._fps = 0.0
        self._last_ts: Optional[float] = None
        self.frames_published = 0
        self.consumers = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # PRODUCEr #
    def publish_threadsafe(self, frame: Frame) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self.publish, frame)

    def publish(self, frame: Frame) -> None:
        if not frame.placeholder:
            now = time.monotonic()
            if self._last_ts is not None:
                dt = now - self._last_ts
                if dt > 0:
                    inst = 1.0 / dt
                    self._fps = inst if self._fps == 0 else 0.9 * self._fps + 0.1 * inst
            self._last_ts = now
            self.frames_published += 1
        self._latest = frame
        old, self._event = self._event, asyncio.Event()
        old.set()

    # CONSUMER #
    @property
    def latest(self) -> Optional[Frame]:
        return self._latest

    @property
    def fps(self) -> float:
        # decay to 0 if frames stopped arriving
        if self._last_ts is None or time.monotonic() - self._last_ts > 3:
            return 0.0
        return round(self._fps, 1)

    async def wait_next(self, after_seq: Optional[int] = None, timeout: Optional[float] = None) -> Optional[Frame]:
        f = self._latest
        if f is not None and (after_seq is None or f.seq != after_seq):
            return f
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except asyncio.TimeoutError:
            return None
        return self._latest

    async def frames(self, max_fps: Optional[float] = None) -> AsyncIterator[Frame]:
        min_interval = 1.0 / max_fps if max_fps and max_fps > 0 else 0.0
        last_seq: Optional[int] = None
        last_sent = 0.0
        self.consumers += 1
        try:
            while True:
                frame = await self.wait_next(last_seq, timeout=5.0)
                if frame is None:
                    continue
                if frame.seq == last_seq:
                    continue
                if min_interval:
                    wait = min_interval - (time.monotonic() - last_sent)
                    if wait > 0:
                        await asyncio.sleep(wait)
                        frame = self._latest or frame
                last_seq = frame.seq
                last_sent = time.monotonic()
                yield frame
        finally:
            self.consumers -= 1
