#!/usr/bin/env bash
# Why the app wouldn't start: iCloud Drive evicted the project's files from
# local disk (Desktop & Documents sync + "Optimise Mac Storage"), leaving
# placeholders. Python hangs forever trying to import SQLAlchemy from them.
#
#   bash fix_icloud.sh          pull everything back down, then test the app
#   bash fix_icloud.sh --move   also move the project out of iCloud for good
set -uo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"

count_evicted() { find . -name '*.icloud' 2>/dev/null | wc -l | tr -d ' '; }

echo "===== 1. Pulling every file back onto the local disk ====="
echo "This can take a few minutes on a slow connection. Leave it running."
if command -v brctl >/dev/null 2>&1; then
  brctl download "$HERE" 2>/dev/null
fi
# brctl only nudges; reading each file is what actually forces materialisation.
find . -type f ! -name '.DS_Store' -print0 2>/dev/null \
  | xargs -0 -n 40 -P 4 cat >/dev/null 2>&1
echo "Placeholders still left: $(count_evicted)"

echo
echo "===== 2. Can Python import everything now? ====="
ok=1
for m in sqlalchemy fastapi uvicorn bcrypt openpyxl; do
  printf "  %-12s " "$m"
  if timeout 60 venv/bin/python -c "import $m" 2>/dev/null; then echo OK; else echo "STILL FAILING"; ok=0; fi
done

if [ "$ok" -ne 1 ]; then
  echo
  echo "Some imports still fail. The venv is the most likely casualty —"
  echo "rebuild it from scratch (safe, it holds no data of yours):"
  echo "    rm -rf venv && ./run.sh"
  exit 1
fi

echo
echo "===== 3. Booting the app for 10 seconds ====="
venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 >/tmp/jsw_boot.log 2>&1 &
BOOT=$!
sleep 10
code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/login 2>/dev/null)
kill "$BOOT" 2>/dev/null
if [ "$code" = "200" ]; then
  echo "The app is working again. Start it normally with ./run.sh"
else
  echo "Still not answering. The log says:"
  tail -20 /tmp/jsw_boot.log
  exit 1
fi

if [ "${1:-}" != "--move" ]; then
  echo
  echo "NOTE: iCloud will evict these files again. Re-run with --move to"
  echo "      relocate the project to ~/jsw_cf_app, outside iCloud."
  exit 0
fi

echo
echo "===== 4. Moving the project out of iCloud ====="
DEST="$HOME/jsw_cf_app"
if [ -e "$DEST" ]; then
  echo "$DEST already exists — move or rename it first. Nothing was changed."
  exit 1
fi
if [ "$(count_evicted)" != "0" ]; then
  echo "There are still iCloud placeholders — not moving, to avoid losing them."
  exit 1
fi
cp -a "$HERE" "$DEST" && echo "Copied to $DEST"
echo "Rebuilding the virtualenv at the new location (paths are baked into it)..."
rm -rf "$DEST/venv"
( cd "$DEST" && ./run.sh 8002 >/tmp/jsw_new.log 2>&1 & sleep 90; true )
sleep 1
pkill -f "port 8002" 2>/dev/null
echo
echo "Done. From now on use:   cd ~/jsw_cf_app && ./run.sh"
echo
echo "The Desktop copy is untouched — check the new one works, then delete"
echo "the old folder yourself. Two follow-ups:"
echo "  * In the Claude desktop app, add ~/jsw_cf_app as a folder (and drop the old one)."
echo "  * Your database now lives at ~/jsw_cf_app/data/jsw_cf.db — back that up."
