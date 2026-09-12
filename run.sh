#!/usr/bin/env bash
# Start the JSW C&F Operations app on this machine.
#
#   ./run.sh          -> http://127.0.0.1:8000
#   ./run.sh 8080     -> http://127.0.0.1:8080
#
# First run creates a virtualenv in ./venv and installs the pinned
# requirements; later runs reuse it and start in a couple of seconds.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8000}"
VENV="venv"

# --- find a usable Python (3.10+) -----------------------------------------
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PY="$candidate"
      break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer is required and wasn't found."
  echo "On a Mac, install it with:  brew install python@3.12"
  exit 1
fi

# --- virtualenv + dependencies --------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  echo "First run — creating the virtualenv (this takes a minute)..."
  "$PY" -m venv "$VENV"
fi
if [ ! -f "$VENV/.deps-installed" ] || [ requirements.txt -nt "$VENV/.deps-installed" ]; then
  echo "Installing dependencies..."
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r requirements.txt
  touch "$VENV/.deps-installed"
fi

# --- config ---------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from the example — set a real SECRET_KEY and ADMIN_PASSWORD in it."
fi
mkdir -p data

if grep -q "insecure-dev-key" .env 2>/dev/null; then
  echo "WARNING: SECRET_KEY in .env is still the placeholder. Sessions are not secure."
  echo "         Generate one with: $VENV/bin/python -c 'import secrets; print(secrets.token_hex(32))'"
fi

# --- go -------------------------------------------------------------------
URL="http://127.0.0.1:${PORT}"
echo
echo "Starting JSW C&F Ops on ${URL}"
echo "Press Ctrl+C to stop."
echo

# Open the browser once the server is actually accepting connections.
if command -v open >/dev/null 2>&1; then
  (
    for _ in $(seq 1 40); do
      if curl -s -o /dev/null "$URL/login"; then open "$URL"; break; fi
      sleep 0.5
    done
  ) &
fi

exec "$VENV/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
