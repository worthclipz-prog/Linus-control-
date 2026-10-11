#!/usr/bin/env bash
# Install Pilot on Linux (Debian/Ubuntu/Fedora/Arch). Run from the repo root:
#   bash scripts/install_linux.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# Desktop-control helpers (screenshots + keyboard on X11). Skipped if no package manager matches.
if command -v apt-get >/dev/null; then
  sudo apt-get update -y
  sudo apt-get install -y python3-venv python3-tk python3-dev scrot xclip
elif command -v dnf >/dev/null; then
  sudo dnf install -y python3-tkinter python3-devel scrot xclip
elif command -v pacman >/dev/null; then
  sudo pacman -S --needed --noconfirm tk scrot xclip
fi

python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -e .
python -m playwright install --with-deps chromium firefox

[ -f config.toml ] || cp config.example.toml config.toml
[ -f .env ] || cp .env.example .env

echo
echo "Installed. Next:"
echo "  1. Put your OpenAI API key in .env (OPENAI_API_KEY=...)"
echo "  2. . .venv/bin/activate && pilot --doctor"
echo "  3. pilot --gui    or    pilot \"open youtube and search for lofi music\""
if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
  echo
  echo "Note: you are on Wayland. Mouse/keyboard control of native apps needs an X11 session"
  echo "(choose 'Ubuntu on Xorg' at the login screen). Browser control works either way."
fi
