#!/usr/bin/env bash
# Import each dependency in its own process and report exactly how it exits.
cd "$(dirname "$0")"
PY=venv/bin/python
echo "python: $($PY -V 2>&1)"
echo "arch:   $(uname -m)   |   python arch: $(file -b $($PY -c 'import sys;print(sys.executable)') 2>/dev/null | head -1)"
echo
for m in fastapi starlette uvicorn sqlalchemy pydantic bcrypt openpyxl jinja2 itsdangerous dotenv multipart psycopg2; do
  printf "%-14s " "$m"
  out=$("$PY" -c "import $m" 2>&1)
  rc=$?
  if [ $rc -eq 0 ]; then
    echo "OK"
  elif [ $rc -gt 128 ]; then
    sig=$((rc - 128))
    name=$(kill -l $sig 2>/dev/null)
    echo "*** CRASHED — killed by signal $sig ($name) ***"
    [ -n "$out" ] && echo "               $out" | head -3
  else
    echo "FAILED (exit $rc)"
    echo "$out" | tail -3 | sed 's/^/               /'
  fi
done
echo
echo "=== now the app itself ==="
out=$("$PY" -c "from app.main import app; print('app imported OK')" 2>&1); rc=$?
echo "$out" | tail -25
[ $rc -gt 128 ] && echo "*** app import CRASHED — signal $((rc-128)) ***"
echo "(exit $rc)"
