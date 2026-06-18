# usb_host_test.py  -  Does CircuitPython USB host work on this Pico 2 W?
#
# HARDWARE-PROVEN 2026-06-18: a Pico 2 W (RP2350, CircuitPython) successfully
# hosts the noknok LEDs module over PIO-USB - enumerates it (incl. string
# descriptors, so the composite-device bug CP #10760 does NOT affect our module),
# configures it, sends LED commands, and reads replies. This script reproduces
# that full end-to-end check.
#
# CircuitPython hosts USB via usb_host.Port on a CONSECUTIVE GPIO PAIR (PIO-USB) -
# NOT the Pico's native USB connector. Wire the device's USB lines to these GPIOs
# (a USB-A breakout, or the powered hub's data lines):
#
#     device D+  -> GP16   (DP, must be the lower-numbered pin)
#     device D-  -> GP17   (DM, DP+1)
#     device 5V  -> VBUS   (so the device gets 5V; use a powered source for many LEDs)
#     device GND -> Pico GND   (common ground - REQUIRED)
#
# The Pico's native USB port stays free for the REPL/CIRCUITPY (device role) while
# it hosts on GP16/17 - both roles run at once. Put this on CIRCUITPY and either
# run as code.py or, from the REPL: `import usb_host_test`  (re-run: usb_host_test.run()).
#
# Wiring reference: https://learn.adafruit.com/using-a-keyboard-with-usb-host

import time
import board
import usb_host
import usb.core

DP = board.GP16   # USB D+   <- change to match your wiring (must be lower pin)
DM = board.GP17   # USB D-

LEDS_VID = 0x1209
LEDS_PID = 0x4E4E
EP_OUT   = 0x02   # commands  (host -> module)
EP_IN    = 0x83   # responses (module -> host)

_port = None      # module-global so the host port persists between run() calls


def _ensure_port():
    global _port
    if _port is None:
        _port = usb_host.Port(DP, DM)
        print("  usb_host.Port OK on", DP, "/", DM)
    return _port


def run():
    print("Bringing up USB host port on GP16 (D+) / GP17 (D-)...")
    try:
        _ensure_port()
    except Exception as e:
        print("  usb_host.Port FAILED:", e)
        return

    # Find the LED module (retry - enumeration takes a moment after attach).
    leds = None
    for attempt in range(15):
        for d in usb.core.find(find_all=True):
            try:
                vid, pid = d.idVendor, d.idProduct
            except Exception as e:
                print("  device present but VID/PID read failed:", e)
                continue
            print("  DEVICE VID=%04X PID=%04X" % (vid, pid))
            if (vid, pid) == (LEDS_VID, LEDS_PID):
                leds = d
        if leds:
            break
        time.sleep(0.5)

    if leds is None:
        print("\nNO noknok LEDs module found.")
        print("If NO device was listed at all, D+/D- are most likely swapped -")
        print("swap the data wires on GP16/GP17 (D+ must be GP16) and re-run.")
        print("Also check common GND and 5V on VBUS.")
        return

    print("\nFound the noknok LEDs module - running the full check.")
    try:
        leds.set_configuration()
    except Exception:
        pass  # often already configured by the host stack

    def send(data):
        leds.write(EP_OUT, bytes(data), timeout=1000)

    # Identity (0xF0 -> 0x4E 0x4E 0x04) and GET_VERSION (0xB1 -> proto,maj,min,patch).
    # NOTE: CircuitPython usb.core.read() reads INTO a buffer and returns the count.
    try:
        send((0xF0,))
        buf = bytearray(3)
        n = leds.read(EP_IN, buf, timeout=500)
        print("  identity:", [hex(b) for b in buf[:n]], "(expect 0x4e 0x4e 0x4)")
    except Exception as e:
        print("  identity query failed:", e)

    try:
        send((0xB1,))
        vbuf = bytearray(4)
        n = leds.read(EP_IN, vbuf, timeout=500)
        print("  version :", list(vbuf[:n]), "(protocol, major, minor, patch)")
    except Exception as e:
        print("  version query failed:", e)

    # Visible proof.
    print("  cycling colours: red -> green -> blue -> off")
    for r, g, b in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
        try:
            send((0x01, r, g, b))
        except Exception as e:
            print("    write failed:", e)
        time.sleep(1.0)
    try:
        send((0x00,))
    except Exception:
        pass

    print("\nDONE - if you saw red/green/blue, CircuitPython PIO-USB hosting works.")


run()
