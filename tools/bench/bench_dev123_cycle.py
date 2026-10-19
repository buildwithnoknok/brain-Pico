# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev123_cycle.py - DEV-123 failure point: status() while bus A is power-cycled.
# cycle_rail("bus_a", 300) then status() in a tight loop for ~3 s: each call must END (a value, or a
# PicoHubError) in well under a second - never hang. 5 rounds; at the end the hub must answer again.
import time
import noknok

c = noknok.Conductor()
c.enumerate()
ph = c.picohub
fails = 0
for rnd in range(5):
    ph.cycle_rail("bus_a", 300)
    t0 = time.monotonic()
    ok = err = 0
    worst = 0.0
    while time.monotonic() - t0 < 3.0:
        t1 = time.monotonic()
        try:
            ph.status()
            ok += 1
        except noknok.PicoHubError:
            err += 1
        except OSError:
            err += 1
        worst = max(worst, time.monotonic() - t1)
    final = None
    try:
        final = ph.status()["contract_v"]
    except Exception as e:
        final = repr(e)
    good = worst < 1.0 and ok > 0 and not isinstance(final, str)
    fails += 0 if good else 1
    print("round %d: %d ok, %d errors, slowest call %.3f s, final contract %s  %s"
          % (rnd + 1, ok, err, worst, final, "PASS" if good else "FAIL"))
print("SUMMARY", "ALL GOOD" if not fails else "%d FAILED" % fails)
