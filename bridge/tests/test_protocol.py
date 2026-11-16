import json

from cnc_camera.protocol import MAGIC, PacketType, StreamParser, encode_command, encode_packet
from cnc_camera.sources.openmv import jpeg_size

import cv2
import numpy as np


def _jpeg(w=64, h=48):
    ok, buf = cv2.imencode(".jpg", np.zeros((h, w, 3), np.uint8))
    return buf.tobytes()


def test_roundtrip_in_small_chunks():
    data = encode_packet(PacketType.FRAME_JPEG, 7, b"abc") + encode_packet(PacketType.INFO, 8, b'{"a":1}')
    p = StreamParser()
    out = []
    for i in range(len(data)):
        out += list(p.feed(data[i:i + 1]))
    assert [(x.type, x.seq) for x in out] == [(PacketType.FRAME_JPEG, 7), (PacketType.INFO, 8)]
    assert out[0].payload == b"abc" and out[1].json() == {"a": 1}


def test_resync_after_garbage_and_truncated_packet():
    good = encode_packet(PacketType.FRAME_JPEG, 2, b"payload")
    truncated = encode_packet(PacketType.FRAME_JPEG, 1, b"x" * 50)[:30]
    p = StreamParser()
    out = list(p.feed(b"\x00junk" + MAGIC[:2] + b"zz" + truncated))
    # the truncated packet claims 50 bytes, so the parser waits; the next packet's
    # bytes are swallowed into it. That costs one frame, then we are back in sync.
    out += list(p.feed(good * 4))
    assert out[-1].payload == b"payload"
    assert p.bytes_discarded > 0


def test_bad_version_skipped():
    bad = bytearray(encode_packet(PacketType.LOG, 1, b"hi"))
    bad[4] = 99
    p = StreamParser()
    out = list(p.feed(bytes(bad) + encode_packet(PacketType.LOG, 2, b"ok")))
    assert [x.payload for x in out] == [b"ok"] and p.bad_headers == 1


def test_command_is_one_json_line():
    raw = encode_command({"cmd": "set", "fps": 10})
    assert raw.endswith(b"\n") and json.loads(raw) == {"cmd": "set", "fps": 10}


def test_jpeg_size_reads_sof():
    assert jpeg_size(_jpeg(320, 240)) == (320, 240)
