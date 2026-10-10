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

# Versions + layouts + local images, taken from each module repo's
# firmware/index.json (as of 8 Oct 2026). index.json is the source of truth for
# "what is current" (no git tags) - re-copy these when an index changes.
# `layout` (DEV-65): the flash layout the image is linked for. Conductor.update_all()
# flashes an I2C module ONLY if its bootloader reports the same layout, and
# refuses (never writes) otherwise - a wrong-layout image hangs the module until
# SWD. USB modules have no layout (no stage-0 port yet).
MANIFEST_FW = {
    "buzzer":     {"version": "3.5.0", "layout": 2, "url": "(local)"},
    "knob":       {"version": "2.3.0", "layout": 2, "url": "(local)"},
    "led_button": {"version": "2.4.1", "layout": 2, "url": "(local)"},
    "display":    {"version": "0.7.0", "layout": 2, "url": "(local)"},
    "usb_leds":   {"version": "1.8.2", "url": "(local)"},
}

IMAGES = {
    "buzzer":     "buzzer_firmware.bin",
    "knob":       "knob_firmware.bin",
    "led_button": "keyboard_firmware.bin",
    "display":    "display_firmware.bin",
    "usb_leds":   "noknok_leds.bin",
}

BUSES = (
    ("std",  board.GP8,  board.GP9),     # ecosystem standard I2C pins
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

    # read_layout=True asks each outdated I2C module for its bootloader layout, so
    # a layout mismatch is shown here (BLOCKED) before anything is written.
    print("\n  installed vs current (layout: installed/image):")
    report = c.firmware_report(MANIFEST_FW, read_layout=True, strict_layout=True)
    for r in report:
        print("    %-11s %-8s -> %-8s  L%s/L%s  %s"
              % (r["type"], r["installed"], r["required"],
                 r["layout_installed"], r["layout_image"], r["reason"]))

    todo = [r for r in report if r["needs_update"]]
    blocked = [r for r in report if r["blocked"]]
    if not todo and not blocked:
        print("  nothing to do on %s" % label)
    else:
        print("\n  updating %d module(s), %d blocked..." % (len(todo), len(blocked)))
        # update_all() enforces the layout gate itself; blocked modules are
        # returned with updated=False and the reason, and are never written to.
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
