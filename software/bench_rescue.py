# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_rescue.py — DEV-31 hardening D: a module parked in the bootloader is
# found, identified by chip UID, and given a good app — all by the Conductor,
# before normal enumeration.
#
# Set-up: the module must already be parked at 0x7E (run the bad-app test from
# module-I2C-bootloader, or interrupt an update). keyboard_firmware.bin on
# CIRCUITPY. noknok_state.json must remember this UID from an earlier
# enumeration — that is the whole point: the bootloader can't say what type it
# is, but we can.

from noknok import Conductor

c = Conductor()

def get_image(entry):
    print("   get_image() asked for: %s  (%s)" % (entry["type"], entry["reason"]))
    with open("keyboard_firmware.bin", "rb") as f:
        return f.read()

print("=== rescue_parked_module() ===")
r = c.rescue_parked_module(get_image)
print("   result:", r)
if not r:
    raise SystemExit("FAIL: nothing was parked - was the bad-app test run first?")
if r["action"] != "reflashed":
    raise SystemExit("FAIL: rescue did not reflash: %s" % r)

print("\n=== enumerate() afterwards ===")
c.enumerate()
found = [m for m in c.ledbutton if getattr(m, "_uid_hex", None) == r["uid"]]
if not found:
    raise SystemExit("FAIL: rescued module did not come back as an LED Button")
print("   rescued LED Button running at 0x%02X" % found[0].address)
print("\nALL PASS - parked module identified by UID and brought back by the Conductor")
