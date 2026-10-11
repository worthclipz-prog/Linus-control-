#!/usr/bin/env bash
# Start Pilot's control panel on a headless Linux VM, inside a virtual display
# so a real (non-headless) browser can run. Env vars you can set:
#   PILOT_HOST (default 127.0.0.1), PILOT_PORT (default 8080), DISPLAY_NUM (default 99)
set -euo pipefail
cd "$(dirname "$0")/.."
. .venv/bin/activate

DISPLAY_NUM="${DISPLAY_NUM:-99}"
export DISPLAY=":${DISPLAY_NUM}"

if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
  echo "Starting virtual display on $DISPLAY ..."
  Xvfb "$DISPLAY" -screen 0 1280x800x24 >/tmp/pilot-xvfb.log 2>&1 &
  sleep 1
fi

exec pilot --serve --host "${PILOT_HOST:-127.0.0.1}" --port "${PILOT_PORT:-8080}" "$@"
