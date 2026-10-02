# SPDX-License-Identifier: MIT
# leds_probe.py - run ON THE PICO: find noknok USB LED modules, print serial + firmware version.
import noknok_usb
mods = noknok_usb.discover()
print("found", len(mods))
for serial, kind, m in mods:
    print("  ", kind, serial, "version", m.version())
