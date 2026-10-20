#!/bin/sh
# SPDX-License-Identifier: MIT
# pullrun.sh ROUNDS [MIN_DELAY MAX_DELAY] (seconds after the update starts) - DEV-124 power-pull run, Pi side. Needs J1 powered from a Pi USB
# port. Each round: start an app update on the Pico, cut ALL Pi USB power (uhubctl -l 2, a real VBUS cut on
# the Pi 4) at a random moment, restore it, classify the power manager, recover it if parked.
# Log: ~/bob/pullrun.log (one line per round) and ~/bob/pr/r_<n>.txt. Tally at the end.
ROUNDS=${1:-5}; MIN=${2:-0.05}; MAX=${3:-1.8}   # MIN/MAX: random delay AFTER the PR START marker
cd /home/noknok/dev/pico || exit 1
mkdir -p ~/bob/pr; : > ~/bob/pullrun.log
usb() { echo raspberry | sudo -S /usr/sbin/uhubctl -l 2 -a "$1" >/dev/null 2>&1; }
wait_port() { i=0; while [ $i -lt 40 ]; do ls /dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00 >/dev/null 2>&1 && return 0; i=$((i+1)); sleep 1; done; return 1; }
classify() { for t in 1 2 3 4; do o=$(timeout 40 python3 pico.py run pr_check.py 30 2>&1 | grep "PR STATE"); [ -n "$o" ] && { echo "$o"; return; }; sleep 4; done; echo "PR STATE unreadable"; }
n=0; hit=0; clean=0; early=0; parked=0; brick=0; old=0; new=0
while [ $n -lt $ROUNDS ]; do
  n=$((n+1))
  rm -f ~/bob/pr/r_$n.txt
  (timeout 40 python3 pico.py run pr_update.py 30 > ~/bob/pr/r_$n.txt 2>&1 &)
  # wait for the Pico's "PR START" marker (the update begins right after it), then cut after a random delay
  w=0; while [ $w -lt 400 ]; do grep -q "PR START" ~/bob/pr/r_$n.txt 2>/dev/null && break; sleep 0.05; w=$((w+1)); done
  d=$(python3 -c "import random;print(round(random.uniform($MIN,$MAX),2))")
  sleep "$d"
  usb 0; sleep 3; usb 1
  pkill -f '[p]ico.py run pr_update'
  wait_port || { echo "round $n: Pico port did not come back" >> ~/bob/pullrun.log; brick=$((brick+1)); continue; }
  sleep ${WAIT:-12}   # WAIT=100 NOREC=1: let the brain's own start-up rescue fix a parked power manager
  started=$(grep -c "PR START" ~/bob/pr/r_$n.txt); pushed=$(grep -c "PR PUSHED" ~/bob/pr/r_$n.txt)
  s=$(classify)
  case "$s" in
    *parked*) parked=$((parked+1)); [ "${NOREC:-0}" = 1 ] && s="$s ** BRAIN DID NOT RESCUE IT **"; rec=$(timeout 60 python3 pico.py run pr_recover.py 40 2>&1 | grep "PR RECOVERED"); s="$s -> $rec" ;;
    *none*|*unreadable*) brick=$((brick+1)) ;;
    *"app (1, 0, 3, 0)"*) old=$((old+1)) ;;
    *"app (1, 0, 3, 1)"*) new=$((new+1)) ;;
  esac
  if [ "$started" -eq 0 ]; then kind="before-update"; early=$((early+1)); elif [ "$pushed" -ge 1 ]; then kind="update-finished-first"; clean=$((clean+1)); else kind="MID-UPDATE"; hit=$((hit+1)); fi
  echo "round $n delay=${d}s $kind | $s" >> ~/bob/pullrun.log
done
echo "TALLY rounds=$n mid-update-cuts=$hit finished-before-cut=$clean cut-before-start=$early parked=$parked old=$old new=$new dead=$brick" >> ~/bob/pullrun.log
