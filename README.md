# Desktop CNC Camera
This section was written by Claude and has not been checked or rewritten yet. So take with a grain of salt

Live camera system for the Desktop CNC: an **OpenMV Cam H7 Plus** (or any USB webcam) streamed from the machine's computer to the Desktop CNC website and to any browser on the same network. Built so vision features (tool-break detection etc.) can be added later without re-architecting.

```
OpenMV H7 Plus / USB webcam ──USB──► camera bridge (Pi, :8090) ──HTTP/WebSocket──► browsers
```

- `openmv/main.py`: script that runs on the OpenMV Cam
- `bridge/`: Python service on the CNC computer (MJPEG streams, snapshots, status, events, viewer page)
- `deploy/`: Raspberry Pi installer + systemd service
- `docs/ARCHITECTURE.md`: design, camera limits, remote access, future-feature plan. **Read this first.**

Website integration lives in `Desktop-CNC-Landing-Site` (`/camera` page).

## Quick start (any computer, with a USB webcam)

```bash
cd bridge
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m cnc_camera --source webcam                    # or --source test (no hardware)
```

Open `http://localhost:8090/`. From another computer on the same network use the `network:` address printed at startup.

Useful flags: `--device 1` (second webcam), `--width 1280 --height 720`, `--fps 10`, `--token SECRET`, `--list-devices`, `-v`.

## On the CNC's Raspberry Pi (permanent install)

```bash
git clone https://github.com/Desktop-CNC/Desktop-CNC-Camera ~/Documents/GitHub/Desktop-CNC-Camera
bash ~/Documents/GitHub/Desktop-CNC-Camera/deploy/install_pi.sh
nano ~/Documents/GitHub/Desktop-CNC-Camera/bridge/config.toml   # pick cameras
sudo systemctl restart cnc-camera
journalctl -u cnc-camera -f
```

The service starts on boot, restarts on crash, and reconnects cameras when they are unplugged/replugged.

## When the OpenMV H7 Plus arrives

1. Install [OpenMV IDE](https://openmv.io/pages/download), connect the camera, update firmware if prompted.
2. Open `openmv/main.py`, run it once in the IDE to check it starts (the IDE frame buffer will be empty; that's expected), then **Tools → Save open script to OpenMV Cam (as main.py)**.
3. **Close the IDE** (it holds the serial port) and reset the camera.
4. On the Pi: `python -m cnc_camera --list-devices` should list it under `openmv`.
5. In `bridge/config.toml` set `enabled = true` for the `spindle` camera, restart the service.
6. Focus the lens by hand while watching the stream, then lock it.

LEDs: blue blink = waiting for the bridge, green blink = streaming, red = error (details in the bridge log).

No camera yet but want to test the OpenMV code path? (Linux/macOS)

```bash
python tools/openmv_emulator.py            # prints a port like /dev/pts/3
python -m cnc_camera --source openmv --port /dev/pts/3
```

## Tests

```bash
cd bridge && pip install pytest pytest-aiohttp && python -m pytest
```

## AI Disclosure
For parts of this project, Claude AI was used in assistance with debugging and development purposes.
A majority of the work completed by Claude was related to USB Camera support, deployment files, and the system overview.
A full list of AI assisted files will be listed here shortly.
