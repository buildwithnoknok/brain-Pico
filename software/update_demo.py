# update_demo.py — demo/bench test of the unified Conductor OTA path.
#
# Exercises firmware_report() (now I2C + USB) and update_all() routing a
# needs_update entry to the right flasher (I2C ModuleFlasher / USB
# UsbModuleFlasher). Copy onto the Pico together with:
#   noknok.py  noknok_usb.py  module_flasher.py
#   noknok_leds.bin            (USB LEDs app image; for any I2C modules add their
#   keyboard_firmware.bin etc.  bins too and map them in IMAGES below)
#
# Run from the REPL:  import update_demo
#
# To make the demo actually flash something, MANIFEST below pins a required
# version just ABOVE what's installed so the DEV-3 policy reports
# "UPDATE AVAILABLE" (same major, installed < required, known protocol). The
# image we hand back is the local .bin — so the module is simply re-flashed with
# the proven app; the point is to prove the report + routing + flash + re-enumerate
# loop, not to ship a new version.

import time
from noknok import Conductor

# Local .bin per manifest module key (the get_image source on the bench).
IMAGES = {
    "usb_leds":   "noknok_leds.bin",
    # "buzzer":     "buzzer_firmware.bin",
    # "knob":       "knob_firmware.bin",
    # "led_button": "keyboard_firmware.bin",
}

# A minimal manifest module_firmware{} block. Versions are set high on purpose to
# force an update of whatever is connected (edit to match your modules).
MANIFEST_FW = {
    "usb_leds":   {"version": "1.9.0", "url": "(local)"},
    "buzzer":     {"version": "3.3.1", "url": "(local)"},
    "knob":       {"version": "2.1.0", "url": "(local)"},
    "led_button": {"version": "2.1.0", "url": "(local)"},
}


def get_image(entry):
    """Inject the firmware image for a report entry from a local file (bench).
    In provisioning this is where a WiFi download of entry['url'] would go."""
    fname = IMAGES.get(entry["type"])
    if not fname:
        raise ValueError("no local .bin mapped for type %r" % entry["type"])
    with open(fname, "rb") as fh:
        return fh.read()


def _progress(done, total):
    print("    %d / %d bytes (%d%%)" % (done, total, 100 * done // total))


def main():
    c = Conductor()
    print("Enumerating all buses...")
    c.enumerate_all()

    print("\nFirmware report (installed vs manifest):")
    report = c.log_firmware_report(MANIFEST_FW)
    todo = [r for r in report if r["needs_update"]]
    if not todo:
        print("\nNothing flagged for update — adjust MANIFEST_FW versions to force one.")
        return

    print("\n%d module(s) flagged. Running update_all()..." % len(todo))
    results = c.update_all(MANIFEST_FW, get_image, progress=_progress)

    print("\nResults:")
    for r in results:
        print("  %-11s %s  %s" % (
            r["type"],
            "UPDATED" if r["updated"] else "FAILED",
            r["error"] or ""))

    print("\nFinal report after re-enumeration:")
    c.log_firmware_report(MANIFEST_FW)
    print("Done.")


main()
