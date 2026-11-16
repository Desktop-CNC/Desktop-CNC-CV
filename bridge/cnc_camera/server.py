"HTTP/WebSocket server for the MJPEG streams, snapshots, status, events, viewing page, etc."
from __future__ import annotations
import asyncio
import hmac
import json
import logging
import socket
import time
from importlib import resources
from typing import Optional
from aiohttp import WSMsgType, web
from .analysis import AnalysisRunner, available as available_analyzers
from .analysis.base import _REGISTRY
from .config import AppConfig, CameraConfig
from .events import EventBus
from .frames import FrameHub
from .machine import MachineState
from .sources import CameraSource, create_source

log = logging.getLogger(__name__)
BOUNDARY = "cncframe"
API_VERSION = 1

class Camera:
    def __init__(self, cfg: CameraConfig, events: EventBus):
        self.cfg = cfg
        self.hub = FrameHub(cfg.id)
        self.source: CameraSource = create_source(cfg, self.hub, events)
        analyzers = []
        for name in cfg.analyzers:
            if name not in _REGISTRY:
                raise ValueError(f"camera {cfg.id}: unknown analyzer {name!r}, available analyzers: {available_analyzers()}")
            analyzers.append(_REGISTRY[name](**cfg.analyzer_options.get(name, {})))
        self.analyzers = analyzers
        self.runner: Optional[AnalysisRunner] = None

# middleware
def _validate_token(request: web.Request, token: str) -> bool:
    supplied = request.query.get("token", "")
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        supplied = auth[7:]
    return hmac.compare_digest(supplied.encode(), token.encode())


def _cors_headers(request: web.Request, headers) -> None:
    cfg: AppConfig = request.app["config"]
    origin = request.headers.get("Origin")
    allowed = cfg.server.cors_origins
    if not origin or not ("*" in allowed or origin in allowed):
        return
    headers["Access-Control-Allow-Origin"] = origin
    headers["Vary"] = "Origin"
    headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    if request.headers.get("Access-Control-Request-Private-Network"):
        headers["Access-Control-Allow-Private-Network"] = "true"


async def _on_response_prepare(request: web.Request, response: web.StreamResponse) -> None:
    _cors_headers(request, response.headers)


@web.middleware
async def preflight_middleware(request: web.Request, handler):
    if request.method == "OPTIONS" and request.headers.get("Access-Control-Request-Method"):
        return web.Response(status=204)
    return await handler(request)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    token = request.app["config"].server.token
    open_paths = ("/", "/viewer", "/api/health", "/favicon.ico")
    if token and request.method != "OPTIONS" and request.path not in open_paths:
        if not _validate_token(request, token):
            raise web.HTTPUnauthorized(text=json.dumps({"error": "bad token"}), content_type="application/json")
    return await handler(request)


# helper functions
def _camera(request: web.Request) -> Camera:
    cams: dict[str, Camera] = request.app["cameras"]
    cam_id = request.match_info.get("cam_id") or request.app["default_camera"]
    cam = cams.get(cam_id)
    if cam is None:
        raise web.HTTPNotFound(text=json.dumps({"error": f"no camera {cam_id!r}"}), content_type="application/json")
    return cam


def _float_param(request: web.Request, name: str) -> Optional[float]:
    raw = request.query.get(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        raise web.HTTPBadRequest(text=f"{name} must be a number")


# handlers
async def server_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "api": API_VERSION, "time": time.time(), "auth_required": bool(request.app["config"].server.token)})


async def list_cameras(request: web.Request) -> web.Response:
    cams: dict[str, Camera] = request.app["cameras"]
    machine: Optional[MachineState] = request.app["machine"]
    return web.json_response({
        "default": request.app["default_camera"],
        "cameras": [c.source.status() for c in cams.values()],
        "machine": {"connected": machine.connected, **machine.latest} if machine else None,
        "analyzers_available": available_analyzers(),
    })


async def camera_status(request: web.Request) -> web.Response:
    return web.json_response(_camera(request).source.status())


async def mjpeg_stream(request: web.Request) -> web.StreamResponse:
    cam = _camera(request)
    app = request.app
    if app["stats"]["stream_clients"] >= app["config"].server.max_stream_clients:
        raise web.HTTPServiceUnavailable(text="server overloaded")
    max_fps = _float_param(request, "fps")

    resp = web.StreamResponse(headers={
        "Content-Type": f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        "Cache-Control": "no-cache, no-store, must-revalidate, private",
        "Pragma": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "close",
    })
    await resp.prepare(request)
    app["stats"]["stream_clients"] += 1
    peer = request.remote
    log.info("stream open  cam=%s peer=%s (%d viewers)", cam.cfg.id, peer, app["stats"]["stream_clients"])
    try:
        async for frame in cam.hub.frames(max_fps=max_fps):
            part = (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame.jpeg)}\r\n"
                    f"X-Timestamp: {frame.timestamp:.3f}\r\nX-Seq: {frame.seq}\r\n\r\n").encode()
            await resp.write(part + frame.jpeg + b"\r\n")
    except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
        pass
    finally:
        app["stats"]["stream_clients"] -= 1
        log.info("stream close cam=%s peer=%s (%d viewers)", cam.cfg.id, peer, app["stats"]["stream_clients"])
    return resp


async def snapshot(request: web.Request) -> web.Response:
    cam = _camera(request)
    if request.query.get("hires") in ("1", "true"):
        try:
            frame = await cam.source.snapshot_hires(
                framesize=request.query.get("framesize", "QXGA"),
                quality=int(request.query.get("quality", 85)))
        except NotImplementedError as exc:
            raise web.HTTPNotImplemented(text=str(exc))
        except (asyncio.TimeoutError, ConnectionError, RuntimeError) as exc:
            raise web.HTTPServiceUnavailable(text=f"snapshot failed: {exc}")
    else:
        frame = await cam.hub.wait_next(timeout=5)
        if frame is None:
            raise web.HTTPServiceUnavailable(text="no frame available yet")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(frame.timestamp))
    headers = {"Cache-Control": "no-store", "X-Timestamp": f"{frame.timestamp:.3f}", "X-Placeholder": "1" if frame.placeholder else "0"}
    if request.query.get("download") in ("1", "true"):
        headers["Content-Disposition"] = f'attachment; filename="{cam.cfg.id}-{stamp}.jpg"'
    return web.Response(body=frame.jpeg, content_type="image/jpeg", headers=headers)


async def camera_command(request: web.Request) -> web.Response:
    cam = _camera(request)
    if not request.app["config"].server.token and request.remote not in ("127.0.0.1", "::1"):
        # without a token, only the machine can modify camera settings
        raise web.HTTPForbidden(text="set [server] token to allow remote camera control")
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise web.HTTPBadRequest(text="body must be JSON")
    try:
        result = await cam.source.command(body)
    except NotImplementedError as exc:
        raise web.HTTPNotImplemented(text=str(exc))
    except (ValueError, asyncio.TimeoutError, ConnectionError) as exc:
        raise web.HTTPBadRequest(text=str(exc) or exc.__class__.__name__)
    return web.json_response(result)


async def events_ws(request: web.Request) -> web.WebSocketResponse:
    events: EventBus = request.app["events"]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    q = events.subscribe()
    await ws.send_json({"type": "hello", "api": API_VERSION, "recent": events.recent(20)})

    async def pump():
        while True:
            await ws.send_json(await q.get())

    pump_task = asyncio.create_task(pump())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
    finally:
        pump_task.cancel()
        events.unsubscribe(q)
    return ws


async def recent_events(request: web.Request) -> web.Response:
    limit = int(request.query.get("limit", 50))
    return web.json_response(request.app["events"].recent(limit))


async def viewer(request: web.Request) -> web.Response:
    html = resources.files("cnc_camera.static").joinpath("viewer.html").read_text("utf-8")
    return web.Response(text=html, content_type="text/html")


# application
def build_app(cfg: AppConfig) -> web.Application:
    app = web.Application(middlewares=[preflight_middleware, auth_middleware])
    app.on_response_prepare.append(_on_response_prepare)
    events = EventBus()
    app["config"] = cfg
    app["events"] = events
    app["stats"] = {"stream_clients": 0}
    app["cameras"] = {c.id: Camera(c, events) for c in cfg.cameras if c.enabled}
    if not app["cameras"]:
        raise ValueError("no cameras configured")
    app["default_camera"] = next(iter(app["cameras"]))
    app["machine"] = (MachineState(cfg.machine.ugs_pendant_url, events, cfg.machine.poll_interval_s) if cfg.machine.ugs_pendant_url else None)

    r = app.router
    r.add_get("/", viewer)
    r.add_get("/viewer", viewer)
    r.add_get("/api/health", server_health)
    r.add_get("/api/cameras", list_cameras)
    r.add_get("/api/cameras/{cam_id}", camera_status)
    r.add_get("/api/cameras/{cam_id}/stream.mjpg", mjpeg_stream)
    r.add_get("/api/cameras/{cam_id}/snapshot.jpg", snapshot)
    r.add_post("/api/cameras/{cam_id}/command", camera_command)
    r.add_get("/api/events", events_ws)
    r.add_get("/api/events/recent", recent_events)
    r.add_get("/stream.mjpg", mjpeg_stream)
    r.add_get("/snapshot.jpg", snapshot)

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)
    return app


async def _on_startup(app: web.Application) -> None:
    loop = asyncio.get_running_loop()
    app["events"].bind_loop(loop)
    machine: Optional[MachineState] = app["machine"]
    if machine:
        machine.start()
    for cam in app["cameras"].values():
        cam.hub.bind_loop(loop)
        cam.source.start()
        cam.runner = AnalysisRunner(cam.cfg.id, cam.hub, app["events"], cam.analyzers, machine)
        cam.runner.start()
    _print_banner(app)


async def _on_shutdown(app: web.Application) -> None:
    for cam in app["cameras"].values():
        if cam.runner:
            await cam.runner.stop()
        await asyncio.get_running_loop().run_in_executor(None, cam.source.stop)
    if app["machine"]:
        await app["machine"].stop()


def lan_addresses() -> list[str]:
    addrs = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


def _print_banner(app: web.Application) -> None:
    cfg: AppConfig = app["config"]
    port = cfg.server.port
    lines = [f"  local:   http://localhost:{port}/"]
    if cfg.server.host in ("0.0.0.0", "::", ""):
        lines += [f"  network: http://{a}:{port}/" for a in lan_addresses()]
        lines.append(f"  mDNS:    http://{socket.gethostname()}.local:{port}/")
    cams = ", ".join(f"{c.cfg.id} ({c.cfg.type})" for c in app["cameras"].values())
    log.info("Desktop CNC camera bridge running. Cameras: %s\n%s", cams, "\n".join(lines))
    if cfg.server.token:
        log.info("Access token required: add ?token=<token> to the URLs above")
