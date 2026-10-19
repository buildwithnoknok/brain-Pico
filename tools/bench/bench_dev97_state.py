# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev97_state.py - READ-ONLY. DEV-97 bench: what is on the bus, is anything
# parked at 0x7E, what is in the /data firmware cache, which files exist.
# Run on the bench host: python3 pico.py run bench_dev97_state.py
import os
import noknok
from module_flasher import ModuleFlasher

c = noknok.Conductor()
print("noknok", getattr(noknok, "__version__", "?"))
for name, bus in c._buses():
    st = ModuleFlasher(bus)._read_status()
    print("bus %s: 0x7E %s" % (name, "PARKED status=%r" % (st,) if st else "empty"))
c.enumerate()
for attr in ("buzzer", "ledbutton", "knob", "display"):
    for m in getattr(c, attr, []) or []:
        print("%-10s addr=0x%02X uid=%s" % (attr, m.address, getattr(m, "_uid_hex", "?")))
for d in ("/data", "/"):
    try:
        print(d, sorted(os.listdir(d)))
    except OSError as e:
        print(d, "listdir failed", e)
