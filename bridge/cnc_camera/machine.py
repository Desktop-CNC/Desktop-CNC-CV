"""
Read only link to UGS, IE: UGS can interact with the camera

UGS sends a REST API request on port 8080 when the server is
started from UGS (Machine > Start pendant). The camera uses it to know what
the machine is actively up to, IE: check the tool when the spindle is stopped at X pos

Disabled by default unless the ugs_pendant_url is set
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional
import aiohttp
from .events import EventBus

log = logging.getLogger(__name__)


class MachineState:
    def __init__(self, base_url: str, events: EventBus, interval_s: float = 1.0):
        self.base_url = base_url.rstrip("/")
        self.events = events
        self.interval_s = interval_s
        self.latest: dict = {}
        self.connected = False
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="ugs-poller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        url = f"{self.base_url}/api/v1/status/getStatus"
        timeout = aiohttp.ClientTimeout(total=2)
        last_state = None
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                try:
                    async with session.get(url) as resp:
                        resp.raise_for_status()
                        self.latest = await resp.json()
                        self.connected = True
                except Exception:  # noqa: BLE001
                    self.connected = False
                    self.latest = {}
                state = self.latest.get("state") if self.connected else "DISCONNECTED"
                if state != last_state:
                    self.events.emit({"type": "machine.state", "state": state})
                    last_state = state
                await asyncio.sleep(self.interval_s)
