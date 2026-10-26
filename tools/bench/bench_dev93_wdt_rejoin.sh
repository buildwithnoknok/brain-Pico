#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev93_wdt_rejoin.sh [ROUNDS] [IP] - DEV-93 evidence: after a WATCHDOG reset, does the
# brain come back on the home WiFi by itself (CircuitPython 10.3.x, Pico 2 W, CYW43)?
#
# Each round: pico.py stops the product (Ctrl-C), arms the hardware watchdog (5 s, RESET) and
# stops feeding it -> the chip resets. No filesystem write is involved (idle REPL), so this
# measures only the radio after a watchdog reset. Then /rpc `hello` is polled over WiFi for up
# to 4 min; `status` must list a new "[WDT] watchdog reset" event (code.py logs it at boot) -
# uptime alone is no proof (time.monotonic() may not restart on a watchdog reset, DEV-93).
# Logs: ~/dev93/wdt.log. Runs on the bench host; brain on USB and on the home WiFi.
set -u
ROUNDS=${1:-3}
IP=${2:-192.168.1.194}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PICO="python3 $REPO/tools/pico.py"
RPC="python3 $REPO/tools/rpc_call.py"
mkdir -p ~/dev93; LOG=~/dev93/wdt.log
ts() { date '+%F %T'; }
say() { echo "$(ts) $*" | tee -a "$LOG"; }
wdt_events() { $RPC "$IP" status 2>/dev/null | grep -c 'WDT'; }

say "=== start rounds=$ROUNDS ip=$IP"
ok=0
for i in $(seq 1 "$ROUNDS"); do
    before=$(wdt_events); [ -z "$before" ] && before=0
    say "round $i: WDT events before: $before - arming the watchdog (5 s) and starving it"
    timeout 30 $PICO exec "import microcontroller, time
from watchdog import WatchDogMode
w = microcontroller.watchdog
w.timeout = 5
w.mode = WatchDogMode.RESET
w.feed()
print('armed, not feeding')
time.sleep(20)
print('STILL ALIVE - watchdog did not fire')" >> "$LOG" 2>&1
    t0=$(date +%s); up=""
    while [ $(( $(date +%s) - t0 )) -lt 240 ]; do
        up=$($RPC "$IP" hello 2>/dev/null | grep '"uptime"' | tr -dc '0-9.')
        [ -n "$up" ] && break
        sleep 5
    done
    after=$(wdt_events); [ -z "$after" ] && after=0
    if [ -n "$up" ] && [ "$after" -gt "$before" ]; then
        ok=$((ok + 1))
        say "round $i: ONLINE $(( $(date +%s) - t0 )) s after the reset (uptime $up), WDT events $before -> $after  PASS"
    else
        say "round $i: online=${up:-NO} WDT events $before -> $after  FAIL ($(( $(date +%s) - t0 )) s)"
    fi
    sleep 20
done
say "=== done: $ok of $ROUNDS rounds back online after a watchdog reset"
