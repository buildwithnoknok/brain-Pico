# SPDX-License-Identifier: MIT
# bench_maxi_enum.py - DEV-73 bench check: enumerate both buses and list every
# module the Smart Lamp Maxi needs (USB LEDs, LED Button, Knob, Buzzer, Display)
# with UID, address and firmware version. Run: ./pico.py run bench_maxi_enum.py
import os
from noknok import Conductor

print("I2C pins from settings.toml: SDA=%s SCL=%s"
      % (os.getenv("NOKNOK_I2C_SDA"), os.getenv("NOKNOK_I2C_SCL")))
c = Conductor()
c.enumerate_all()

def show(kind, mods):
    if not mods:
        print("%-10s MISSING" % kind)
        return
    for m in mods:
        print("%-10s uid=%s addr=%s fw=%s" % (
            kind, getattr(m, "_uid_hex", None) or getattr(m, "uid", None),
            hex(m.address) if hasattr(m, "address") else "usb",
            getattr(m, "firmware_version", None)))

show("leds", c.leds)
show("ledbutton", c.ledbutton)
show("knob", c.knob)
show("buzzer", c.buzzer)
show("display", c.display)
ok = all((c.leds, c.ledbutton, c.knob, c.buzzer, c.display))
print("MAXI SET COMPLETE" if ok else "MAXI SET INCOMPLETE")
