# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# Run on the brain: python3 pico.py run tools/bench/dev155_legacy_bl.py 240   (needs noknok.py >= 1.22)
# dev155_legacy_bl.py - DEV-155 hardware check (Sam, 25 Oct 2026). Runs on the bench Pico.
# Reads the bootloader version of every I2C module with the NEW noknok.py 1.22 +
# module_flasher.py, and shows what the layout gate concludes. Flashes nothing:
# each read is 0xB0 -> 0x7E -> 0xB1 -> BOOT (the module goes straight back to its app).
import noknok
from noknok import Conductor
print("noknok", noknok.__version__)
c = Conductor()
c.enumerate_all()
kn = c.knob[0]
uid = kn._uid_hex
print("KNOB", uid, "app", getattr(kn, "firmware_version", None))
print("restored bl (Store had [0,0,0,0,0]):", repr(getattr(kn, "bootloader", "not read")))
vals = []
for i in range(6):
    m = c.by_uid(uid)
    e = {"type": "knob", "bus": "i2c", "i2c_bus": getattr(m, "i2c_bus", "A"), "address": m.address, "uid": uid}
    vals.append(c.bootloader_version(e))
print("KNOB bl reads:", vals)
m = c.by_uid(uid)
print("KNOB module.bootloader:", m.bootloader, " layout_of:", c.layout_of(m.bootloader))
print("store bl:", noknok.store().get("state")[uid].get("bl"))
for name, lst in (("ledbutton", c.ledbutton), ("buzzer", c.buzzer), ("display", c.display)):
    if not lst:
        print(name, "not present")
        continue
    mm = lst[0]
    e = {"type": name, "bus": "i2c", "i2c_bus": getattr(mm, "i2c_bus", "A"), "address": mm.address, "uid": mm._uid_hex}
    v = c.bootloader_version(e)
    print(name.upper(), mm._uid_hex, "bl", v, "layout_of", c.layout_of(v))
print("SAM155 DONE")
