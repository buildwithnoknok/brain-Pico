#!/bin/bash
# Bob: prep for the setup round: code.py version on the Pico, factory reset, hard reset, start console recorder
cd ~/dev/pico
cat > ~/bob_ver.py <<'PY'
import re
s = open("/code.py").read(3000)
m = re.search(r"[Vv]ersion[^\n]{0,60}|0\.\d\d", s)
print("CODEPY", m.group(0) if m else s[:120])
PY
for i in 1 2 3 4; do python3 pico.py run ~/bob_ver.py 20 2>&1 | grep -a CODEPY && break; sleep 5; done
for i in 1 2 3 4; do python3 pico.py run ~/dev/brain-Pico/tools/bench/bench_factory_reset.py 30 > ~/bob_fr.txt 2>&1; grep -q 'wifi after' ~/bob_fr.txt && break; sleep 8; done
tail -2 ~/bob_fr.txt
python3 pico.py run ~/dev/brain-Pico/tools/usb_bench/hard_reset.py 20 >/dev/null 2>&1
sleep 45
cd ~/dev/brain-Pico/tools/bench
nohup python3 serial_listen.py 2700 > ~/bob_setup_console6.txt 2>&1 &
sleep 8
tail -1 ~/bob_setup_console6.txt | cut -c1-100
