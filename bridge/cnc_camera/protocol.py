"""
Protocol between the OpenMV Cam (openmv/main.py) and the bridge.

12 byte header:
IE: {"id": 3, "cmd": "set", "framesize": "VGA", "quality": 60, "fps": 15}

This file must be kept in sync with openmv/main.py
Parts of this file were written by Claude
"""
from __future__ import annotations
import json
import struct
from dataclasses import dataclass
from typing import Iterator

MAGIC = b"CNCV"
PROTOCOL_VERSION = 1
HEADER = struct.Struct("<4sBBHI")
HEADER_SIZE = HEADER.size  # 12 byte
MAX_PAYLOAD = 4 * 1024 * 1024  # 5 MP JPEG falls within this


class PacketType:
    FRAME_JPEG = 0x01  # livestream frame
    SNAPSHOT_JPEG = 0x02  # high-res image for processing
    EVENT = 0x10  # result from an on-camera analyzer
    INFO = 0x11  # telemetry
    LOG = 0x12  # UTF-8
    ACK = 0x13  # response to a host command

    NAMES = {0x01: "frame", 0x02: "snapshot", 0x10: "event", 0x11: "info", 0x12: "log", 0x13: "ack",}


@dataclass(frozen=True)
class Packet:
    type: int
    seq: int
    payload: bytes

    @property
    def type_name(self) -> str:
        return PacketType.NAMES.get(self.type, f"0x{self.type:02x}")

    def json(self) -> dict:
        return json.loads(self.payload.decode("utf-8"))


def encode_packet(ptype: int, seq: int, payload: bytes) -> bytes:
    """Used by tests and the emulator; the camera builds the same bytes."""
    return HEADER.pack(MAGIC, PROTOCOL_VERSION, ptype, seq & 0xFFFF, len(payload)) + payload


def encode_command(cmd: dict) -> bytes:
    return (json.dumps(cmd, separators=(",", ":")) + "\n").encode("utf-8")


def looks_like_jpeg(data: bytes) -> bool:
    return len(data) > 4 and data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9"


class StreamParser:
    """Incremental parser. Feed it arbitrary byte chunks, get whole packets out.

    Resynchronises on the magic marker, so a partial frame (camera reset,
    cable glitch, host attached mid-frame) costs at most one frame.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.bytes_discarded = 0
        self.bad_headers = 0

    def feed(self, data: bytes) -> Iterator[Packet]:
        self._buf.extend(data)
        while True:
            start = self._buf.find(MAGIC)
            if start < 0:
                # keep the last 3 bytes in case the magic straddles chunks
                keep = len(MAGIC) - 1
                if len(self._buf) > keep:
                    self.bytes_discarded += len(self._buf) - keep
                    del self._buf[:-keep]
                return
            if start > 0:
                self.bytes_discarded += start
                del self._buf[:start]
            if len(self._buf) < HEADER_SIZE:
                return
            _magic, version, ptype, seq, length = HEADER.unpack_from(self._buf, 0)
            if version != PROTOCOL_VERSION or length > MAX_PAYLOAD:
                # not a real header (or incompatible firmware); skip this magic
                self.bad_headers += 1
                del self._buf[:1]
                continue
            total = HEADER_SIZE + length
            if len(self._buf) < total:
                return
            payload = bytes(self._buf[HEADER_SIZE:total])
            del self._buf[:total]
            yield Packet(ptype, seq, payload)
