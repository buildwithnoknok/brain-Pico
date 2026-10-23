#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev79_router_off.sh - DEV-79 acceptance on the Pi4 bench: the brain cold-boots with
# the router OFF, runs its product offline, and joins by itself once the router is back.
# Runs entirely on the Pi (it loses its own WiFi while the router is off), detached:
#   nohup bash bench_dev79_router_off.sh > ~/dev/pico/dev79_router.log 2>&1 &
# A person: unplugs the router when asked (the script waits for the router to vanish),
# waits ~5 min, plugs it back in. The script:
#   1. waits until the router (192.168.1.1) stops answering ping,
#   2. cuts the brain's USB power (uhubctl -l 2) -> cold boot with no network,
#   3. records the brain's console the whole time (serial_listen.py: passive, sends nothing),
#   4. waits for the router to answer again, then polls hello every 10 s and records how
#      long after the router came back the brain answered, plus the .local name.
# Read: grep -E 'STEP|RESULT' ~/dev/pico/dev79_router.log; console in ~/dev/pico/dev79_console.log
set -u
ROUTER=192.168.1.1
BRAIN=192.168.1.194
NAME=noknok-a324.local
BP=/home/noknok/dev/brain-Pico
ts() { date '+%F %T'; }
echo "$(ts) STEP 0 waiting for the router to go away (unplug it now)"
# "gone" = no answer for 30 s in a row: a short WiFi blip must not start the test
# (23 Oct 10:58 a 3 min unplanned outage did, and was misread as the deliberate test)
miss=0
while [ $miss -lt 15 ]; do
    if ping -c 1 -W 1 $ROUTER >/dev/null 2>&1; then miss=0; else miss=$((miss + 1)); fi
    sleep 1
done
echo "$(ts) STEP 1 router gone (no answer for 30 s)"
timeout 3000 python3 $BP/tools/bench/serial_listen.py 2700 > ~/dev/pico/dev79_console.log 2>&1 &
sleep 2
echo raspberry | sudo -S -p "" /usr/sbin/uhubctl -l 2 -a off >/dev/null; sleep 4
echo raspberry | sudo -S -p "" /usr/sbin/uhubctl -l 2 -a on >/dev/null
echo "$(ts) STEP 2 brain power-cut, cold boot without a network (plug the router back in after ~5 min)"
until ping -c 1 -W 1 $ROUTER >/dev/null 2>&1; do sleep 2; done
T_BACK=$(date +%s)
echo "$(ts) STEP 3 router answers again"
for i in $(seq 1 180); do
    up=$(python3 $BP/tools/rpc_call.py $BRAIN hello 2>/dev/null | grep '"uptime"' | tr -dc '0-9.')
    if [ -n "$up" ]; then
        echo "$(ts) RESULT brain online $(( $(date +%s) - T_BACK )) s after the router came back (brain uptime $up s)"
        sleep 20
        echo "$(ts) RESULT name $NAME -> $(getent hosts $NAME | awk '{print $1}')"
        python3 $BP/tools/rpc_call.py $BRAIN status | grep -A3 '"link"'
        break
    fi
    sleep 10
done
[ -z "${up:-}" ] && echo "$(ts) RESULT brain NOT online 30 min after the router came back"
echo "$(ts) STEP 4 done (console recording keeps running until its 45 min are up)"
