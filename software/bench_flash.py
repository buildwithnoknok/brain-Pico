# bench_flash.py — noknok one-at-a-time bench bring-up flasher (Pico / CircuitPython)
#
# Purpose: put APPLICATION firmware onto a CH32V003 module that currently has
# ONLY the bootloader on it (blank app). Used for factory bring-up of fresh
# boards. Run it from the Pico REPL (Thonny):
#
#       import bench_flash
#
# Why a human picks the type: a blank module has NO type identity. The bootloader
# is the SAME code on every buzzer / knob / LED button, so a module sitting in
# flash mode at 0x7E cannot tell you what it is — the type only exists once an app
# is flashed and reported during enumeration. And because every blank module
# answers at 0x7E, only ONE can be on the bus at a time (two = address collision).
# So bring-up is inherently: connect one module -> tell it the type -> flash ->
# it reboots and self-identifies from then on. Repeat for the next module.
#
# Files this script needs on the Pico (CIRCUITPY root):
#   noknok.py            (Conductor — provides the shared I2C bus)
#   module_flasher.py    (Sam's OTA flasher)
#   buzzer_firmware.bin      from module-I2C-buzzer/firmware/bin/
#   knob_firmware.bin        from module-I2C-knob/firmware/bin/
#   keyboard_firmware.bin    from module-I2C-ledbutton/firmware/bin/  (LED button)
#   display_firmware.bin     from module-I2C-1.42-display/firmware/bin/
#
# It also works on a module that is ALREADY running an app (it will send 0xB0 to
# flip it into the bootloader first), so you can re-flash a module too.

import os
import time

from noknok import Conductor
from module_flasher import ModuleFlasher, BL_ADDR, FlashError

# ── Module catalogue ──────────────────────────────────────────────────────────
# menu key -> (human label, .bin filename on the Pico, Conductor list attribute)
# The Conductor attribute lets us confirm the module came up as the right type
# after flashing (enumerate() fills c.buzzer / c.knob / c.ledbutton).
MODULES = {
    "1": ("buzzer",     "buzzer_firmware.bin",   "buzzer"),
    "2": ("knob",       "knob_firmware.bin",     "knob"),
    "3": ("led_button", "keyboard_firmware.bin", "ledbutton"),
    "4": ("display",    "display_firmware.bin",  "display"),
}

STATE_FILE = "noknok_state.json"   # enumerate()'s saved UID->address map


def _progress(done, total):
    print("    %3d%%   (%d / %d bytes)" % (100 * done // total, done, total))


def _load_image(filename):
    """Read a firmware .bin off the Pico filesystem. Returns bytes, or None."""
    try:
        with open(filename, "rb") as fh:
            return fh.read()
    except OSError:
        print("  !! Could not open '%s'." % filename)
        print("     Copy it to the CIRCUITPY root and try again.")
        return None


def _wipe_state():
    """Start each bench session from a clean enumeration state so the confirm
    step never trips over a stale saved address from an earlier module."""
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass   # not there — fine


def _flash_one(c, f):
    """Flash a single module. Returns True if a module was flashed (success or
    fail is printed), False if the user chose to quit at the menu."""

    # ── 1. Pick the module type ───────────────────────────────────────────────
    print("\nWhich module is connected?")
    for key, (label, _, _) in sorted(MODULES.items()):
        print("   %s = %s" % (key, label))
    print("   q = quit")
    choice = input("Choice: ").strip().lower()

    if choice == "q":
        return False
    if choice not in MODULES:
        print("  Unknown choice — try again.")
        return True

    label, binfile, list_attr = MODULES[choice]

    image = _load_image(binfile)
    if image is None:
        return True
    print("\nFlashing a %s with %s (%d bytes)." % (label, binfile, len(image)))

    # ── 2. Find the module: blank (at 0x7E) or running an app? ────────────────
    runtime_addr = None
    if f.present():
        # Bootloader is already waiting at 0x7E — this is the blank-board case.
        print("  Bootloader present at 0x%02X — flashing directly." % BL_ADDR)
    else:
        # No bootloader at 0x7E => a module is running an app. Enumerate to find
        # it, then we'll send 0xB0 to drop it into the bootloader.
        print("  No bootloader at 0x%02X — looking for a running module..." % BL_ADDR)
        c.enumerate()
        found = sorted(
            [m for lst in (c.buzzer, c.knob, c.ledbutton) for m in lst],
            key=lambda m: m.address,
        )
        if len(found) == 0:
            print("  Nothing on the bus. Is exactly one module connected & powered?")
            return True
        if len(found) > 1:
            print("  %d modules on the bus — connect only ONE at a time for bench"
                  " flashing." % len(found))
            return True
        runtime_addr = found[0].address
        print("  Found a running module at 0x%02X — will send it into the"
              " bootloader." % runtime_addr)

    # ── 3. Flash ──────────────────────────────────────────────────────────────
    try:
        f.flash(image, runtime_addr=runtime_addr, progress=_progress)
    except FlashError as e:
        print("  FLASH FAILED: %s" % e)
        print("  The module is safe — it stays in the bootloader at 0x%02X."
              " Re-seat it and try again." % BL_ADDR)
        return True
    print("  Flash OK — module booted into the %s application." % label)

    # ── 4. Confirm it came up as the expected type ────────────────────────────
    time.sleep(0.6)   # let the fresh app advertise at 0x7F
    _wipe_state()     # ignore any stale saved address before re-enumerating
    c.enumerate()
    # getattr with a default: a newly-added module type may not have a Conductor
    # list yet (the display does not, until noknok.py learns type 0x05). The
    # flash itself is already verified by CRC at this point — this step only
    # confirms the module re-enumerates as the expected type.
    came_up = getattr(c, list_attr, None) or []
    if came_up:
        m = came_up[-1]
        print("  CONFIRMED: %s live at 0x%02X  UID: %s"
              % (label, m.address, getattr(m, "_uid_hex", "?")))
    else:
        print("  Flash verified, but no %s enumerated — re-seat and re-check."
              % label)
    return True


def main():
    print("=" * 60)
    print(" noknok bench flasher — initial application flash")
    print("=" * 60)
    print("Connect ONE module at a time (blank or running). Pick its type,")
    print("it gets flashed over I2C, then swap in the next module.")

    _wipe_state()
    c = Conductor()
    f = ModuleFlasher(c.i2c)

    flashed = 0
    while True:
        keep_going = _flash_one(c, f)
        if not keep_going:
            break
        # _flash_one prints its own result; count only is approximate, but
        # the per-module confirmation above is the real record.
        input("\nSwap in the next module and press Enter (or type q at the menu"
              " to stop)... ")
        flashed += 1

    print("\nDone. Bench session finished.")


main()
