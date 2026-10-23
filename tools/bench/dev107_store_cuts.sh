#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# dev107_store_cuts.sh [ROUNDS] [IP] - DEV-107 hands-free: real power cut during a Store write,
# then catch a dead console on the next boot.
#
# Runs on the bench host (Pi4 `noknok`), brain on a Pi USB port, everything else powered from
# the brain's cable (so the ganged cut `uhubctl -l 2` = a real unplug of the whole lamp).
# Each round:
#   1. `pico.py run` a copy of software/bench_yank.py with PHASES = store, store, idle. This is
#      ALSO the probe of the previous boot: if it cannot reach the REPL, that boot is a HANG.
#   2. as soon as the tone starts, wait 0.5-6 s (random) and cut VBUS for 3 s.
#   3. even rounds: record the boot passively from the first second (boot_capture.py, 45 s),
#      odd rounds: touch nothing, first console contact at a random 5-30 s after power-on
#      (the original DEV-107 condition: pico.py into a booting brain).
# On a HANG it records, before anything else: /rpc hello over WiFi (is the VM alive? uptime),
# dmesg, a passive listen + one Ctrl-C, then a data-only port cycle (uhubctl -l 1-1 -p 4,
# no VBUS cut on this hub? uptime tells) and the console again, then one more pico.py try.
# A brain that stays dead is recovered with a ganged cut (that boot is then probed as usual).
#
# Logs: ~/dev107/run.log (one line per round + HANG blocks), ~/dev107/r<n>_*.log.
# Watch: grep -E 'ROUND|HANG|tally' ~/dev107/run.log
# Stop:  kill by PID (ps -eo pid,args | grep dev107_) - never pkill -f over ssh.
set -u
ROUNDS=${1:-20}
IP=${2:-192.168.1.194}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PICO="python3 -u $REPO/tools/pico.py"     # -u: the tone line must reach the log at once
RPC="python3 $REPO/tools/rpc_call.py"
OUT=~/dev107; mkdir -p "$OUT"
LOG=$OUT/run.log
SCRIPT=$OUT/bench_yank_store.py
sed -E 's/^PHASES *=.*/PHASES  = ("store", "store", "idle")   # dev107_store_cuts.sh/' \
    "$REPO/software/bench_yank.py" > "$SCRIPT"
grep -q 'dev107_store_cuts' "$SCRIPT" || { echo "could not patch PHASES"; exit 1; }
ts() { date '+%F %T'; }
say() { echo "$(ts) $*" | tee -a "$LOG"; }
cut() {
    echo raspberry | sudo -S -p "" /usr/sbin/uhubctl -l 2 -a off >/dev/null; sleep 3
    echo raspberry | sudo -S -p "" /usr/sbin/uhubctl -l 2 -a on  >/dev/null
}
up() { $RPC "$IP" hello 2>&1 | grep -E '"uptime"|ERROR' | tr -d ' \n'; }

diag() {   # $1 = round
    f=$OUT/r$1_hang.log
    {
        echo "$(ts) HANG diagnostics round $1"
        echo "-- rpc hello (VM alive?)"; up; echo
        echo "-- dmesg"; dmesg | tail -25
        echo "-- ports"; ls /dev/serial/by-id/
        echo "-- listen 10 s + one Ctrl-C"
        timeout 30 python3 "$REPO/tools/bench/serial_listen.py" 10 --ctrlc
        echo "-- rpc hello again"; up; echo
        echo "-- data-only port cycle (hub 1-1 port 4)"
        echo raspberry | sudo -S -p "" /usr/sbin/uhubctl -l 1-1 -p 4 -a cycle -d 2 >/dev/null
        sleep 8
        echo "-- rpc hello after the port cycle (uptime continued = no reset)"; up; echo
        dmesg | tail -8
        echo "-- listen 5 s + one Ctrl-C after the port cycle"
        timeout 30 python3 "$REPO/tools/bench/serial_listen.py" 5 --ctrlc
        echo "$(ts) HANG diagnostics end"
    } > "$f" 2>&1
    say "HANG round $1: $(grep -A1 'VM alive' "$f" | tail -1) | after port cycle: $(grep -A1 'uptime continued' "$f" | tail -1) | Ctrl-C after cycle bytes: $(grep -c '>>>' "$f")"
}

say "=== start rounds=$ROUNDS ip=$IP script=$SCRIPT"
hangs=0
for i in $(seq 1 "$ROUNDS"); do
    R=$OUT/r${i}_round.log
    timeout 100 $PICO run "$SCRIPT" 90 > "$R" 2>&1 &
    pid=$!
    started=0
    for t in $(seq 1 600); do        # up to 60 s for the tone
        grep -q 'TONE ON' "$R" && { started=1; break; }
        kill -0 $pid 2>/dev/null || break
        sleep 0.1
    done
    phase=$(grep -a -m1 -E '^ROUND .*(must survive|known unsafe)' "$R" | cut -c1-30)
    verdict=$(grep -a -m1 -E 'PASS|FAIL' "$R" | cut -c1-90)
    if [ $started = 0 ]; then
        wait $pid
        hangs=$((hangs + 1))
        say "ROUND $i: NO REPL - previous boot is a HANG ($(tail -1 "$R" | cut -c1-100))"
        diag "$i"
        if ! timeout 60 $PICO exec "print('alive')" 2>&1 | grep -q alive; then
            say "round $i: still dead after diagnostics - recovering with a ganged cut"
            cut; sleep 50
        else
            say "round $i: console came back after diagnostics"
        fi
        continue
    fi
    sleep "$(( RANDOM % 6 )).$(( RANDOM % 10 ))"; sleep 0.5
    mode=late
    if [ $((i % 2)) = 0 ]; then
        mode=capture
        python3 -u "$REPO/tools/bench/boot_capture.py" 45 > "$OUT/r${i}_boot.log" 2>&1 &
        cpid=$!
        sleep 0.3
    fi
    cut
    wait $pid 2>/dev/null
    if [ $mode = capture ]; then
        wait $cpid
        extra=$(grep -c . "$OUT/r${i}_boot.log")
    else
        d=$(( RANDOM % 26 + 5 )); sleep $d; extra="first contact at ${d}s"
    fi
    say "ROUND $i: $phase cut, mode=$mode ($extra lines) | prev verify: $verdict"
done
say "=== done: $hangs hang(s) in $ROUNDS rounds. Clean up: pico.py run software/bench_yank_cleanup.py"
