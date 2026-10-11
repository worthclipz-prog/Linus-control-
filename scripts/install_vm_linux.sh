#!/usr/bin/env bash
# Install Pilot on a headless Linux VM (no monitor) -- e.g. an Oracle Cloud
# free-tier instance. Adds a virtual display (Xvfb) so a real browser can run.
# Run from the repo root:  bash scripts/install_vm_linux.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Installing system packages =="
if command -v apt-get >/dev/null; then
  sudo apt-get update -y
  sudo apt-get install -y python3-venv python3-dev python3-tk xvfb x11-utils fonts-liberation
elif command -v dnf >/dev/null; then
  sudo dnf install -y python3 python3-devel python3-tkinter xorg-x11-server-Xvfb xorg-x11-utils
else
  echo "Unknown package manager; install python3-venv, python3-dev and Xvfb yourself." >&2
fi

echo "== Python environment =="
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -e .

echo "== Browsers =="
# install-deps pulls the shared libraries Chromium/Firefox need on a server.
python -m playwright install-deps chromium firefox || true
python -m playwright install chromium firefox

[ -f config.toml ] || cp config.example.toml config.toml
[ -f .env ] || cp .env.example .env

cat <<'NOTE'

Installed.

Next steps:
  1. Put your Gemini (or other) API key(s) in .env:
       GEMINI_API_KEY=...
       # optional extra keys to fail over to when the first hits its limit:
       PILOT_API_KEY_2=...
       PILOT_API_KEY_3=...
  2. In config.toml set:
       [model]
       provider = "gemini"
       name     = "gemini-2.0-flash"
  3. Pick a strong server token:
       PILOT_SERVER_TOKEN=some-long-random-string      (in .env)
  4. Check it:   . .venv/bin/activate && pilot --doctor
  5. Start the phone control panel on a virtual screen:
       bash scripts/run_headless.sh

Reaching it from your phone:
  - Safest: keep the default bind (127.0.0.1) and make an SSH tunnel from your
    phone/laptop:  ssh -L 8080:127.0.0.1:8080 user@your-vm-ip
    then open http://127.0.0.1:8080/?token=... in the browser.
  - Or expose it: set PILOT_HOST=0.0.0.0, open the port in your cloud security
    list / firewall, and rely on the token. Anyone who reaches the port and has
    the token can control this machine, so use a long token and HTTPS if you can.
NOTE
