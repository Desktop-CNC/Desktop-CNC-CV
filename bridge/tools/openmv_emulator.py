"""Pretend to be an OpenMV Cam running openmv/main.py, on a virtual serial port.

Lets you develop and test the OpenMV code path without the hardware arrives:

    python tools/openmv_emulator.py               # prints e.g. /dev/pts/5
    python -m cnc_camera --source openmv --port /dev/pts/5

Linux/macOS only (uses a pseudo-terminal). Uses a webcam as the image source
if --webcam N is given, otherwise a synthetic pattern.
This part was developed by claude
"""
from __future__ import annotations

import argparse
import json
import os
import pty
import select
import sys
import time
import tty
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cnc_camera.protocol import PacketType, encode_packet  # noqa: E402

SIZES = {"QQVGA": (160, 120), "QVGA": (320, 240), "VGA": (640, 480), "SVGA": (800, 600),
         "XGA": (1024, 768), "HD": (1280, 720), "SXGA": (1280, 1024), "UXGA": (1600, 1200),
         "FHD": (1920, 1080), "QXGA": (2048, 1536), "WQXGA2": (2592, 1944)}


class Emulator:
    def __init__(self, fd: int, webcam=None, link_bytes_per_s: float = 900_000):
        self.fd = fd
        self.cap = cv2.VideoCapture(webcam) if webcam is not None else None
        self.state = {"streaming": False, "framesize": "VGA", "pixformat": "RGB565",
                      "quality": 60, "fps": 15.0, "hmirror": False, "vflip": False}
        self.seq = 0
        self.buf = b""
        self.link = link_bytes_per_s  # simulate USB full-speed throughput
        self.t0 = time.monotonic()

    def send(self, ptype: int, payload: bytes) -> None:
        self.seq = (self.seq + 1) & 0xFFFF
        data = encode_packet(ptype, self.seq, payload)
        mv = memoryview(data)
        deadline = time.monotonic() + 0.5  # like usb.send(timeout=500) on the camera
        while mv:
            _, w, _ = select.select([], [self.fd], [], max(0.0, deadline - time.monotonic()))
            if not w:
                return  # host not reading: drop (a truncated packet is resynced by the host)
            try:
                n = os.write(self.fd, mv[:16384])
            except (BlockingIOError, OSError):
                return
            mv = mv[n:]
        time.sleep(len(data) / self.link)

    def send_json(self, ptype: int, obj: dict) -> None:
        self.send(ptype, json.dumps(obj).encode())

    def info(self) -> dict:
        return {"proto": 1, "script": "emulator", "firmware": "emulated", "board": "EMULATOR",
                "sensor": "0x5640", "api": "emu", **{k: self.state[k] for k in
                ("framesize", "pixformat", "quality")}, "target_fps": self.state["fps"]}

    def image(self, size) -> np.ndarray:
        w, h = size
        if self.cap is not None:
            ok, img = self.cap.read()
            if ok:
                return cv2.resize(img, (w, h))
        img = np.full((h, w, 3), 30, np.uint8)
        t = time.monotonic() - self.t0
        x = int(w / 2 + w / 3 * np.sin(t))
        cv2.circle(img, (x, h // 2), max(8, h // 10), (0, 180, 255), -1)
        cv2.putText(img, f"OPENMV EMULATOR {w}x{h} #{self.seq}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7 * w / 640, (255, 255, 255), 2)
        if self.state["pixformat"] == "GRAYSCALE":
            img = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        return img

    def jpeg(self, size, quality) -> bytes:
        ok, buf = cv2.imencode(".jpg", self.image(size), [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes()

    def handle(self, cmd: dict) -> None:
        name = cmd.get("cmd")
        reply = {"id": cmd.get("id"), "cmd": name, "ok": True}
        if name in ("hello", "ping"):
            reply["info"] = self.info()
        elif name == "set":
            for k in ("framesize", "pixformat", "quality", "fps", "hmirror", "vflip"):
                if k in cmd:
                    self.state[k] = cmd[k]
            if self.state["framesize"] not in SIZES:
                reply.update(ok=False, error="unknown framesize")
                self.state["framesize"] = "VGA"
            reply["info"] = self.info()
        elif name == "stream":
            self.state["streaming"] = bool(cmd.get("enable", True))
        elif name == "snapshot":
            fs = cmd.get("framesize", "QXGA")
            if fs not in SIZES:
                reply.update(ok=False, error="unknown framesize")
            else:
                self.send_json(PacketType.ACK, reply)
                self.send(PacketType.SNAPSHOT_JPEG, self.jpeg(SIZES[fs], int(cmd.get("quality", 85))))
                return
        elif name == "led":
            pass
        else:
            reply.update(ok=False, error=f"unknown cmd {name}")
        self.send_json(PacketType.ACK, reply)

    def poll(self) -> None:
        r, _, _ = select.select([self.fd], [], [], 0)
        if not r:
            return
        try:
            self.buf += os.read(self.fd, 4096)
        except OSError:
            return
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            if line.strip():
                try:
                    self.handle(json.loads(line))
                except json.JSONDecodeError:
                    self.send(PacketType.LOG, b"bad command")

    def run(self) -> None:
        next_frame = next_info = time.monotonic()
        while True:
            self.poll()
            now = time.monotonic()
            if now >= next_info:
                self.send_json(PacketType.INFO, self.info())
                next_info = now + 2
            if self.state["streaming"] and now >= next_frame:
                next_frame = now + 1 / max(float(self.state["fps"]), 0.5)
                self.send(PacketType.FRAME_JPEG,
                          self.jpeg(SIZES[self.state["framesize"]], int(self.state["quality"])))
            time.sleep(0.002)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--webcam", type=int, default=None, help="use this webcam as the image source")
    ap.add_argument("--link-kbps", type=float, default=900, help="simulated USB throughput, kB/s")
    args = ap.parse_args()
    master, slave = pty.openpty()
    tty.setraw(master)
    tty.setraw(slave)
    os.set_blocking(master, False)
    print(os.ttyname(slave), flush=True)
    try:
        Emulator(master, args.webcam, args.link_kbps * 1000).run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
