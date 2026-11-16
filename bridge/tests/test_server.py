import asyncio

import pytest

from cnc_camera.config import AppConfig, CameraConfig, ServerConfig
from cnc_camera.frames import Frame, FrameHub
from cnc_camera.server import build_app


def _cfg(**server):
    return AppConfig(server=ServerConfig(**server),
                     cameras=[CameraConfig(id="t", name="Test", type="test", fps=20,
                                           analyzers=["camera_health"])])


async def _wait_streaming(client, cam="t"):
    for _ in range(50):
        st = await (await client.get(f"/api/cameras/{cam}")).json()
        if st["state"] == "streaming" and st["width"]:
            return st
        await asyncio.sleep(0.1)
    raise AssertionError("camera never started")


async def test_status_snapshot_and_stream(aiohttp_client):
    client = await aiohttp_client(build_app(_cfg()))
    st = await _wait_streaming(client)
    assert (st["width"], st["height"]) == (640, 480)

    r = await client.get("/api/cameras/t/snapshot.jpg")
    assert r.status == 200 and r.content_type == "image/jpeg"
    assert (await r.read())[:2] == b"\xff\xd8"

    r = await client.get("/api/cameras/t/stream.mjpg")
    assert r.headers["Content-Type"].startswith("multipart/x-mixed-replace")
    chunk = await r.content.read(4096)
    assert b"--cncframe" in chunk and b"image/jpeg" in chunk
    r.close()

    assert (await client.get("/api/cameras/nope")).status == 404
    assert (await client.get("/")).status == 200


async def test_events_websocket_gets_status(aiohttp_client):
    client = await aiohttp_client(build_app(_cfg()))
    ws = await client.ws_connect("/api/events")
    hello = await ws.receive_json(timeout=2)
    assert hello["type"] == "hello"
    kinds = {e["type"] for e in hello["recent"]}
    assert "camera.status" in kinds
    await ws.close()


async def test_token_required(aiohttp_client):
    client = await aiohttp_client(build_app(_cfg(token="s3cret")))
    assert (await client.get("/api/health")).status == 200        # health is open
    assert (await client.get("/api/cameras")).status == 401
    assert (await client.get("/api/cameras?token=s3cret")).status == 200
    r = await client.get("/api/cameras", headers={"Authorization": "Bearer s3cret"})
    assert r.status == 200


async def test_cors_preflight_private_network(aiohttp_client):
    client = await aiohttp_client(build_app(_cfg()))
    r = await client.options("/api/cameras", headers={
        "Origin": "https://example.org", "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Private-Network": "true"})
    assert r.status == 204
    assert r.headers["Access-Control-Allow-Origin"] == "https://example.org"
    assert r.headers["Access-Control-Allow-Private-Network"] == "true"


async def test_hub_slow_consumer_skips_frames():
    hub = FrameHub("x", asyncio.get_running_loop())
    got = []

    async def consume():
        async for f in hub.frames(max_fps=2):
            got.append(f.seq)
            if len(got) == 2:
                return

    task = asyncio.create_task(consume())
    for i in range(1, 40):
        hub.publish(Frame(b"", 1, 1, i, 0.0, "x"))
        await asyncio.sleep(0.02)
    await asyncio.wait_for(task, 2)
    assert got[1] - got[0] > 1  # skipped intermediate frames, no backlog
