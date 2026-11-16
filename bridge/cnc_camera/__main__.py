"""
Command processor
Example commands:
    python -m cnc_camera --source webcam  # USB webcam
    python -m cnc_camera --source webcam --device 1 --width 1280 --height 720
    python -m cnc_camera --source openmv  # OpenMV Cam
    python -m cnc_camera --source test  # no camera
    python -m cnc_camera --config config.toml # full configuration
    python -m cnc_camera --list-devices
"""
from __future__ import annotations
import argparse
import json
import logging
import sys
from pathlib import Path
from aiohttp import web
from .config import CameraConfig, load_config

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cnc_camera", description="Desktop CNC camera bridge for website integration")
    ap.add_argument("--config", type=Path, help="Config File")
    ap.add_argument("--source", choices=["webcam", "openmv", "test"], help="quick start options")
    ap.add_argument("--device", default=None, help="webcam index, /dev/videoN or URL")
    ap.add_argument("--port", dest="serial_port", default=None, help="OpenMV serial port")
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--fps", type=float)
    ap.add_argument("--quality", type=int, help="JPEG quality 10-95")
    ap.add_argument("--framesize", help="OpenMV framesize, IE: QVGA, VGA, HD")
    ap.add_argument("--host", help="host address (default; 0.0.0.0 = LAN)")
    ap.add_argument("--http-port", type=int, help="HTTP port (default 8090)")
    ap.add_argument("--token", help="require an access token")
    ap.add_argument("--list-devices", action="store_true", help="list all connected cameras")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    if args.list_devices:
        from .sources.openmv import find_openmv_ports
        from .sources.webcam import list_webcams
        print(json.dumps({"openmv": find_openmv_ports(), "webcams": list_webcams()}, indent=2, default=str))
        return 0

    cfg = load_config(args.config)
    if args.source:
        cam = CameraConfig(id="main", name={"webcam": "USB webcam", "openmv": "OpenMV H7 Plus", "test": "Test pattern"}[args.source], type=args.source)
        cfg.cameras = [cam] + [c for c in cfg.cameras if c.id != "main"]
    if not cfg.cameras:
        cfg.cameras = [CameraConfig(id="main", name="USB webcam", type="webcam")]

    first = cfg.cameras[0]
    for attr, val in (("device", args.device), ("port", args.serial_port), ("width", args.width),
                      ("height", args.height), ("fps", args.fps), ("jpeg_quality", args.quality),
                      ("framesize", args.framesize)):
        if val is not None:
            setattr(first, attr, val)
    if args.host:
        cfg.server.host = args.host
    if args.http_port:
        cfg.server.port = args.http_port
    if args.token is not None:
        cfg.server.token = args.token

    from .server import build_app
    try:
        app = build_app(cfg)
    except ValueError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    web.run_app(app, host=cfg.server.host, port=cfg.server.port, print=None, access_log=None)
    return 0

if __name__ == "__main__":
    sys.exit(main())
