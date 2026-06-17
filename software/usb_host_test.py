# usb_host_test.py  -  Does CircuitPython USB host work on this Pico 2 W?
#
# CircuitPython hosts USB via usb_host.Port on a CONSECUTIVE GPIO PAIR (PIO-USB) -
# NOT the Pico's native USB connector. So you must wire the device's USB lines to
# these GPIOs (a USB-A breakout / the powered hub's data lines):
#
#     device D+  -> GP16   (DP, must be the even/lower pin)
#     device D-  -> GP17   (DM, DP+1)
#     device 5V  -> VBUS   (use the POWERED hub so the device gets 5V)
#     device GND -> Pico GND   (common ground - REQUIRED)
#
# Power the Pico itself via VSYS (or the hub back-feeding VBUS), since its native
# USB port is free here. Put this file on CIRCUITPY as code.py (or import it).
#
# Wiring reference: https://learn.adafruit.com/using-a-keyboard-with-usb-host

import time
import board
import usb_host
import usb.core

DP = board.GP16   # USB D+   <- change to match your wiring
DM = board.GP17   # USB D-

print("Bringing up USB host port on", DP, "/", DM, "...")
try:
    port = usb_host.Port(DP, DM)
    print("  usb_host.Port OK")
except Exception as e:
    print("  usb_host.Port FAILED:", e)
    raise

print("Scanning for USB devices - plug a keyboard (or the LED module) into the hub.")
print("Ctrl-C to stop.\n")

seen = set()
while True:
    any_dev = False
    try:
        for d in usb.core.find(find_all=True):
            any_dev = True
            key = (d.idVendor, d.idProduct)
            try:    man = d.manufacturer
            except Exception: man = "?"
            try:    prod = d.product
            except Exception: prod = "?"
            line = "DEVICE  VID=%04X PID=%04X  %s / %s" % (d.idVendor, d.idProduct, man, prod)
            if key not in seen:
                seen.add(key)
                print(">>> NEW", line)
                if key == (0x1209, 0x4E4E):
                    print("    ^ that's the noknok LEDs module! USB host works.")
            else:
                print("    ", line)
    except Exception as e:
        print("  scan error:", e)   # e.g. RP2350 descriptor-read bug on composite devices
    if not any_dev:
        print("  (no device yet)")
    time.sleep(1.5)
