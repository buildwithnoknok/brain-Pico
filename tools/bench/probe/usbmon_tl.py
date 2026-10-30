# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
# usbmon_tl.py FILE DEV [N] - compact timeline of one device (collapses repeated identical lines)
import sys
f, d = sys.argv[1], sys.argv[2]; n = int(sys.argv[3]) if len(sys.argv) > 3 else 80
ev = [l.split() for l in open(f) if (":1:%s:" % d) in l]
t0 = int(ev[0][1]); prev = None; rep = 0; out = 0
for e in ev:
    key = " ".join([e[2], e[3]] + e[4:10])
    if key == prev:
        rep += 1; continue
    if rep: print("        (x%d more)" % rep); rep = 0
    print("%8.3f %s" % ((int(e[1]) - t0) / 1e6, key)); prev = key; out += 1
    if out >= n: break
