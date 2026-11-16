"""
OpenMV Cam H7 Plus source

The camera runs openmv/main.py standalone (saved to camera flash as main.py)
and pushes JPEG frames + JSON telemetry over USB using
the framing in cnc_camera.protocol. JPEGs are passed straight through to
viewers, so the CPU doesn't have to encode/decode.

USB on the H7 Plus specs:
    (12 Mbit/s, ~0.8-1 MB/s usable).
    Budget:
    VGA q50 ~25-35 KB -> ~20-30 fps
    HD q50 ~60-90 KB -> ~10 fps
"""
from __future__ import annotations
import concurrent.futures
import itertools
import logging
import threading
import time
from typing import Optional
import serial
from serial.tools import list_ports
from ..frames import Frame
from ..protocol import PacketType, StreamParser, encode_command, looks_like_jpeg
from .base import CameraSource, SourceUnavailable

log = logging.getLogger(__name__)

OPENMV_USB_IDS = {(0x1209, 0xABD1), (0x37C5, 0x124A), (0x37C5, 0x1204)}
OPENMV_VENDORS = {0x37C5}
SAFE_BAUD = 115200

def find_openmv_ports() -> list[dict]:
    out = []
    for p in list_ports.comports():
        ids = (p.vid, p.pid)
        if ids in OPENMV_USB_IDS or p.vid in OPENMV_VENDORS or "openmv" in (p.manufacturer or "").lower():
            out.append({"port": p.device, "description": p.description, "serial": p.serial_number, "vid_pid": f"{p.vid:04X}:{p.pid:04X}" if p.vid else None})
    return out


class OpenMVSource(CameraSource):
    kind = "openmv"
    capabilities = ("command", "hires_snapshot", "on_camera_analysis")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ser: Optional[serial.Serial] = None
        self._parser = StreamParser()
        self._write_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending: dict[int, concurrent.futures.Future] = {}
        self._snapshot_waiters: list[concurrent.futures.Future] = []
        self._last_frame = 0.0
        self._last_cam_seq: Optional[int] = None
        self.dropped_frames = 0

    # Connection stuff
    def _resolve_port(self) -> str:
        if self.cfg.port and self.cfg.port != "auto":
            return self.cfg.port
        ports = find_openmv_ports()
        if not ports:
            raise SourceUnavailable("no OpenMV cam found on USB")
        return ports[0]["port"]

    def open(self) -> None:
        port = self._resolve_port()
        try:
            ser = serial.Serial(port, SAFE_BAUD, timeout=0.2, write_timeout=1.0)
        except serial.SerialException as exc:
            raise SourceUnavailable(f"cannot open {port}: {exc}") from exc
        try:
            ser.dtr = True
        except (OSError, serial.SerialException):
            pass  # the emulator has no modem lines
        ser.reset_input_buffer()
        self._ser = ser
        self._parser = StreamParser()
        self._last_frame = time.monotonic()
        self._last_cam_seq = None
        self.info = {"port": port}
        self._send({"cmd": "hello"})
        self._send({
            "cmd": "set",
            "framesize": self.cfg.framesize,
            "pixformat": self.cfg.pixformat,
            "quality": int(self.cfg.jpeg_quality),
            "fps": float(self.cfg.fps),
            "hmirror": bool(self.cfg.hmirror),
            "vflip": bool(self.cfg.vflip),
        })
        self._send({"cmd": "stream", "enable": True})

    def close(self) -> None:
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        for fut in list(self._pending.values()) + self._snapshot_waiters:
            if not fut.done():
                fut.set_exception(ConnectionError("camera disconnected"))
        self._pending.clear()
        self._snapshot_waiters.clear()

    def read_frame(self) -> Optional[Frame]:
        ser = self._ser
        assert ser is not None
        data = ser.read(max(ser.in_waiting, 1))  # raises SerialException if cam is unplugged
        if not data:
            if time.monotonic() - self._last_frame > self.cfg.frame_timeout_s:
                raise RuntimeError(f"no frames for {self.cfg.frame_timeout_s:.0f}s "
                                   "(is openmv/main.py flashed to the camera)")
            return None
        frame = None
        for pkt in self._parser.feed(data):
            if self._last_cam_seq is not None:
                gap = (pkt.seq - self._last_cam_seq) & 0xFFFF
                if gap > 1:
                    self.dropped_frames += gap - 1
            self._last_cam_seq = pkt.seq
            result = self._handle(pkt)
            if result is not None:
                frame = result  # only publish the newest
        return frame

    def _handle(self, pkt) -> Optional[Frame]:
        if pkt.type == PacketType.FRAME_JPEG:
            if not looks_like_jpeg(pkt.payload):
                return None
            self._last_frame = time.monotonic()
            w, h = jpeg_size(pkt.payload)
            return self.make_frame(pkt.payload, w, h, cam_seq=pkt.seq)

        if pkt.type == PacketType.SNAPSHOT_JPEG:
            w, h = jpeg_size(pkt.payload)
            snap = self.make_frame(pkt.payload, w, h, hires=True)
            waiters, self._snapshot_waiters = self._snapshot_waiters, []
            for fut in waiters:
                if not fut.done():
                    fut.set_result(snap)
            return None

        if pkt.type in (PacketType.INFO, PacketType.EVENT, PacketType.ACK):
            try:
                body = pkt.json()
            except (ValueError, UnicodeDecodeError):
                return None
            if pkt.type == PacketType.INFO:
                self.info.update(body)
                self.events.emit_threadsafe({"type": "camera.info", "camera": self.cfg.id, **body})
            elif pkt.type == PacketType.EVENT:
                # results from analyzers
                self.events.emit_threadsafe({"type": "analysis", "camera": self.cfg.id, "origin": "camera", **body})
            else:
                fut = self._pending.pop(body.get("id"), None)
                if fut and not fut.done():
                    fut.set_result(body)
            return None

        if pkt.type == PacketType.LOG:
            log.info("[%s cam] %s", self.cfg.id, pkt.payload.decode("utf-8", "replace").rstrip())
        return None

    # commands
    def _send(self, cmd: dict) -> concurrent.futures.Future:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        ser = self._ser
        if ser is None:
            fut.set_exception(ConnectionError("camera not connected"))
            return fut
        cmd = dict(cmd)
        cmd["id"] = next(self._ids)
        self._pending[cmd["id"]] = fut
        with self._write_lock:
            ser.write(encode_command(cmd))
        return fut

    async def command(self, cmd: dict) -> dict:
        if "cmd" not in cmd:
            raise ValueError("missing 'cmd'")
        ack = await self._await_future(self._send(cmd), timeout=5.0)
        if cmd["cmd"] == "set" and ack.get("ok"):  # remember so a reconnect re-applies it
            mapping = {"framesize": "framesize", "pixformat": "pixformat", "quality": "jpeg_quality", "fps": "fps", "hmirror": "hmirror", "vflip": "vflip"}
            for k, attr in mapping.items():
                if k in cmd:
                    setattr(self.cfg, attr, cmd[k])
        return ack

    async def snapshot_hires(self, framesize: str = "QXGA", quality: int = 85) -> Frame:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._snapshot_waiters.append(fut)
        ack = await self._await_future(self._send({"cmd": "snapshot", "framesize": framesize, "quality": quality}), timeout=5.0)
        if not ack.get("ok", False):
            fut.cancel()
            raise RuntimeError(ack.get("error", "snapshot failed"))
        return await self._await_future(fut, timeout=15.0)

    def status(self) -> dict:
        st = super().status()
        st["info"] = {**self.info, "lost_packets": self.dropped_frames, "bad_headers": self._parser.bad_headers}
        return st

def jpeg_size(data: bytes) -> tuple[int, int]:
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h = (data[i + 5] << 8) | data[i + 6]
            w = (data[i + 7] << 8) | data[i + 8]
            return w, h
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = (data[i + 2] << 8) | data[i + 3]
        i += 2 + seg_len
    return 0, 0