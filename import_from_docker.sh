#!/usr/bin/env bash
# Move this app's data out of Docker and into the local (./run.sh) setup.
#
#   ./import_from_docker.sh                      # look only — shows what it found, changes nothing
#   ./import_from_docker.sh --import             # copy the Docker database into ./data/
#   ./import_from_docker.sh --import --retire    # ...and stop + remove the container for good
#
# The Docker deployment keeps its SQLite database at /app/data/jsw_cf.db inside
# the named volume `jsw_cf_data`. That volume is invisible to the local run, so
# the data has to be copied across once. --retire then stops the container and
# clears its restart policy so it can't come back on reboot and can't hold port
# 8000. The volume itself is never deleted — it stays as an untouched fallback.
set -euo pipefail
cd "$(dirname "$0")"

DO_IMPORT="no"; DO_RETIRE="no"
for arg in "$@"; do
  case "$arg" in
    --import) DO_IMPORT="yes" ;;
    --retire|--retire-docker) DO_RETIRE="yes" ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
hr()  { printf '%s\n' "------------------------------------------------------------"; }

count_rows() {  # count_rows <db file> -> prints "grn=N billing=N dispatch=N"
  local PY="python3"; [ -x venv/bin/python ] && PY="venv/bin/python"
  "$PY" - "$1" <<'PYEOF'
import sqlite3, sys
try:
    con = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
    out = []
    for t in ("grn", "billing", "dispatch"):
        try:
            out.append("%s=%d" % (t, con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]))
        except sqlite3.Error:
            out.append("%s=?" % t)
    print(" ".join(out))
except Exception:
    print("unreadable")
PYEOF
}

summarise() {  # summarise <db file>
  local PY="python3"; [ -x venv/bin/python ] && PY="venv/bin/python"
  "$PY" - "$1" <<'PYEOF'
import sqlite3, sys
con = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
for t in tables:
    print("  %-20s %7d rows" % (t, con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]))
cols = [r[1] for r in con.execute("PRAGMA table_info(grn)")] if "grn" in tables else []
if cols:
    print()
    if "godown_id" in cols:
        print("  Schema: multi-godown (already migrated)")
    else:
        print("  Schema: single-godown — it gets migrated on the next start, with")
        print("          every existing row tagged to Manesar Godown.")
try:
    print("  Logins that came across:", ", ".join(r[0] for r in con.execute("SELECT username FROM users")))
except sqlite3.Error:
    pass
con.close()
PYEOF
}

# --- 1. Docker present and running? ---------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  say "Docker isn't installed (or isn't on PATH). Start Docker Desktop and try again."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  say "Docker is installed but not running. Start Docker Desktop and try again."
  exit 1
fi

# --- 2. What's there? ------------------------------------------------------
hr; say "Containers on this machine:"; hr
docker ps -a --format '  {{.Names}}	{{.Image}}	{{.Status}}' || true
hr; say "Volumes on this machine:"; hr
docker volume ls --format '  {{.Name}}' || true
hr

CONTAINER=""
for c in $(docker ps -a --format '{{.ID}}'); do
  if docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' "$c" 2>/dev/null | grep -q '/app/data'; then
    CONTAINER="$c"; break
  fi
done
VOLUME="$(docker volume ls --format '{{.Name}}' | grep -i 'jsw_cf_data' | head -1 || true)"

[ -n "$CONTAINER" ] && say "Found the app container: $(docker inspect -f '{{.Name}} ({{.State.Status}})' "$CONTAINER" | sed 's|^/||')"
[ -n "$VOLUME" ]    && say "Found the data volume:   $VOLUME"
if [ -z "$CONTAINER" ] && [ -z "$VOLUME" ]; then
  say "No JSW C&F container or jsw_cf_data volume found — nothing to import."
  exit 1
fi
if docker ps -a --format '{{.Image}}' | grep -qi 'postgres'; then
  say "NOTE: a Postgres container is also present. If the app was switched to"
  say "      Postgres, the SQLite copy below is not your live data — say so first."
fi

if [ -f data/jsw_cf.db ]; then
  say "Local database as it stands now: $(count_rows data/jsw_cf.db)"
fi

if [ "$DO_IMPORT" != "yes" ]; then
  hr; say "Look-only mode: nothing has been changed."
  say "To bring the data across and retire Docker:"
  say "    ./import_from_docker.sh --import --retire"
  exit 0
fi

# --- 3. Copy the database out ---------------------------------------------
mkdir -p data
TMP_OUT="data/_incoming_jsw_cf.db"
rm -f "$TMP_OUT"

if [ -n "$CONTAINER" ]; then
  STATE="$(docker inspect -f '{{.State.Status}}' "$CONTAINER")"
  if [ "$STATE" = "running" ]; then
    say "App is running — taking a consistent snapshot inside the container first..."
    docker exec "$CONTAINER" python -c "import sqlite3; src=sqlite3.connect('/app/data/jsw_cf.db'); dst=sqlite3.connect('/app/data/_snapshot.db'); src.backup(dst); dst.close(); src.close()"
    docker cp "$CONTAINER:/app/data/_snapshot.db" "$TMP_OUT"
    docker exec "$CONTAINER" rm -f /app/data/_snapshot.db
  else
    say "App container is stopped — copying its database file directly..."
    docker cp "$CONTAINER:/app/data/jsw_cf.db" "$TMP_OUT"
  fi
else
  say "No container left — reading straight from the volume $VOLUME..."
  HELPER_IMAGE="$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(python|alpine|busybox)' | head -1 || true)"
  [ -z "$HELPER_IMAGE" ] && HELPER_IMAGE="alpine"
  docker run --rm -v "$VOLUME":/vol -v "$PWD/data":/out "$HELPER_IMAGE" \
    sh -c 'cp /vol/jsw_cf.db /out/_incoming_jsw_cf.db'
fi

if [ ! -s "$TMP_OUT" ]; then
  say "Couldn't get the database out — the copy is empty. Nothing was changed."
  exit 1
fi
say "Copied out of Docker: $(count_rows "$TMP_OUT")"

# --- 4. Back up whatever is here, then put the import in place -------------
STAMP="$(date +%Y%m%d-%H%M%S)"
if [ -f data/jsw_cf.db ]; then
  cp data/jsw_cf.db "data/backup-before-docker-import-$STAMP.db"
  say "Backed up the existing local database to data/backup-before-docker-import-$STAMP.db"
fi
cp "$TMP_OUT" "data/imported-from-docker-$STAMP.db"   # untouched copy of the import
mv "$TMP_OUT" data/jsw_cf.db
say "data/jsw_cf.db is now the database from Docker."

hr; say "What came across:"; hr
summarise data/jsw_cf.db
hr

# --- 5. Retire the container ----------------------------------------------
if [ "$DO_RETIRE" = "yes" ] && [ -n "$CONTAINER" ]; then
  NAME="$(docker inspect -f '{{.Name}}' "$CONTAINER" | sed 's|^/||')"
  say "Retiring the Docker container '$NAME'..."
  docker update --restart=no "$CONTAINER" >/dev/null 2>&1 || true   # don't come back on reboot
  docker stop "$CONTAINER" >/dev/null 2>&1 || true
  docker rm "$CONTAINER"   >/dev/null 2>&1 || true
  say "Stopped and removed. Port 8000 is free for ./run.sh now."
  say "The volume ${VOLUME:-jsw_cf_data} was NOT deleted — your data is still in there"
  say "as a fallback. Once you're happy with the local run, you can drop it with:"
  say "    docker volume rm ${VOLUME:-jsw_cf_data}"
elif [ "$DO_RETIRE" = "yes" ]; then
  say "No container to retire (only the volume was present)."
fi

hr
say "Next: ./run.sh   — log in with the username and password you used under Docker."
say "      (The users table came across with the data, so ADMIN_PASSWORD in .env is ignored.)"
