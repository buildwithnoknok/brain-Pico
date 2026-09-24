# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_update_modules.py - bring every module on the bench up to the version
# its repo's firmware/index.json declares, over the REAL OTA path
# (Conductor.firmware_report -> update_all -> ModuleFlasher), so the update
# mechanism is exercised at the same time as the modules are updated.
#
# Runs ON the Pico via `pico.py run`. Needs on the Pico's filesystem:
#   module_flasher.py  noknok.py  noknok_usb.py
#   buzzer_firmware.bin  knob_firmware.bin  keyboard_firmware.bin
#   noknok_leds.bin  display_firmware.bin      (only those you intend to flash)
#
# Walks BOTH I2C buses, because the Conductor is single-bus and the shipping
# layout puts modules on two of them.

import time

import board
from noknok import Conductor

# Versions + local images, taken from each module repo's firmware/index.json.
# index.json is the source of truth for "what is current" (no git tags).
MANIFEST_FW = {
    "buzzer":     {"version": "3.5.0", "url": "(local)"},
    "knob":       {"version": "2.3.0", "url": "(local)"},
    "led_button": {"version": "2.4.1", "url": "(local)"},
    "display":    {"version": "0.5.0", "url": "(local)"},
    "usb_leds":   {"version": "1.8.1", "url": "(local)"},
}

IMAGES = {
    "buzzer":     "buzzer_firmware.bin",
    "knob":       "knob_firmware.bin",
    "led_button": "keyboard_firmware.bin",
    "display":    "display_firmware.bin",
    "usb_leds":   "noknok_leds.bin",
}

BUSES = (
    ("i2c0", board.GP20, board.GP21),
    ("i2c1", board.GP18, board.GP19),
)


def get_image(entry):
    name = IMAGES.get(entry["type"])
    if not name:
        raise ValueError("no local image mapped for %r" % entry["type"])
    with open(name, "rb") as fh:
        return fh.read()


def progress(done, total):
    if total and (done == total or done % 1024 == 0):
        print("      %d / %d bytes (%d%%)" % (done, total, 100 * done // total))


def run_bus(label, sda, scl, do_usb):
    print("\n================ %s ================" % label)
    c = Conductor(sda=sda, scl=scl)
    c.enumerate(total_timeout_sec=8)
    if do_usb:
        try:
            c.enumerate_usb()
        except Exception as e:
            print("  (usb enumeration skipped: %s)" % e)

    print("\n  installed vs current:")
    report = c.firmware_report(MANIFEST_FW)
    for r in report:
        print("    %-11s %-8s -> %-8s  %s"
              % (r["type"], r["installed"], r["required"], r["reason"]))

    todo = [r for r in report if r["needs_update"]]
    if not todo:
        print("  nothing to do on %s" % label)
    else:
        print("\n  updating %d module(s)..." % len(todo))
        results = c.update_all(MANIFEST_FW, get_image, progress=progress)
        for r in results:
            print("    %-11s updated=%s  %s"
                  % (r["type"], r["updated"], r["error"] or ""))

    # Read the versions back from the hardware - the only proof that counts.
    time.sleep(0.3)
    c.enumerate(total_timeout_sec=8)
    print("\n  AFTER:")
    for kind in ("buzzer", "knob", "ledbutton", "display"):
        for m in getattr(c, kind, []):
            print("    %-11s 0x%02X  fw %s  uid %s"
                  % (kind, m.address, getattr(m, "firmware_version", None),
                     getattr(m, "_uid_hex", None)))
    if do_usb:
        for m in getattr(c, "leds", []):
            print("    %-11s fw %s" % ("usb_leds", getattr(m, "firmware_version", None)))
    try:
        c.i2c.deinit()
    except Exception:
        pass


for (label, sda, scl) in BUSES:
    try:
        run_bus(label, sda, scl, do_usb=(label == "i2c1"))
    except Exception as e:
        print("  %s FAILED: %s: %s" % (label, type(e).__name__, e))

print("\n== done ==")
