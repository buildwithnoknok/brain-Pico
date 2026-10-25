#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# dev70_guard.sh END_FILE PHASE - keep a DEV-70 night run going until a fixed end time, even
# across a Pi restart (a plain nohup launcher died with the Pi on 25 Oct 2026).
#
# END_FILE holds the end as epoch seconds. Run it once by hand to start, and from cron @reboot:
#   @reboot sleep 90 && bash .../dev70_guard.sh ~/dev/pico/dev70/night3.end on >> ~/dev/pico/dev70/run3.log 2>&1
# Each start runs dev70_at.sh (one phase PHASE, the remaining whole hours, link watcher) one
# minute from now; after the end time it does nothing (remove the cron line when done).
# Never runs twice: exits if a dev70 run is already active.
set -u
END_FILE=$1; PHASE=${2:-on}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
ts() { date '+%F %T'; }
[ -f "$END_FILE" ] || { echo "$(ts) guard: no end file $END_FILE - nothing to do"; exit 0; }
end=$(cat "$END_FILE")
left=$(( end - $(date +%s) ))
hours=$(( left / 3600 ))
if pgrep -f "[d]ev70_mdns_nights.sh|[d]ev70_at.sh" >/dev/null; then
    echo "$(ts) guard: a dev70 run is already active - not starting another"; exit 0
fi
if [ $hours -lt 1 ]; then
    echo "$(ts) guard: less than 1 h left before $(date -d @$end '+%F %T') - not starting"; exit 0
fi
echo "$(ts) guard: (re)starting phase $PHASE for $hours h, planned end $(date -d @$end '+%F %T')"
exec bash "$REPO/tools/bench/dev70_at.sh" "$(date -d '+1 min' +%H:%M)" "$hours" "$PHASE"
