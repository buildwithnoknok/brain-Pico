#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# dev70_at.sh HH:MM HOURS PHASES... - start the DEV-70 night run (dev70_mdns_nights.sh) at a
# clock time today, plus a link watcher: every 15 min `status` over /rpc (no USB contact) logs
# the brain's WiFi link counters (drops, rejoins, backoff) and uptime, so a lost WiFi and the
# rejoin show up in the morning without touching the console.
#
# Usage (on the Pi, detached):
#   nohup bash dev70_at.sh 20:00 12 on > ~/dev/pico/dev70/run3.log 2>&1 < /dev/null &
# Logs: run3.log (phases, HANG, RESULT), <n>_<on|off>.log (polls), link3.log (watcher).
# Stop: kill by PID (ps -eo pid,args | grep dev70_) - never pkill -f over ssh.
set -u
AT=$1; HOURS=$2; shift 2
PHASES=${*:-on}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
OUT=/home/noknok/dev/pico/dev70
IP=${IP:-192.168.1.194}
ts() { date '+%F %T'; }
cd "$REPO" && git pull -q --ff-only 2>/dev/null
wait_s=$(( $(date -d "$AT" +%s) - $(date +%s) ))
[ $wait_s -lt 0 ] && { echo "$(ts) $AT is already past - not starting"; exit 1; }
echo "$(ts) scheduled: waiting for $AT ($wait_s s), then $HOURS h per phase: $PHASES"
sleep $wait_s
end=$(( $(date +%s) + HOURS * 3600 * $(echo $PHASES | wc -w) + 2400 ))
(
    sleep 300                                    # after the phase start (settings put + cut)
    while [ "$(date +%s)" -lt "$end" ]; do
        r=$(python3 "$REPO/tools/rpc_call.py" "$IP" status 2>&1 | python3 -c '
import json, sys
t = sys.stdin.read()
try:
    d = json.loads(t)
    print("link=%s mem_free=%s modules=%d" % (d.get("link"), d.get("mem_free"), len(d.get("modules", []))))
except Exception:
    print("ERROR " + t.strip().splitlines()[-1][:120] if t.strip() else "ERROR empty")')
        u=$(python3 "$REPO/tools/rpc_call.py" "$IP" hello 2>/dev/null | grep '"uptime"' | tr -dc '0-9.')
        echo "$(ts) uptime=${u:-none} $r" >> "$OUT/link3.log"
        sleep 900
    done
) &
exec bash "$REPO/tools/bench/dev70_mdns_nights.sh" "$OUT/base.toml" "$HOURS" $PHASES
