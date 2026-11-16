"""Configuration: TOML file + command-line overrides."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union


@dataclass
class CameraConfig:
    id: str = "main"
    name: str = "Camera"
    type: str = "webcam"  # can be either: webcam | openmv | test
    enabled: bool = True

    # image stream settings
    width: int = 640
    height: int = 480
    fps: float = 15.0
    jpeg_quality: int = 70

    # webcams
    device: Union[int, str] = 0  # index, /dev/videoN, or a URL OpenCV can open
    backend: str = "auto"  # auto | v4l2 | dshow | msmf | avfoundation
    rotate: int = 0  # 0, 90, 180, 270 (webcam/test only; OpenMV rotates on-camera)

    # openmv
    port: str = "auto"  # auto, /dev/ttyACM0, COM5, ...
    framesize: str = "VGA"  # QVGA, VGA, SVGA, XGA, HD, (see openmv/main.py)
    pixformat: str = "RGB565"  # RGB565 | GRAYSCALE
    hmirror: bool = False
    vflip: bool = False
    frame_timeout_s: float = 5.0  # try and reconnect if no frame for 5 seconds, assuming crash

    analyzers: list[str] = field(default_factory=list)
    analyzer_options: dict[str, dict] = field(default_factory=dict)


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8090  # 8080 is used by the UGS website
    token: str = ""  # secret key, empty for lan connections
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    max_stream_clients: int = 8


@dataclass
class MachineConfig:
    """Optional link to the UGS web pendant for machine-aware features."""
    ugs_pendant_url: str = ""  # machine ip for analyzers, IE: http://127.0.0.1:8080, keep empty for nothing
    poll_interval_s: float = 1.0


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    machine: MachineConfig = field(default_factory=MachineConfig)
    cameras: list[CameraConfig] = field(default_factory=list)


def _build(cls, data: dict[str, Any]):
    known = {f for f in cls.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} keys: {', '.join(sorted(unknown))}")
    return cls(**data)


def load_config(path: Optional[Path]) -> AppConfig:
    if path is None:
        return AppConfig()
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    cfg = AppConfig(
        server=_build(ServerConfig, raw.get("server", {})),
        machine=_build(MachineConfig, raw.get("machine", {})),
        cameras=[_build(CameraConfig, c) for c in raw.get("cameras", [])],
    )
    ids = [c.id for c in cfg.cameras]
    if len(ids) != len(set(ids)):
        raise ValueError("Camera id must be unique")
    return cfg
