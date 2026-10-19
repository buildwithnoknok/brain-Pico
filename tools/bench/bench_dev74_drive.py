# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev74_drive.py - DEV-74: modules on BOTH I2C buses are drivable from a
# product-style script and found by UID across buses. Drives the Buzzer (bus A:
# one beep) and the LED Button (bus B: red, green, off). Checks i2c_bus + by_uid.
import time
import noknok

c = noknok.Conductor()
c.enumerate()
ok = True
def check(name, cond, info=""):
    global ok
    ok = ok and bool(cond)
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, info))

bz, lb = c.buzzer[0], c.ledbutton[0]
check("buzzer is on bus A", getattr(bz, "i2c_bus", None) == "A", bz.i2c_bus)
check("LED Button is on bus B", getattr(lb, "i2c_bus", None) == "B", lb.i2c_bus)
check("different addresses", bz.address != lb.address, "0x%02X / 0x%02X" % (bz.address, lb.address))
for m in (bz, lb):
    check("by_uid finds %s across buses" % m._uid_hex, c.by_uid(m._uid_hex) is m)
bz.play(880, 120)
for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 0)):
    lb.set_color(*rgb)
    time.sleep(0.4)
print("SUMMARY:", "ALL GOOD" if ok else "FAILURES")
