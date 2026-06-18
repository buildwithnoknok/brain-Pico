# usb_host_stress_test.py  -  drive MULTIPLE noknok LEDs modules through a hub
#
# Stress test for the "many USB modules on one Pico via a hub" topology (the
# DataHub model). Finds EVERY noknok LEDs module on the USB host bus, probes each
# (serial / identity / GET_VERSION), then lights each a DIFFERENT colour at the
# same time so you can confirm they are independently addressable.
#
# Wiring: same powered-hub rig as usb_host_test.py
#   hub upstream D+ -> GP16, D- -> GP17, GND -> Pico GND; hub EXTERNALLY POWERED;
#   each LED module in its own downstream port.
#
# POWER NOTE: 4 modules can pull ~1-2 A total. Use the powered hub (the known-good
# one) with a capable adapter. This test uses 1-2 colour channels per module (not
# full white) to keep current sane.
#
# Run from the REPL: import usb_host_stress_test   (re-run: usb_host_stress_test.run())

import board
import time
import usb_host
import usb.core

DP = board.GP16
DM = board.GP17

LEDS_VID = 0x1209
LEDS_PID = 0x4E4E
EP_OUT   = 0x02
EP_IN    = 0x83

EXPECTED = 4   # how many modules you plugged in (just controls the find retry)

# Distinct colours per module (<= 2 channels each to limit current).
COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 0, 255),
          (0, 255, 255), (255, 255, 0)]
NAMES  = ["red", "green", "blue", "magenta", "cyan", "yellow"]

_port = None


def _ensure_port():
    global _port
    if _port is None:
        _port = usb_host.Port(DP, DM)
        print("  usb_host.Port OK on GP16 (D+) / GP17 (D-)")
    return _port


def _is_leds(d):
    try:
        return d.idVendor == LEDS_VID and d.idProduct == LEDS_PID
    except Exception:
        return False


def run():
    print("=" * 54)
    print("noknok USB host STRESS test - multiple LED modules via hub")
    print("=" * 54)
    try:
        _ensure_port()
    except Exception as e:
        print("  host port failed:", e)
        return

    # Collect ALL matching modules. Retry while they enumerate (hubs are slow).
    mods = []
    for attempt in range(20):
        mods = [d for d in usb.core.find(find_all=True) if _is_leds(d)]
        print("attempt %2d: %d LED module(s) found" % (attempt, len(mods)))
        if len(mods) >= EXPECTED:
            break
        time.sleep(0.5)

    n = len(mods)
    if n == 0:
        print("\nNo modules found. Check the hub is powered and the upstream wiring.")
        return
    print("\nFound %d module(s) (expected %d). Probing each..." % (n, EXPECTED))

    # Configure + probe each module.
    good = []
    for i, d in enumerate(mods):
        try:
            d.set_configuration()
        except Exception:
            pass
        try:
            sn = d.serial_number
        except Exception as e:
            sn = "<read failed: %s>" % e
        try:
            d.write(EP_OUT, bytes((0xF0,)), timeout=1000)
            b = bytearray(3); k = d.read(EP_IN, b, timeout=500)
            ident = [hex(x) for x in b[:k]]
        except Exception as e:
            ident = "ERR %s" % e
        try:
            d.write(EP_OUT, bytes((0xB1,)), timeout=1000)
            b = bytearray(4); k = d.read(EP_IN, b, timeout=500)
            ver = list(b[:k])
        except Exception as e:
            ver = "ERR %s" % e
        print("  module[%d]  serial=%s  identity=%s  version=%s" % (i, sn, ident, ver))
        good.append(d)

    # Light each module a distinct colour, all at once.
    print("\nLighting each module its own colour simultaneously:")
    for i, d in enumerate(good):
        r, g, b = COLORS[i % len(COLORS)]
        try:
            d.write(EP_OUT, bytes((0x01, r, g, b)), timeout=1000)
            print("  module[%d] -> %s" % (i, NAMES[i % len(NAMES)]))
        except Exception as e:
            print("  module[%d] write FAILED: %s" % (i, e))
    time.sleep(3)

    # All off.
    for d in good:
        try:
            d.write(EP_OUT, bytes((0x00,)), timeout=1000)
        except Exception:
            pass

    print("\nDONE. Drove %d/%d module(s)." % (len(good), EXPECTED))
    print("If each module showed its OWN colour, multi-module hosting via the hub works.")
    print("If serials are all blank/identical, USB modules need a unique ID (note for Sam).")


run()
