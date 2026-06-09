# flash_ledbutton.py — end-to-end I2C OTA test for the noknok LED Button
#
# Copy onto the Pico together with:
#   noknok.py
#   module_flasher.py
#   keyboard_firmware.bin   (offset-linked app image from module-I2C-ledbutton)
#
# Run from the REPL:  import flash_ledbutton
#
# Two scenarios, auto-detected:
#   A) Blank board  — bootloader already waiting at 0x7E (no valid app yet).
#                     We flash, boot, then enumerate to prove the app runs.
#   B) Running app  — module enumerated normally; we send 0xB0 to flip it into
#                     the bootloader, re-flash, boot, and re-enumerate.

import time
from noknok import Conductor
from module_flasher import ModuleFlasher, BL_ADDR, FlashError

BIN_FILE = "keyboard_firmware.bin"


def _progress(done, total):
    print("  flashing %d / %d bytes (%d%%)" % (done, total, 100 * done // total))


def main():
    with open(BIN_FILE, "rb") as fh:
        image = fh.read()
    print("Loaded %s: %d bytes" % (BIN_FILE, len(image)))

    c = Conductor()
    f = ModuleFlasher(c.i2c)

    runtime_addr = None
    if f.present():
        print("Bootloader already at 0x%02X (blank/invalid app) — flashing directly." % BL_ADDR)
    else:
        print("No bootloader at 0x%02X — enumerating to find the running LED Button..." % BL_ADDR)
        c.enumerate()
        if not c.ledbutton:
            print("No LED Button found. Is it connected and running v2.0+?")
            return
        runtime_addr = c.ledbutton[0].address
        print("Found LED Button at 0x%02X. Sending it into the bootloader." % runtime_addr)

    # ── Flash ────────────────────────────────────────────────────────────────
    print("Starting OTA flash...")
    try:
        f.flash(image, runtime_addr=runtime_addr, progress=_progress)
    except FlashError as e:
        print("FLASH FAILED:", e)
        return
    print("Flash OK — booted into the application.")

    # ── Prove the app runs ───────────────────────────────────────────────────
    time.sleep(0.5)            # let the app enumerate (backoff 0.3-2.8 s)
    print("Re-enumerating...")
    c.enumerate()
    if not c.ledbutton:
        print("App flashed but did not enumerate — check the module.")
        return

    k = c.ledbutton[0]
    print("LED Button live at 0x%02X. Running a quick self-test." % k.address)
    for name, rgb in (("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255))):
        print("  LED ->", name)
        k.set_color(*rgb)
        time.sleep(0.4)
    k.led_off()
    print("Press the button within 5 s...")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        s = k.read()
        if s and s.press_event:
            print("  button press detected — END TO END OK.")
            k.set_color(0, 255, 0)
            time.sleep(0.5)
            k.led_off()
            break
        time.sleep(0.05)
    else:
        print("  (no press seen — LED test already confirms the app is alive.)")

    print("Done.")


main()
