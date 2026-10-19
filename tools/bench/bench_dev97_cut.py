# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev97_cut.py - DEV-97 step 4: "power loss during the rescue flash".
# Parks the Display (ERASE), then flashes its cached image and does a HARD RESET
# of the brain (microcontroller.reset(), not a reload) after CUT_AT bytes: the brain
# dies mid-flash exactly as a power pull would, the module is left with a partial
# image. The NEXT boot's parked-module rescue must reflash it. This is a hard reset
# standing in for a pulled plug, not a physical pull (the module stays powered).
import microcontroller
import noknok
from module_flasher import ModuleFlasher

CUT_AT = 2500
c = noknok.Conductor()
c.enumerate()
d = c.display[0]
bus = c.i2c if d.i2c is c.i2c else c.i2c_b
f = ModuleFlasher(bus)
img = open("/data/fw_display.bin", "rb").read()
f.enter_bootloader(d.address)
f.wait_for_bootloader()
f.erase()
print("parked; flashing %d B, hard reset after %d B" % (len(img), CUT_AT))

def progress(done, total):
    if done >= CUT_AT:
        print("CUT at %d/%d" % (done, total))
        microcontroller.reset()

f.flash(img, progress=progress)
print("not cut?")
