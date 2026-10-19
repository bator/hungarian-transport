#!/bin/bash
# HA shell_command kills the process group after 60s. Detach so a 100 MB GTFS
# rebuild can finish; the Python script notifies via webhook when it is done.
#
# The script reports its own failures and SIGTERM through the webhook and
# then exits 3. Every other non-zero exit is reported by the worker below:
# python3 missing, a syntax error in update_volan_index.py itself (exit 1),
# a crash the script did not catch, or a kill outright (OOM, `timeout -k`).
# A broken sibling script (build_*.py, gtfs_compact.py) is reported by the
# script itself.
#
# `run_update.sh --worker` runs the job in the foreground (tests, debugging).
# pipefail: a failed stage of a pipeline (the escape fallback, the token
# header pipe) fails the pipeline instead of being hidden by its last stage.
set -euo pipefail
base="${HUNGARIAN_TRANSPORT_BASE:-/config/hungarian-transport}"
default_webhook="http://127.0.0.1:8123/api/webhook/hungarian-transport-gtfs"
webhook="${HUNGARIAN_TRANSPORT_WEBHOOK:-}"
log="$base/update.log"
# update_volan_index.REPORTED: the script already sent this failure.
reported=3

json_body() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -I -c 'import json, sys; print(json.dumps({"title": "Hungarian transport GTFS", "message": sys.argv[1]}))' "$1" \
      && return 0
  fi
  # No usable python3 (the very failure being reported): escape by hand.
  local msg
  msg=$(printf '%s' "$1" | tr -d '\000-\037' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
  printf '{"title":"Hungarian transport GTFS","message":"%s"}\n' "$msg"
}

notify() {
  local body
  body=$(json_body "$1")
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -m 10 -X POST -H 'Content-Type: application/json' --data "$body" "$webhook" >/dev/null 2>&1 && return 0
    if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
      # The header comes from stdin (printf is a builtin), so the token is
      # not on curl's command line, where any process could read it.
      printf 'Authorization: Bearer %s\n' "$SUPERVISOR_TOKEN" \
        | curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -H @- \
          --data "$body" "http://supervisor/core/api/webhook/hungarian-transport-gtfs" >/dev/null 2>&1 && return 0
    fi
  fi
  echo "notify failed: $1" >> "$log"
  return 0
}

# HUNGARIAN_TRANSPORT_WEBHOOK from .env, read by the script's own parser so
# both send to the same place. Empty when python3 or the scripts are broken:
# then the default webhook it is.
env_webhook() {
  command -v python3 >/dev/null 2>&1 && [ -f "$base/gtfs_compact.py" ] || return 0
  python3 -I -c 'import os, sys
sys.path.insert(0, sys.argv[1])
import gtfs_compact
gtfs_compact.load_env_files()
print(os.environ.get("HUNGARIAN_TRANSPORT_WEBHOOK", "").strip())' "$base" 2>/dev/null || true
}

worker() {
  local rc=0
  if [ -z "$webhook" ]; then webhook=$(env_webhook); fi
  webhook="${webhook:-$default_webhook}"
  # Volán shapes.txt (8 M points) adds about a minute on a desktop and several
  # on a Raspberry Pi, hence the generous limit. SIGTERM lets the script report
  # itself; a minute later it is killed for good (137).
  PYTHONUNBUFFERED=1 timeout -k 60 1800 python3 "$base/update_volan_index.py" >> "$log" 2>&1 < /dev/null || rc=$?
  case "$rc" in
    0|"$reported"|124) ;;  # finished, or the script reported itself (124: SIGTERM delivered)
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
