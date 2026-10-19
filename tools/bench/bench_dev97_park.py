# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev97_park.py - DEV-97 bench: PARK the buzzer in its bootloader (an update
# that lost power part-way: ERASE done, nothing written -> no valid app at 0x7E).
# Enumerates first so the UID -> type is in the Store state (the rescue needs it).
# Recover with the product rescue (that is the test) or bench_dev97_recover.py.
import time
import noknok
from module_flasher import ModuleFlasher

c = noknok.Conductor()
c.enumerate()
if not c.buzzer:
    raise SystemExit("FAIL: no buzzer enumerated")
b = c.buzzer[0]
bus = c.i2c if b.i2c is c.i2c else c.i2c_b
f = ModuleFlasher(bus)
print("buzzer addr=0x%02X uid=%s" % (b.address, getattr(b, "_uid_hex", "?")))
f.enter_bootloader(b.address)
f.wait_for_bootloader()
print("bootloader version:", f.get_version(), "uid:", f.get_uid())
f.erase()
print("PARKED: ERASE done, no app. status:", f._read_status())
