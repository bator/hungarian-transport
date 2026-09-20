#!/bin/bash
# HA shell_command kills the process group after 60s. Detach so a 100 MB GTFS
# rebuild can finish; the Python script notifies via webhook when it is done.
set -eu
mkdir -p /config/hungarian-transport
setsid env PYTHONUNBUFFERED=1 timeout 600 python3 /config/hungarian-transport/update_volan_index.py \
  >> /config/hungarian-transport/update.log 2>&1 < /dev/null &
echo "started $!"
