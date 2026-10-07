# SPDX-License-Identifier: MIT
# bench_maxi_enum.py - DEV-73 bench check: enumerate both buses and list every
# module the Smart Lamp Maxi needs (USB LEDs, LED Button, Knob, Buzzer, Display)
# with UID, address and firmware version, then read the knob for 8 s (turn and
# press it while this runs). Run: ./pico.py run bench_maxi_enum.py ; ./pico.py reset
import os, time
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

show("leds16", c.leds16)
show("leds", c.leds)
show("ledbutton", c.ledbutton)
show("knob", c.knob)
show("buzzer", c.buzzer)
show("display", c.display)
ok = all((c.leds16 or c.leds, c.ledbutton, c.knob, c.buzzer, c.display))
print("MAXI SET COMPLETE" if ok else "MAXI SET INCOMPLETE")

if c.knob:
    k = c.knob[0]
    print("reading the knob for 8 s - turn and press it")
    t0 = time.monotonic()
    total, presses, was = 0, 0, False
    while time.monotonic() - t0 < 8:
        r = k.read()
        if r is None:
            print("  read -> None (I2C error)")
        else:
            total += r.delta
            if r.pressed and not was:
                presses += 1
            was = r.pressed
        time.sleep(0.05)
    print("knob: net turn %d, presses %d" % (total, presses))
