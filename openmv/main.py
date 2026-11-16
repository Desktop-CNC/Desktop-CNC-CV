# Desktop CNC - OpenMV Cam H7 Plus streaming script
#
# LED meanings:
# blue + blinking = waiting for host
# green + blinking = streaming
# red = error (Check the bridge log)

import json
import struct
import time
import pyb

try:
    import csi
    _HAVE_CSI = True
except ImportError:
    import sensor
    _HAVE_CSI = False

PROTOCOL_VERSION = 1
MAGIC = b"CNCV"
T_FRAME, T_SNAPSHOT, T_EVENT, T_INFO, T_LOG, T_ACK = 0x01, 0x02, 0x10, 0x11, 0x12, 0x13
FW_SCRIPT_VERSION = "0.1.0"

usb = pyb.USB_VCP()
usb.setinterrupt(-1)  # disble ctrl-c stopping the camera

red_led, green_led, blue_led, infared_LED = pyb.LED(1), pyb.LED(2), pyb.LED(3), pyb.LED(4)

# camera wrapper
_MOD = csi if _HAVE_CSI else sensor
FRAMESIZES = {}
for _n in ("QQVGA", "QVGA", "VGA", "SVGA", "XGA", "HD", "SXGA", "UXGA", "FHD", "QXGA", "WQXGA2"):
    if hasattr(_MOD, _n):
        FRAMESIZES[_n] = getattr(_MOD, _n)
PIXFORMATS = {"RGB565": _MOD.RGB565, "GRAYSCALE": _MOD.GRAYSCALE}
if hasattr(_MOD, "JPEG"):
    PIXFORMATS["JPEG"] = _MOD.JPEG


class Cam:
    def __init__(self):
        if _HAVE_CSI:
            self.c = csi.CSI()
            self.c.reset()
        else:
            self.c = None
            sensor.reset()

    def pixformat(self, pf):
        self.c.pixformat(pf) if _HAVE_CSI else sensor.set_pixformat(pf)

    def framesize(self, fs):
        self.c.framesize(fs) if _HAVE_CSI else sensor.set_framesize(fs)

    def mirror(self, h, v):
        if _HAVE_CSI:
            self.c.hmirror(h)
            self.c.vflip(v)
        else:
            sensor.set_hmirror(h)
            sensor.set_vflip(v)

    def settle(self, ms):
        if _HAVE_CSI:
            self.c.snapshot(time=ms)
        else:
            sensor.skip_frames(time=ms)

    def snapshot(self):
        return self.c.snapshot() if _HAVE_CSI else sensor.snapshot()

    def sensor_id(self):
        try:
            return hex(self.c.cid() if _HAVE_CSI else sensor.get_id())
        except Exception:
            return "unknown"


def to_jpeg(img, quality):
    """Return a JPEG Image without touching the original (needed by analyzers)."""
    if hasattr(img, "to_jpeg"):
        return img.to_jpeg(quality=quality, copy=True)
    return img.compressed(quality=quality)


# PACKET HANDLING #
_seq = 0


def send_packet(ptype, payload, timeout_ms=500):
    # this will return false if we aren't receiving the packets fast enough
    global _seq
    if not usb.isconnected():
        return False
    _seq = (_seq + 1) & 0xFFFF
    mv = memoryview(payload)
    header = struct.pack("<4sBBHI", MAGIC, PROTOCOL_VERSION, ptype, _seq, len(mv))
    try:
        if usb.send(header, timeout=timeout_ms) != len(header):
            return False
        sent = 0
        while sent < len(mv):
            n = usb.send(mv[sent:], timeout=timeout_ms)
            if not n:
                return False
            sent += n
        return True
    except OSError:
        return False


def send_json(ptype, obj):
    return send_packet(ptype, json.dumps(obj).encode())


def log(msg):
    send_packet(T_LOG, str(msg).encode())


# CAMERA SETTINGS #
# camera state information
state = {
    "streaming": False,
    "framesize": "VGA",
    "pixformat": "RGB565",
    "quality": 60,
    "fps": 15.0,
    "hmirror": False,
    "vflip": False,
}
cam = Cam()


def apply_settings():
    cam.pixformat(PIXFORMATS[state["pixformat"]])
    cam.framesize(FRAMESIZES[state["framesize"]])
    cam.mirror(state["hmirror"], state["vflip"])
    cam.settle(300)


def info():
    return {
        "proto": PROTOCOL_VERSION,
        "script": FW_SCRIPT_VERSION,
        "firmware": _fw(),
        "board": _board(),
        "sensor": cam.sensor_id(),
        "api": "csi" if _HAVE_CSI else "sensor",
        "framesize": state["framesize"],
        "pixformat": state["pixformat"],
        "quality": state["quality"],
        "targetfps": state["fps"],
        "measuredfps": round(fps, 1),
        "framesizes": sorted(FRAMESIZES),
        "free_mem": _free(),
    }


def _fw():
    try:
        import omv
        return omv.version_string()
    except Exception:
        return "unknown"


def _board():
    try:
        import omv
        return omv.board_type()
    except Exception:
        return "OpenMV"


def _free():
    try:
        import gc
        return gc.mem_free()
    except Exception:
        return -1


# COMPUTER VISION CUSTOM FUNCTIONS #
# Future computer vision functions go in here. Everything in here will take in
# the uncompressed image, run in through whatever function you make
# and return a dict event or None.
# Events reach every viewer as {"type":"analysis","origin":"camera", ...}
# Try and keep them cheap as the power onboard is limited
#
# Example:
# def tool_present(img):
#     roi = (300, 100, 40, 200) # where the tool appears
#     s = img.get_statistics(roi=roi)
#     return {"kind": "tool_present", "value": s.mean() > 80}
ANALYZERS = []


def handle(cmd):
    name = cmd.get("cmd")
    reply = {"id": cmd.get("id"), "cmd": name, "ok": True}
    try:
        if name == "hello" or name == "ping":
            reply["info"] = info()
        elif name == "set":
            changed = False
            for k in ("framesize", "pixformat"):
                if k in cmd and cmd[k] != state[k]:
                    if k == "framesize" and cmd[k] not in FRAMESIZES:
                        raise ValueError("unknown framesize " + str(cmd[k]))
                    if k == "pixformat" and cmd[k] not in ("RGB565", "GRAYSCALE"):
                        raise ValueError("pixformat must be RGB565 or GRAYSCALE")
                    state[k] = cmd[k]
                    changed = True
            for k in ("hmirror", "vflip"):
                if k in cmd and bool(cmd[k]) != state[k]:
                    state[k] = bool(cmd[k])
                    changed = True
            if "quality" in cmd:
                state["quality"] = max(10, min(95, int(cmd["quality"])))
            if "fps" in cmd:
                state["fps"] = max(0.5, float(cmd["fps"]))
            if changed:
                apply_settings()
            reply["info"] = info()
        elif name == "stream":
            state["streaming"] = bool(cmd.get("enable", True))
        elif name == "snapshot":
            reply["ok"] = hires_snapshot(cmd.get("framesize", "QXGA"), int(cmd.get("quality", 85)))
            if not reply["ok"]:
                reply["error"] = "snapshot failed (framesize too large for memory?)"
        elif name == "led":
            infared_LED.on() if cmd.get("ir") else infared_LED.off()
        else:
            raise ValueError("unknown cmd " + str(name))
    except Exception as e:
        reply["ok"] = False
        reply["error"] = str(e)
    send_json(T_ACK, reply)


def hires_snapshot(fs_name, quality):
    if fs_name not in FRAMESIZES:
        return False
    ok = False
    try:
        if "JPEG" in PIXFORMATS:
            cam.pixformat(PIXFORMATS["JPEG"])
            cam.framesize(FRAMESIZES[fs_name])
            cam.settle(500)
            img = cam.snapshot()
        else:
            cam.framesize(FRAMESIZES[fs_name])
            cam.settle(500)
            img = to_jpeg(cam.snapshot(), quality)
        ok = send_packet(T_SNAPSHOT, img.bytearray(), timeout_ms=3000)
    except Exception as e:
        log("snapshot error: " + str(e))
    finally:
        apply_settings()  # back to stream settings which are slightly worse for speed
    return ok


_line = b""


def poll_commands():
    global _line
    while usb.any():
        chunk = usb.read(usb.any())
        if not chunk:
            break
        _line = _line + chunk
        while True:
            i = _line.find(b"\n")
            if i < 0:
                break
            raw, _line = _line[:i], _line[i + 1:]
            if raw.strip():
                try:
                    handle(json.loads(raw))
                except ValueError:
                    log("bad command: " + str(raw[:60]))
        if len(_line) > 4096:  # garbage; reset
            _line = b""


# MAIN LOOP #
fps = 0.0  # how many frames were viewing at


def main():
    global fps
    apply_settings()
    clock = time.clock()
    next_frame = time.ticks_ms()
    next_info = time.ticks_ms()
    was_connected = False
    blink = 0

    while True:
        poll_commands()
        connected = usb.isconnected()
        if connected and not was_connected:
            send_json(T_INFO, info())
        if not connected:
            state["streaming"] = False  # reenable after reconnect
            was_connected = False
            blink += 1
            green_led.off()
            blue_led.on() if (blink // 10) % 2 else blue_led.off()
            time.sleep_ms(50)
            continue
        was_connected = True
        blue_led.off()

        now = time.ticks_ms()
        if time.ticks_diff(now, next_info) >= 0:
            send_json(T_INFO, info())
            next_info = time.ticks_add(now, 2000)

        if not state["streaming"]:
            time.sleep_ms(10)
            continue
        if time.ticks_diff(now, next_frame) < 0:
            time.sleep_ms(1)
            continue
        next_frame = time.ticks_add(now, int(1000 / state["fps"]))

        clock.tick()
        img = cam.snapshot()
        for fn in ANALYZERS:
            try:
                ev = fn(img)
                if ev:
                    send_json(T_EVENT, ev)
            except Exception as e:
                log("analyzer error: " + str(e))
        jpg = to_jpeg(img, state["quality"])
        if send_packet(T_FRAME, jpg.bytearray()):
            blink += 1
            green_led.on() if (blink // 5) % 2 else green_led.off()
        fps = clock.fps()


while True:
    try:
        main()
    except Exception as e:
        green_led.off()
        red_led.on()
        try:
            log("fatal: " + str(e))
        except Exception:
            pass
        time.sleep_ms(1000)
        red_led.off()
        try:
            cam = Cam()
        except Exception:
            pass
