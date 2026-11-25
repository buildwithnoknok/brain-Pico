#!/bin/bash
# SPDX-License-Identifier: MIT
# usage: run_probe.sh NAME SECONDS  (monitor + brain run, then stall analysis)
N=$1; S=$2
cd ~/dev/pico
(nohup python3 -u zz_monitor.py 192.168.1.194 $((S+40)) > /tmp/zz_mon_$N.log 2>&1 &)
sleep 1
PICO_SERIAL=82322992A5A2A324 timeout $((S+60)) python3 -u pico.py run zz_probe_$N.py $((S+30)) 2>&1 | grep -a 'PROBE' | head -40
sleep 8
python3 - $N <<'P'
import sys
rows = []
for l in open('/tmp/zz_mon_%s.log' % sys.argv[1]):
    p = l.split()
    if len(p) >= 4 and p[0] == 'MON':
        rows.append((float(p[1]), float(p[2]), ' '.join(p[3:])))
bad = [r for r in rows if r[2] != 'ok']
print('MONITOR samples %d, errors %d, max latency %.2f s' % (len(rows), len(bad), max([r[1] for r in rows] or [0])))
win, cur = [], None
for r in rows:
    s = r[1] > 1.0 or r[2] != 'ok'
    if s and cur is None: cur = [r[0], r[0] + r[1]]
    elif s: cur[1] = r[0] + r[1]
    elif cur is not None: win.append(cur); cur = None
if cur: win.append(cur)
for w in win: print('STALL start %.1f length %.1f s' % (w[0], w[1] - w[0]))
if not win: print('no stall longer than 1 s')
P
