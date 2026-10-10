# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_unpark.py - bring modules that are parked in their bootloader (0x7E) back to
# their application. Use after an interrupted bootloader read. Runs on the Pico via
# `pico.py run`. Needs module_flasher.py. Safe: BOOT only jumps to the app that is
# already there; it writes nothing. Several parked modules share 0x7E, but BOOT is a
# write-only command, so one call restarts all of them.

import time
import board
import busio
from module_flasher import ModuleFlasher

for label, sda, scl in (("GP8/9", board.GP8, board.GP9),):
    i2c = busio.I2C(scl, sda, frequency=100000)
    f = ModuleFlasher(i2c)
    print(label, "bootloader answers at 0x7E:", f.present())
    for n in range(4):
        if not f.present():
            break
        try:
            f.boot()
        except Exception as e:
            print("  boot() ->", repr(e))
        time.sleep(1.0)
        print("  after BOOT #%d, still parked:" % (n + 1), f.present())
    i2c.deinit()
print("done - now power-cycle check: run probe_pins.py / bench_dev65.py")
