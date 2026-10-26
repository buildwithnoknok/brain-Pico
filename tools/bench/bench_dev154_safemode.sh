#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev154_safemode.sh [IP] - DEV-154 on hardware: safemode.py restarts a brain out of
# CircuitPython safe mode at most twice in a row, then stays (no reboot loop); a real
# power-on resets the count.
#
# Needs code.py >= 0.37 + safemode.py on the brain, brain online, the Pi's ganged USB power
# switch (uhubctl -l 2) feeding the brain. Safe mode is requested with
# microcontroller.on_next_reset(RunMode.SAFE_MODE) (reason PROGRAMMATIC - the same safemode.py
# path as a hard fault; only USER, the boot buttons, is skipped by CircuitPython).
#   round 1, 2: safe mode -> safemode.py restarts -> product online, a [SAFE] event each
#   round 3:    safe mode -> stays in safe mode (REPL answers, reason PROGRAMMATIC, offline)
#   power cut:  normal boot, online, count back to 0 (nvm[6] = 0xA0)
# No filesystem write. Log: ~/dev154/run.log
set -u
IP=${1:-192.168.1.194}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PICO="python3 $REPO/tools/pico.py"
RPC="python3 $REPO/tools/rpc_call.py"
mkdir -p ~/dev154; LOG=~/dev154/run.log
ts() { date '+%F %T'; }
say() { echo "$(ts) $*" | tee -a "$LOG"; }
safe_events() { $RPC "$IP" status 2>/dev/null | grep -c '\[SAFE\]'; }
online_within() {   # $1 seconds -> prints seconds taken, or nothing
    local t0=$(date +%s)
    while [ $(( $(date +%s) - t0 )) -lt "$1" ]; do
        $RPC "$IP" hello 2>/dev/null | grep -q '"uptime"' && { echo $(( $(date +%s) - t0 )); return; }
        sleep 5
    done
}
nvm6() { timeout 40 $PICO exec "import microcontroller; print('NVM6=%02x' % microcontroller.nvm[6])" 2>/dev/null | grep -o 'NVM6=..'; }
to_safe_mode() {
    timeout 30 $PICO exec "import microcontroller
microcontroller.on_next_reset(microcontroller.RunMode.SAFE_MODE)
microcontroller.reset()" >> "$LOG" 2>&1
}

say "=== DEV-154 safe-mode bench start ip=$IP"
pass=0
for r in 1 2; do
    before=$(safe_events); before=${before:-0}
    say "round $r: [SAFE] events before: $before - requesting safe mode"
    to_safe_mode
    s=$(online_within 240)
    after=$(safe_events); after=${after:-0}
    if [ -n "$s" ] && [ "$after" -gt "$before" ]; then
        pass=$((pass + 1)); say "round $r: restarted out of safe mode, ONLINE after ${s} s, [SAFE] $before -> $after  PASS"
    else
        say "round $r: online=${s:-NO} [SAFE] $before -> $after  FAIL"
    fi
    sleep 15
done
say "round 3: requesting safe mode a third time in a row - must STAY in safe mode"
to_safe_mode
sleep 25
s=$(online_within 60)
info=$(timeout 40 $PICO exec "import supervisor, microcontroller; print('REASON=%s NVM6=%02x' % (supervisor.runtime.safe_mode_reason, microcontroller.nvm[6]))" 2>&1 | grep -o 'REASON=.*')
if [ -z "$s" ] && echo "$info" | grep -q "PROGRAMMATIC" && echo "$info" | grep -q "NVM6=a2"; then
    pass=$((pass + 1)); say "round 3: stayed in safe mode, not online, REPL answers: $info  PASS"
else
    say "round 3: online=${s:-no} $info  FAIL"
fi
say "power cut (uhubctl -l 2, real power-on): count must go back to 0"
echo raspberry | sudo -S -p "" /usr/sbin/uhubctl -l 2 -a off >/dev/null; sleep 4
echo raspberry | sudo -S -p "" /usr/sbin/uhubctl -l 2 -a on  >/dev/null
s=$(online_within 240)
v=$(nvm6)
if [ -n "$s" ] && [ "$v" = "NVM6=a0" ]; then
    pass=$((pass + 1)); say "after the power cut: ONLINE after ${s} s, $v (count 0)  PASS"
else
    say "after the power cut: online=${s:-NO} ${v:-nvm unread}  FAIL"
fi
timeout 15 $PICO exec "import microcontroller; microcontroller.reset()" >/dev/null 2>&1   # back to the product
say "=== done: $pass of 4 checks PASS"
