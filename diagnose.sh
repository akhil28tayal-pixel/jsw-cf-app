#!/usr/bin/env bash
# Why won't the app start? Run this from the jsw_cf_app folder:  bash diagnose.sh
cd "$(dirname "$0")"
echo "=============== 1. Is something already on port 8000? ==============="
if lsof -nP -iTCP:8000 -sTCP:LISTEN 2>/dev/null | grep -q LISTEN; then
  echo "YES — port 8000 is taken. That is almost certainly the problem."
  lsof -nP -iTCP:8000 -sTCP:LISTEN
  echo
  echo "   If it's a Docker process:   docker ps    then   docker stop <name>"
  echo "   If it's an old uvicorn:     kill \$(lsof -t -nP -iTCP:8000 -sTCP:LISTEN)"
  echo "   Or just use another port:   ./run.sh 8001"
else
  echo "No — port 8000 is free."
fi

echo
echo "=============== 2. Docker containers still running? ================"
if command -v docker >/dev/null 2>&1; then
  docker ps 2>/dev/null || echo "   (docker installed but not running — fine)"
else
  echo "   docker not installed — fine."
fi

echo
echo "=============== 3. The virtualenv ==================================="
if [ -x venv/bin/python ]; then
  echo "python: $(venv/bin/python -V 2>&1)"
  venv/bin/python -c "import fastapi,uvicorn,sqlalchemy,psycopg2,bcrypt,openpyxl; print('all imports OK')" \
    || echo ">>> a dependency is missing or broken — fix with:  venv/bin/pip install -r requirements.txt"
else
  echo ">>> venv/bin/python missing. Delete venv and re-run ./run.sh:  rm -rf venv && ./run.sh"
fi

echo
echo "=============== 4. The database file ================================"
ls -la data/*.db 2>/dev/null || echo ">>> no database file in data/"

echo
echo "=============== 5. Import the app and show the real error =========="
# This is the step that surfaces whatever ./run.sh is swallowing.
venv/bin/python -c "
import traceback
try:
    from app.main import app
    print('app imported cleanly — the problem is not in the code')
except Exception:
    print('>>> IMPORT FAILED:')
    traceback.print_exc()
"

echo
echo "=============== 6. Actually boot it, in the foreground =============="
echo "Running uvicorn directly on port 8001 for 8 seconds — watch for errors:"
( venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 & echo $! > /tmp/jsw_diag.pid ) 2>&1 &
sleep 8
echo "--- did it answer? ---"
curl -s -o /dev/null -w "HTTP %{http_code} from http://127.0.0.1:8001/login\n" http://127.0.0.1:8001/login || echo "no answer"
kill "$(cat /tmp/jsw_diag.pid 2>/dev/null)" 2>/dev/null
pkill -f "uvicorn app.main:app --host 127.0.0.1 --port 8001" 2>/dev/null
echo
echo "Done. Paste everything above back to Claude."
