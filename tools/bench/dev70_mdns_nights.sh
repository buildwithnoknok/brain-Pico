#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# dev70_mdns_nights.sh - DEV-70 unattended mDNS idle test on the Pi4 bench.
#
# Runs PHASES one after the other (default "on off on", HOURS each, default 12):
#   1. writes the brain's settings.toml = BASE + "NOKNOK_MDNS = 1|0" (pico.py put, the ONLY
#      pico.py contact of the phase), then a real USB power cut (uhubctl -l 2, ganged VBUS),
#   2. waits for the boot, proves the reset (hello uptime small) and the mDNS state
#      (on: noknok-XXXX.local resolves; off: it must not),
#   2b. waits up to BOOT_WAIT_S (40 min) for a brain that booted offline to join;
#   3. polls `hello` by IP every 30 s, and resolves the .local name every 10 min,
#   4. a hang (10 failed hellos in a row = 5 min) ends the phase early: captures ping, ARP,
#      the name lookup and the serial console (listen, then one Ctrl-C) BEFORE the next
#      phase's power cut. At the end of each phase: `status` (events, [WDT]).
# No bench watchdog is involved (NOKNOK_WATCHDOG must not be in BASE).
#
# Usage (on the Pi, detached):
#   nohup bash dev70_mdns_nights.sh BASE.toml [HOURS] [PHASES...] > ~/dev/pico/dev70/run.log 2>&1 &
#   BASE.toml = the bench brain's own settings.toml without any NOKNOK_MDNS line.
# Read:  grep -E 'PHASE|HANG|RESULT|FAIL' ~/dev/pico/dev70/run.log ; per-phase poll log
#        ~/dev/pico/dev70/<n>_<on|off>.log (one line per poll).
# DEV-67 shares this brain: never run both at once.
set -u
BASE=$1; shift
HOURS=${1:-12}; [ $# -gt 0 ] && shift
PHASES=${*:-on off on}
MAC=98:fe:54:0f:cb:ee           # bench brain Pico 2 W 82322992A5A2A324
NAME=noknok-a324.local
IP_GUESS=192.168.1.194
BOOT_WAIT_S=${BOOT_WAIT_S:-2400}  # 40 min: an offline cold boot rejoins on its own (DEV-79)
PICO=/home/noknok/dev/pico
RPC="python3 /home/noknok/dev/brain-Pico/tools/rpc_call.py"
OUT=$PICO/dev70; mkdir -p "$OUT"
ts() { date '+%F %T'; }

find_ip() {   # by ARP (works with mDNS off too): ping the guess, else sweep the /24
    ping -c 1 -W 1 "$IP_GUESS" >/dev/null 2>&1
    ip=$(ip neigh | grep -i "$MAC" | grep -v FAILED | awk '{print $1}' | head -1)
    if [ -z "$ip" ]; then
        for i in $(seq 1 254); do ping -c 1 -W 1 192.168.1.$i >/dev/null 2>&1 & done; wait
        ip=$(ip neigh | grep -i "$MAC" | grep -v FAILED | awk '{print $1}' | head -1)
    fi
    echo "$ip"
}

uptime_of() { $RPC "$1" hello 2>/dev/null | grep '"uptime"' | tr -dc '0-9.'; }

capture_hang() {   # $1 = ip, $2 = log
    {
        echo "$(ts) HANG capture --------------------------------"
        echo "-- ping"; ping -c 3 -W 2 "$1"
        echo "-- arp";  ip neigh | grep -i "$MAC"
        echo "-- name"; getent hosts "$NAME" || echo "name does not resolve"
        echo "-- serial ports"; ls /dev/serial/by-id/
        echo "-- serial listen 10 s, then one Ctrl-C"
        timeout 30 python3 /home/noknok/dev/brain-Pico/tools/bench/serial_listen.py 10 --ctrlc
        echo "$(ts) HANG capture end"
    } >> "$2" 2>&1
}

n=0
for mode in $PHASES; do
    n=$((n + 1)); LOG=$OUT/${n}_${mode}.log; : > "$LOG"
    [ "$mode" = on ] && v=1 || v=0
    echo "$(ts) PHASE $n mdns=$mode start ($HOURS h)"
    { grep -v NOKNOK_MDNS "$BASE"; echo "NOKNOK_MDNS = $v"; } > "$OUT/settings.toml"
    (cd "$PICO" && timeout 120 ./pico.py put "$OUT/settings.toml" settings.toml) \
        || echo "$(ts) FAIL settings put (phase $n)"
    echo raspberry | sudo -S -p "" /usr/sbin/uhubctl -l 2 -a off >/dev/null; sleep 4
    echo raspberry | sudo -S -p "" /usr/sbin/uhubctl -l 2 -a on  >/dev/null
    sleep 90
    # A cold boot can come up OFFLINE (seen 23 Oct 02:57 and 03:04): the product runs and
    # the brain joins later through its rejoin loop. Wait up to BOOT_WAIT_S for that
    # instead of calling it a hang; how long it took is part of the result.
    t_cut=$(( $(date +%s) - 90 )); IP=""; up=""
    while [ $(( $(date +%s) - t_cut )) -lt $BOOT_WAIT_S ]; do
        IP=$(find_ip); [ -n "$IP" ] && up=$(uptime_of "$IP")
        [ -n "$up" ] && break
        sleep 30
    done
    echo "$(ts) PHASE $n boot: ip=${IP:-none} uptime=${up:-none} online $(( $(date +%s) - t_cut )) s after the cut (small uptime = real reset)"
    nm=$(getent hosts "$NAME" | awk '{print $1}')
    echo "$(ts) PHASE $n name $NAME -> ${nm:-not resolved} (expect: $mode)"
    if [ -z "$up" ]; then echo "$(ts) FAIL brain not online $BOOT_WAIT_S s after the cut, phase $n skipped"; continue; fi
    end=$(( $(date +%s) + HOURS * 3600 )); fails=0; polls=0; bad=0; i=0; hung=0; pu=
    while [ "$(date +%s)" -lt "$end" ]; do
        r=$($RPC "$IP" hello 2>&1 | grep -E '"uptime"|ERROR' | tr -d ' \n')
        polls=$((polls + 1))
        if echo "$r" | grep -q uptime; then
            fails=0; u=$(echo "$r" | tr -dc '0-9.' | cut -d. -f1)
            # a reset in the middle of a phase = uptime went backwards
            [ -n "${pu:-}" ] && [ "$u" -lt "$pu" ] && echo "$(ts) RESET phase $n: uptime $pu -> $u"
            pu=$u
        else fails=$((fails + 1)); bad=$((bad + 1)); fi
        line="$(ts) $r"
        if [ $((i % 20)) -eq 0 ]; then
            line="$line name=$(getent hosts "$NAME" | awk '{print $1}')"
        fi
        echo "$line" >> "$LOG"; i=$((i + 1))
        if [ $fails -ge 10 ]; then
            echo "$(ts) HANG phase $n mdns=$mode after $polls polls"; hung=1
            capture_hang "$IP" "$LOG"; break
        fi
        sleep 30
    done
    if [ $hung -eq 0 ]; then
        $RPC "$IP" status > "$OUT/${n}_${mode}_status.json" 2>&1
        wdt=$(grep -c 'WDT' "$OUT/${n}_${mode}_status.json")
        echo "$(ts) RESULT phase $n mdns=$mode CLEAN-RUN polls=$polls failed=$bad last_uptime=$(uptime_of "$IP") wdt_events=$wdt"
    else
        echo "$(ts) RESULT phase $n mdns=$mode HANG polls=$polls failed=$bad"
    fi
done
echo "$(ts) ALL PHASES DONE"
