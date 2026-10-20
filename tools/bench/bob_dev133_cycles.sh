#!/bin/bash
# Bob: DEV-133 cycles: rescue_setup (16x -> 2.2.0), then due check (2.2.0 -> 2.2.1), read result.
B=~/dev/brain-Pico/tools/bench
cd ~/dev/pico
run() { for i in 1 2 3 4; do python3 pico.py run "$1" ${2:-40} > ~/bob_out.txt 2>&1; [ -s ~/bob_out.txt ] && grep -q "$3" ~/bob_out.txt && return; sleep 6; done; }
: > ~/bob_cycles.log
for n in 1 2 3; do
  echo "=== cycle $n" >> ~/bob_cycles.log
  python3 pico.py run $B/bench_dev133_rescue_setup.py 60 >/dev/null 2>&1
  sleep 150
  python3 pico.py run $B/bench_dev133_due.py 30 >/dev/null 2>&1
  sleep 150
  run $B/bench_dev133_read.py 40 "nvm"
  grep -E 'PENDING|\[USBFW\]|\[usb\] (using|usb_leds|hard)|CRASH|verified|product.py present|== nvm|2\.2\.[01]' ~/bob_out.txt | tail -18 >> ~/bob_cycles.log
done
echo DONE >> ~/bob_cycles.log
