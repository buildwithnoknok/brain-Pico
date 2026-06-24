# flash_leds_usb.py — end-to-end USB OTA test for the noknok LEDs module.
#
# The USB counterpart of flash_ledbutton.py. Copy onto the Pico together with:
#   noknok_usb.py
#   noknok_leds.bin     (offset-linked app image from module-usb-led/firmware/bin,
#                        i.e. the v1.8.0+ bootloader-hosted build linked at 0x2000)
#
# Run from the REPL:  import flash_leds_usb
#
# Hardware: Pico 2 W with PIO-USB host on GP16 (D+) / GP17 (D-), the noknok LEDs
# module behind a POWERED hub, with the noknok USB bootloader already flashed once
# (via BOOT0 jumper / WCHISPTool).
#
# Two scenarios, auto-detected:
#   A) Bootloader present (PID 4E42) — blank/invalid app. Flash, boot, confirm.
#   B) Running app (PID 4E4E)        — send 0xB0 to flip it into the bootloader,
#                                      re-flash, boot, re-enumerate.
#
# THE POINT OF THIS TEST is scenario B: it rides the full PID-change loop
#   app 4E4E --0xB0--> BL 4E42 --flash--> BOOT --> app 4E4E
# which forces the PIO-USB host to RE-ENUMERATE the device three times. That
# re-enumeration is the known risk (CP PIO-USB hot re-attach); this script prints
# at each transition so we can see exactly where it succeeds or stalls.

import time
from noknok_usb import UsbModuleFlasher, NoknokLEDs, UsbFlashError

BIN_FILE = "noknok_leds.bin"


def _progress(done, total):
    print("  flashing %d / %d bytes (%d%%)" % (done, total, 100 * done // total))


def main():
    with open(BIN_FILE, "rb") as fh:
        image = fh.read()
    print("Loaded %s: %d bytes" % (BIN_FILE, len(image)))

    f = UsbModuleFlasher()      # brings up the GP16/GP17 PIO-USB host port

    # Let the bus settle so the module enumerates before we look for it.
    print("Bringing up USB host port; waiting for the module to enumerate...")
    time.sleep(3)

    if f.present():
        print("Bootloader present (PID 4E42) — blank/invalid app. Flashing directly.")
    else:
        bl = f._find(f.PID_APP)
        if bl is None:
            print("No noknok USB module found (neither app 4E4E nor bootloader 4E42).")
            print("Check: powered hub, GP16/GP17 wiring, bootloader flashed, data cable.")
            return
        try:
            serial = (bl.serial_number or "?").lower()
        except Exception:
            serial = "?"
        print("Running app found (PID 4E4E, serial %s)." % serial)
        print("flash() will send 0xB0 and wait for it to re-attach as the bootloader.")

    # ── Flash (orchestrates 0xB0 -> re-find 4E42 -> erase/write/verify -> boot) ─
    print("Starting USB OTA flash...")
    try:
        app_dev = f.flash(image, progress=_progress, confirm_app=True)
    except UsbFlashError as e:
        print("FLASH FAILED:", e)
        return
    print("Flash OK — BOOT sent.")

    # ── Prove the app re-enumerated and runs ──────────────────────────────────
    if app_dev is None:
        print("App did NOT re-enumerate as PID 4E4E within the timeout.")
        print(">> This is the PIO-USB re-attach risk. Note whether a power-cycle "
              "of the hub brings it back as 4E4E (= host stale, not a flash fail).")
        return

    print("App re-enumerated (PID 4E4E). Running a quick LED self-test.")
    leds = NoknokLEDs(app_dev)
    v = leds.version()
    if v:
        print("  GET_VERSION: proto %d, fw %d.%d.%d" % v)
    for name, rgb in (("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255))):
        print("  LEDs ->", name)
        leds.set_all(*rgb)
        time.sleep(0.5)
    leds.off()
    print("Done — END TO END OK.")


main()
