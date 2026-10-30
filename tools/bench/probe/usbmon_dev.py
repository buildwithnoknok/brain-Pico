# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
# usbmon_dev.py FILE DEV... - per device: bulk-IN (console) completions with data, timeline, last events
import sys, collections
f, devs = sys.argv[1], sys.argv[2:]
for d in devs:
    ev = [l.split() for l in open(f) if (":1:%s:" % d) in l]
    if not ev: print("dev", d, "none"); continue
    t0 = int(ev[0][1]); t1 = int(ev[-1][1])
    bi = [e for e in ev if e[3].startswith("Bi") and e[2] == "C"]
    data = [e for e in bi if e[4] == "0" and int(e[5]) > 0]
    st = collections.Counter((e[4], "len>0" if e[5] != "0" else "len0") for e in bi)
    print("=== dev %s: span %.1f s, %d events; bulk-IN completions %s" % (d, (t1 - t0) / 1e6, len(ev), dict(st)))
    if data:
        print("  first console data at +%.2f s, last at +%.2f s, %d packets" % ((int(data[0][1]) - t0) / 1e6, (int(data[-1][1]) - t0) / 1e6, len(data)))
    else:
        print("  NO console data from the device at all")
    ctl = [e for e in ev if e[3].startswith("Co") and e[2] == "S"]
    print("  control OUT requests (setup):", [" ".join(e[5:10]) for e in ctl][-6:])
