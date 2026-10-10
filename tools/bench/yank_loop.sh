#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# yank_loop.sh [RUNS] [--runtime-only] - drive software/bench_yank.py rounds hands-free.
#
# Runs on the bench host, brain on USB. Run 1 starts a round immediately. A person pulls the
# brain's cable during each TONE and plugs it back in; the loop records that boot from the first
# second (boot_capture.py, 35 s - a cold boot needs ~25 s before the product runs, and touching
# the console earlier can leave it dead, DEV-107), then runs the next round, which first VERIFIES
# the previous pull. RUNS = 8 pulls + 1 = 9 by default; the last round is a no-pull control.
#
#   --runtime-only  use a copy of bench_yank.py with PHASES = RUNTIME * 4 (idle/store only).
#                   Use it on a brain with real setup data: the SETUP rounds can wipe /data.
#
# Logs: ~/yank_run.log (rounds, PASS/FAIL tally), ~/yank_boot_<n>.log (each boot).
# Watch: tail -f ~/yank_run.log | grep -E 'ROUND|TONE|tally|lost'
# Stop:  kill the loop by PID (ps -eo pid,args | grep yank_loop) - not pkill -f over ssh.
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PICO="python3 $REPO/tools/pico.py"
RUNS=9; RT=0
for a in "$@"; do case "$a" in --runtime-only) RT=1 ;; *) RUNS=$a ;; esac; done
SCRIPT=$REPO/software/bench_yank.py
if [ $RT = 1 ]; then
  SCRIPT=$HOME/bench_yank_runtime.py
  sed -E 's/^PHASES *=.*/PHASES  = RUNTIME * 4   # yank_loop.sh --runtime-only/' "$REPO/software/bench_yank.py" > "$SCRIPT"
  grep -q '^PHASES  = RUNTIME \* 4' "$SCRIPT" || { echo "could not patch PHASES"; exit 1; }
fi
LOG=~/yank_run.log
echo "=== start $(date +%T) runs=$RUNS script=$SCRIPT" >> $LOG
for i in $(seq 1 $RUNS); do
  if [ $i -gt 1 ]; then
    echo "=== run $i: waiting for unplug+replug, recording boot" >> $LOG
    python3 -u "$REPO/tools/bench/boot_capture.py" 35 > ~/yank_boot_$i.log 2>&1
  fi
  echo "=== run $i: start $(date +%T)" >> $LOG
  timeout 90 $PICO run "$SCRIPT" 80 >> $LOG 2>&1
  echo "=== run $i ended rc=$? $(date +%T)" >> $LOG
done
echo "=== ALL RUNS DONE $(date +%T) - clean up with: pico.py run software/bench_yank_cleanup.py" >> $LOG
