#!/bin/bash
# Bob: DEV-132 rerun: per due boot, read events ring + nvm and keep the evidence of each boot.
B=~/dev/brain-Pico/tools/bench
cd ~/dev/pico
: > ~/bob_d132b.log
for n in 1 2 3 4 5 6; do
  python3 pico.py run $B/bench_dev133_due.py 30 > ~/bob_due_$n.txt 2>&1
  sleep 115
  for i in 1 2 3 4; do python3 pico.py run $B/bench_dev133_read.py 40 > ~/bob_rd_$n.txt 2>&1; grep -q nvm ~/bob_rd_$n.txt && break; sleep 6; done
  echo "=== due boot $n: due-script-ran=$(grep -c 'OTA check due' ~/bob_due_$n.txt) CRASH=$(grep -c CRASH ~/bob_rd_$n.txt) MemoryError=$(grep -c MemoryError ~/bob_rd_$n.txt) postOTAreload=$(grep -c 'first run after the post-OTA' ~/bob_rd_$n.txt) $(grep '== nvm' ~/bob_rd_$n.txt) productStarted=$(grep -c 'product.py present' ~/bob_rd_$n.txt)" >> ~/bob_d132b.log
  grep -E 'update check|OTA pass done|first run' ~/bob_rd_$n.txt | tail -3 | cut -c1-120 >> ~/bob_d132b.log
done
echo DONE >> ~/bob_d132b.log
