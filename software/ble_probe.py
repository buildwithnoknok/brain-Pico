# SPDX-License-Identifier: MIT
# ble_probe.py - is native BLE usable on this CircuitPython board?  (DEV-33)
#
# Walks the exact stack the Device Protocol v1 spec assumes and reports where
# it stops:
#   1. the firmware's _bleio module and its adapter (the radio handle),
#   2. the ways an adapter could be created,
#   3. the adafruit_ble library path (BLERadio -> UARTService -> advertise),
#      if the library is present in /lib,
#   4. a plain wifi check, to show the CYW43 itself is alive.
#
# Run:  ./pico.py run ble_probe.py     (Pi4 bench)   or paste into the REPL.
# Nothing here writes to the filesystem or changes any setting.

import sys
import os

PASS, FAIL, SKIP = "PASS", "FAIL", "skip"
results = []


def report(step, status, detail=""):
    results.append((step, status))
    print("[%s] %s%s" % (status, step, (" - " + detail) if detail else ""))


print("=" * 64)
print("BLE probe - CircuitPython %s on %s" % (
    ".".join(str(v) for v in sys.implementation.version[:3]), os.uname().machine))
print("=" * 64)

# ── 1. _bleio module + adapter ─────────────────────────────────────────────
adapter = None
have_bleio = False
try:
    import _bleio
    have_bleio = True
    report("1a import _bleio", PASS, "names: " + ", ".join(sorted(n for n in dir(_bleio) if not n.startswith("__"))))
    adapter = _bleio.adapter
    if adapter is None:
        report("1b _bleio.adapter", FAIL,
               "is None - the firmware has no radio bound to _bleio (HCI variant, needs an external chip)")
    else:
        report("1b _bleio.adapter", PASS, repr(adapter))
except ImportError as e:
    report("1a import _bleio", FAIL, repr(e))

# ── 2. Can an adapter be created without external hardware? ───────────────
if adapter is None and have_bleio:
    try:
        _bleio.Adapter()
        report("2a _bleio.Adapter() no args", PASS, "constructed")
    except Exception as e:
        report("2a _bleio.Adapter() no args", FAIL, "%s: %s" % (type(e).__name__, e))
    # The HCI constructor wants uart/rts/cts. Show what it asks for when given nothing useful.
    try:
        _bleio.Adapter(uart=None, rts=None, cts=None)
        report("2b _bleio.Adapter(uart=None,...)", PASS, "constructed (unexpected)")
    except Exception as e:
        report("2b _bleio.Adapter(uart=None,...)", FAIL, "%s: %s" % (type(e).__name__, e))
else:
    report("2  adapter construction", SKIP, "adapter already present")

# ── 3. adafruit_ble library path (what the spec calls 'free') ──────────────
try:
    from adafruit_ble import BLERadio
    from adafruit_ble.advertising.standard import ProvideServicesAdvertisement
    from adafruit_ble.services.nordic import UARTService
    report("3a import adafruit_ble", PASS)
    try:
        ble = BLERadio()                     # internally: self._adapter = _bleio.adapter
        report("3b BLERadio()", PASS, "adapter=%r" % (ble._adapter,))
        uart = UARTService()
        report("3c UARTService()", PASS)
        adv = ProvideServicesAdvertisement(uart)
        ble.name = "noknok-probe"
        ble.start_advertising(adv)
        report("3d start_advertising", PASS, "ADVERTISING as 'noknok-probe' - check nRF Connect")
        ble.stop_advertising()
    except Exception as e:
        report("3  adafruit_ble path", FAIL, "%s: %s" % (type(e).__name__, e))
except ImportError as e:
    report("3  adafruit_ble library", SKIP, "not in /lib (%s) - irrelevant while 1b fails: "
           "BLERadio() just reads _bleio.adapter" % e)

# ── 4. Is the CYW43 alive at all? (WiFi side) ─────────────────────────────
try:
    import wifi
    mac = ":".join("%02X" % b for b in wifi.radio.mac_address)
    report("4  wifi.radio (CYW43 WiFi side)", PASS, "MAC " + mac)
except Exception as e:
    report("4  wifi.radio", FAIL, repr(e))

# ── Verdict ────────────────────────────────────────────────────────────────
print("-" * 64)
if adapter is not None:
    print("VERDICT: native BLE adapter PRESENT - the spec's premise holds.")
else:
    print("VERDICT: NO native BLE on this build. _bleio is compiled in but has no")
    print("         radio behind it; adafruit_ble/UARTService cannot work without")
    print("         an external HCI BLE chip on a UART. WiFi side of the CYW43 is fine.")
print("=" * 64)
