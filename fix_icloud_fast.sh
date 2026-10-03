#!/usr/bin/env bash
# Fix for: the app hangs on startup because iCloud Drive evicted its files.
#
# Rather than wait for iCloud to hand back thousands of library files, this
# rebuilds the virtualenv from PyPI at ~/.venvs/jsw_cf — outside iCloud, so it
# can never be evicted again — and only pulls back your own source files,
# which are few and small.
#
#   bash fix_icloud_fast.sh
set -uo pipefail
cd "$(dirname "$0")"

echo "===== 1. Pulling your own source files back to local disk ====="
# Just the code and data — not venv. A few hundred KB, seconds not minutes.
for d in app tests scripts data deploy; do [ -d "$d" ] && find "$d" -type f -print0; done \
  | xargs -0 -n 40 -P 4 cat >/dev/null 2>&1
find . -maxdepth 1 -type f -print0 | xargs -0 -n 20 cat >/dev/null 2>&1
left=$(find app tests scripts data -name '*.icloud' 2>/dev/null | wc -l | tr -d ' ')
echo "iCloud placeholders left in your source: $left"
if [ "$left" != "0" ]; then
  echo ">>> Some source files still haven't come back. Check that iCloud is"
  echo "    online (System Settings > Apple Account > iCloud) and re-run this."
  exit 1
fi

echo
echo "===== 2. Rebuilding the virtualenv outside iCloud ====="
VENV="$HOME/.venvs/jsw_cf"
mkdir -p "$HOME/.venvs"
rm -rf "$VENV"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null && { PY="$c"; break; }
done
[ -z "$PY" ] && { echo "No Python 3.10+ found."; exit 1; }
echo "Using $PY ($("$PY" -V 2>&1)) -> $VENV"
"$PY" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install -r requirements.txt || { echo ">>> pip install failed — check your internet connection."; exit 1; }
touch "$VENV/.deps-installed"

echo
echo "===== 3. Import check (with a timeout, so a stall reports) ====="
for m in sqlalchemy fastapi uvicorn bcrypt openpyxl psycopg2 jinja2 itsdangerous; do
  printf "  %-14s " "$m"
  if timeout 45 "$VENV/bin/python" -c "import $m" 2>/dev/null; then echo OK; else echo "FAILED"; fi
done

echo
echo "===== 4. Removing the old venv from inside iCloud ====="
# It holds nothing of yours, and leaving it there means iCloud keeps syncing
# ~80MB of library files for no reason.
rm -rf ./venv && echo "  old ./venv deleted"

echo
echo "===== 5. Booting the app ====="
"$VENV/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8001 >/tmp/jsw_boot.log 2>&1 &
BOOT=$!
sleep 12
code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/login 2>/dev/null)
kill "$BOOT" 2>/dev/null
echo
if [ "$code" = "200" ]; then
  echo "WORKING. Start it as always:   ./run.sh"
  echo "run.sh now uses $VENV automatically."
else
  echo "Still not answering (got '$code'). The log says:"
  tail -25 /tmp/jsw_boot.log
  exit 1
fi
