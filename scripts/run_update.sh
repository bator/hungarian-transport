#!/bin/bash
# HA shell_command kills the process group after 60s. Detach so a 100 MB GTFS
# rebuild can finish; the Python script notifies via webhook when it is done.
set -eu
base=/config/hungarian-transport
mkdir -p "$base"
log="$base/update.log"
if [ -f "$log" ]; then
  sz=$(wc -c < "$log" || echo 0)
  if [ "${sz:-0}" -gt 1048576 ]; then
    mv "$log" "$log.1" || true
  fi
fi
setsid env PYTHONUNBUFFERED=1 timeout 600 python3 "$base/update_volan_index.py" \
  >> "$log" 2>&1 < /dev/null &
echo "started $!"
