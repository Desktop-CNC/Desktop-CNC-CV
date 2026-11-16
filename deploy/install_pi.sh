#!/usr/bin/env bash
# One-time setup on the Desktop CNC's Raspberry Pi
# Run from anywhere: bash deploy/install_pi.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

echo "==> Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y python3-venv python3-opencv libgl1 avahi-daemon

echo "==> Creating virtualenv (re-uses apt's OpenCV, which is faster to install on ARM)"
cd "$DIR/bridge"
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q aiohttp pyserial
[ -f config.toml ] || cp config.example.toml config.toml

echo "==> Device permissions"
sudo usermod -aG dialout,video "$USER_NAME"
# stop ModemManager from probing the OpenMV serial port (it delays/blocks the port)
echo 'ATTRS{idVendor}=="1209", ATTRS{idProduct}=="abd1", ENV{ID_MM_DEVICE_IGNORE}="1"
ATTRS{idVendor}=="37c5", ENV{ID_MM_DEVICE_IGNORE}="1"' | sudo tee /etc/udev/rules.d/49-openmv.rules >/dev/null
sudo udevadm control --reload-rules

echo "==> Installing systemd service"
sed -e "s#__USER__#$USER_NAME#g" -e "s#__DIR__#$DIR#g" "$DIR/deploy/cnc-camera.service" \
  | sudo tee /etc/systemd/system/cnc-camera.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now cnc-camera

echo
echo "Done. Edit $DIR/bridge/config.toml then: sudo systemctl restart cnc-camera"
echo "Logs:    journalctl -u cnc-camera -f"
echo "Viewer:  http://$(hostname).local:8090/   (from any computer on the same network)"
