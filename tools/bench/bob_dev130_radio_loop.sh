#!/bin/bash
# Bob: repeat the DEV-130 radio check (station scan -> start_ap -> stop_ap -> failed join -> again)
cd ~/dev/pico
for n in 1 2 3 4 5 6; do
  for i in 1 2 3; do timeout 150 python3 pico.py run ~/dev/brain-Pico/tools/bench/bench_dev130_scan_then_ap.py 120 > ~/bob_scan_$n.txt 2>&1; [ -s ~/bob_scan_$n.txt ] && break; sleep 5; done
  echo "RUN $n: $(tail -3 ~/bob_scan_$n.txt | tr '\n' '|' | cut -c1-200)"
done
