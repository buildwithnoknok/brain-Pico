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
# flip it into the bootloader first), so you can re-flash a module too. In that
# case the module's type IS known from enumeration, so the script matches the
# flash target BY TYPE and will not touch a module of a different type.
#
# RECOVERY: if a module is running the WRONG firmware (e.g. a buzzer that got a
# display image), it will never enumerate as its real type again, so the type
# match can never succeed. For that case the script offers an explicit force
# path — it shows you what the module currently reports as, and makes you type
# the word FORCE. That is the supported way back; do not hand-drive
# ModuleFlasher for it.

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

# Every module type the Conductor can enumerate, as (label, Conductor attribute).
#
# ANY NEW MODULE TYPE MUST BE ADDED HERE, and kept in sync with noknok.py.
# A missing type is NOT a harmless omission. On 23 Jul 2026 "display" was absent:
# a display module sitting on the bus was therefore invisible to this script, the
# only module it COULD see was a buzzer that was only present to lend its I2C
# pull-ups, that buzzer looked like "the one module connected", and display
# firmware was flashed straight into it. The missing entry did not just hide the
# display — it made the "only one module on the bus" safety check pass falsely.
KNOWN_TYPE_ATTRS = (
    ("buzzer",     "buzzer"),
    ("knob",       "knob"),
    ("led_button", "ledbutton"),
    ("display",    "display"),
)


def _offer_force(inventory, label):
    """Deliberate escape hatch for a module running the WRONG firmware.

    This exists because refusing to flash, on its own, leaves you stuck: if a
    buzzer accidentally receives a display image it will never again enumerate
    as a buzzer, so the type check can never match and the normal path can never
    recover it. (Exactly what happened on 23 Jul 2026.)

    It overwrites a module that is working, just working as the wrong thing, so
    it deliberately does NOT accept a casual y/n. You must name the address and
    then type the word FORCE, having been shown what that module currently is.

    Returns the runtime address to flash, or None to abort.
    """
    print("\n  RECOVERY OPTION")
    print("  If one of the modules above is running the WRONG firmware, you can")
    print("  force %s firmware onto it. This OVERWRITES it." % label)
    print("  Enter its address in hex (e.g. 08), or press Enter to abort.")

    ans = input("  Force-flash address: ").strip().lower()
    if not ans:
        print("  Aborted — nothing written.")
        return None

    try:
        addr = int(ans, 16)
    except ValueError:
        print("  '%s' is not a hex address — aborted." % ans)
        return None

    match = [(lbl, m) for lbl, m in inventory if m.address == addr]
    if not match:
        print("  0x%02X is not on the bus — aborted." % addr)
        return None

    cur_label, m = match[0]
    print("\n  0x%02X currently reports as: %s" % (addr, cur_label))
    print("  UID: %s" % getattr(m, "_uid_hex", "?"))
    print("  You are about to OVERWRITE it with %s firmware." % label)
    if input("  Type FORCE (capitals) to confirm: ").strip() != "FORCE":
        print("  Aborted — nothing written.")
        return None

    print("  Forcing: %s at 0x%02X will be re-flashed as %s."
          % (cur_label, addr, label))
    return addr


def _bus_inventory(c):
    """Every module the Conductor can currently see, as [(label, module)],
    sorted by address. getattr with a default so an older noknok.py that lacks
    a type list degrades to 'not found' rather than raising."""
    out = []
    for lbl, attr in KNOWN_TYPE_ATTRS:
        for m in (getattr(c, attr, None) or []):
            out.append((lbl, m))
    out.sort(key=lambda pair: pair[1].address)
    return out


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

    # ── 2. Survey the whole bus, THEN pick the flash target ───────────────────
    # Always take a full inventory first and print it. Flashing the wrong module
    # destroys a working one, so it is worth a couple of seconds to show exactly
    # what is connected before anything is written.
    bootloader_waiting = f.present()
    c.enumerate()
    inventory = _bus_inventory(c)

    print("\n  Bus inventory:")
    for lbl, m in inventory:
        print("     0x%02X   %-11s UID %s"
              % (m.address, lbl, getattr(m, "_uid_hex", "?")))
    if bootloader_waiting:
        print("     0x%02X   <blank / sitting in the bootloader>" % BL_ADDR)
    if not inventory and not bootloader_waiting:
        print("     (nothing found)")

    runtime_addr = None

    if bootloader_waiting:
        # Blank-board case. A module in the bootloader has NO type identity —
        # the bootloader is byte-identical on every CH32V003 module — so we
        # cannot verify this really is a `label`. That check is on you.
        print("\n  Flash target: the blank module at 0x%02X." % BL_ADDR)
        if inventory:
            print("  The %d module(s) listed above are NOT touched."
                  % len(inventory))
        print("  A blank module cannot report its type — make sure it really"
              " is a %s." % label)
    else:
        # A module is running an app. Now we CAN check the type, because
        # enumeration told us what everything is.
        if not inventory:
            print("\n  Nothing on the bus. Is the module connected and powered?")
            return True

        same_type = [m for lbl, m in inventory if lbl == label]

        if len(same_type) == 0:
            print("\n  NOT FLASHING BY DEFAULT: no %s found on the bus." % label)
            print("  Writing %s firmware into any module above would overwrite a"
                  " working one." % label)
            print("  If the %s IS connected but missing from the list, this script"
                  " may not know" % label)
            print("  its type yet (see KNOWN_TYPE_ATTRS) — fix that rather than"
                  " forcing.")
            runtime_addr = _offer_force(inventory, label)
            if runtime_addr is None:
                return True

        elif len(same_type) > 1:
            print("\n  %d %s modules on the bus — connect only one of a given"
                  " type at a time." % (len(same_type), label))
            return True

        else:
            # Exactly one module of the chosen type — the normal, safe path.
            runtime_addr = same_type[0].address
            print("\n  Flash target: %s at 0x%02X — sending it into the"
                  " bootloader." % (label, runtime_addr))
            if len(inventory) > 1:
                print("  The other %d module(s) above are left alone. (A buzzer"
                      " or similar sitting" % (len(inventory) - 1))
                print("  on the chain purely to provide I2C pull-ups is fine —"
                      " the target is matched")
                print("  by type, not by being the only thing connected.)")

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
