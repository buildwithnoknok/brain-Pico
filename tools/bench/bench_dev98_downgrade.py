# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev98_downgrade.py - DEV-98: put an OLDER layout-2 Display app (/display_050.bin, v0.5.0)
# on the Display, and make the daily OTA check due (nvm[1:5] = 0), so the next ONLINE boot has
# to update it through the product OTA pass. Run, then soft-reboot the brain.
import microcontroller
import noknok
from module_flasher import ModuleFlasher

c = noknok.Conductor()
c.enumerate()
d = c.display[0]
bus = c.i2c if d.i2c is c.i2c else c.i2c_b
img = open("/display_050.bin", "rb").read()
print("flashing %d B over the Display (was %s)" % (len(img), getattr(d, "firmware_version", "?")))
ModuleFlasher(bus).flash(img, runtime_addr=d.address)
microcontroller.nvm[1:5] = bytes(4)          # last OTA check = never -> due
print("flashed; OTA check made due")
