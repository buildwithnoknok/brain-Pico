# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev97_recover.py - put a good buzzer app back on a module parked at 0x7E
# (bench recovery when the product rescue did not do it). Image: /buzzer_firmware.bin
# or /data/fw_buzzer.bin on the Pico.
import noknok
from module_flasher import ModuleFlasher

c = noknok.Conductor()
for name, bus in c._buses():
    f = ModuleFlasher(bus)
    if f._read_status() is None:
        continue
    for p in ("/data/fw_buzzer.bin", "/buzzer_firmware.bin"):
        try:
            img = open(p, "rb").read()
            break
        except OSError:
            img = None
    if not img:
        raise SystemExit("FAIL: no buzzer image on the Pico")
    print("flashing %d B from %s on bus %s" % (len(img), p, name))
    f.flash(img)
    print("flashed - module resets into the app")
    break
else:
    print("nothing parked")
