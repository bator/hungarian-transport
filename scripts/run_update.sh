#!/bin/bash
# HA shell_command kills the process group after 60s. Detach so a 100 MB GTFS
# rebuild can finish; the Python script notifies via webhook when it is done.
#
# The script reports its own failures (exit 1) and SIGTERM through the
# webhook. It cannot when it never ran (python3 missing, a syntax error in a
# half-updated install) or was killed outright (OOM, `timeout -k`), so the
# worker below reports those exit codes itself.
#
# `run_update.sh --worker` runs the job in the foreground (tests, debugging).
set -eu
base="${HUNGARIAN_TRANSPORT_BASE:-/config/hungarian-transport}"
webhook="${HUNGARIAN_TRANSPORT_WEBHOOK:-http://127.0.0.1:8123/api/webhook/hungarian-transport-gtfs}"
log="$base/update.log"

notify() {
  local body
  body=$(printf '{"title":"Hungarian transport GTFS","message":"%s"}' "$1")
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -m 10 -X POST -H 'Content-Type: application/json' --data "$body" "$webhook" >/dev/null 2>&1 && return 0
    if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
      curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
        --data "$body" "http://supervisor/core/api/webhook/hungarian-transport-gtfs" >/dev/null 2>&1 && return 0
    fi
  fi
  echo "notify failed: $1" >> "$log"
  return 0
}

worker() {
  local rc=0
  # Volán shapes.txt (8 M points) adds about a minute on a desktop and several
  # on a Raspberry Pi, hence the generous limit. SIGTERM lets the script report
  # itself; a minute later it is killed for good (137).
  PYTHONUNBUFFERED=1 timeout -k 60 1800 python3 "$base/update_volan_index.py" >> "$log" 2>&1 < /dev/null || rc=$?
  case "$rc" in
    0|1|124) ;;  # finished, or the script reported itself (124: SIGTERM delivered)
    137|143) notify "Volán GTFS frissítés megölve (exit $rc, OOM vagy időtúllépés): az indexek nem frissültek, lásd $log" ;;
    *) notify "Volán GTFS frissítés el sem indult vagy összeomlott (exit $rc), lásd $log" ;;
  esac
  return 0
}

mkdir -p "$base"
if [ "${1:-}" = "--worker" ]; then
  worker
  exit 0
fi
if [ -f "$log" ]; then
  sz=$(wc -c < "$log" || echo 0)
  if [ "${sz:-0}" -gt 1048576 ]; then
    mv "$log" "$log.1" || true
  fi
fi
setsid bash "$0" --worker > /dev/null 2>&1 < /dev/null &
echo "started $!"
