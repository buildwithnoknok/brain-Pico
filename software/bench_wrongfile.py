# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_wrongfile.py — DEV-31 hardening E: VERIFY_STAGE1 must refuse an image
# that is not a stage-1 for this layout, even though its CRC matches perfectly.
#
# We stage the LED Button APPLICATION and ask stage-1 to install it as a
# bootloader. The CRC will match (the host computed it over exactly what it
# sent), so before hardening E this would have been armed and installed — and
# the module would have booted an app at 0x0400 and died. Now stage-1 reads the
# header slot at +0x100, finds no "NKS1", and answers error 8.
#
# Ends by re-flashing the app so the module is left running.

from noknok import Conductor
from module_flasher import ModuleFlasher, FlashError, PAGE, crc32


print("=== drop the running LED Button into the bootloader ===")
c = Conductor(); c.enumerate()
if not c.ledbutton:
    raise SystemExit("FAIL: no LED Button")
addr = c.ledbutton[0].address
f = ModuleFlasher(c.i2c)
f.enter_bootloader(addr); f.wait_for_bootloader()
print("   at 0x7E, bootloader", f.get_version())

print("\n=== stage the APP image and call VERIFY_STAGE1 on it ===")
with open("keyboard_firmware.bin", "rb") as fh:
    app = fh.read()
bogus = app[:3000]           # fits the 3 KB stage-1 region; valid CRC; no header at +0x100
f.erase()
for off in range(0, len(bogus), PAGE):
    f.write_chunk(off, bogus[off:off + PAGE])
print("   %d bytes staged, CRC 0x%08X" % (len(bogus), crc32(bogus)))
try:
    f.verify_stage1(len(bogus), crc32(bogus))
    raise SystemExit("FAIL: VERIFY_STAGE1 ACCEPTED an application image as a stage-1")
except FlashError as e:
    print("   refused:", e)
    if "code 8" not in str(e):
        raise SystemExit("FAIL: refused, but not with error 8")

print("\n=== control block must NOT have been written ===")
# (checked over SWD by the caller; here we just confirm the module is still a
#  sane bootloader and put the app back)
f.erase()
for off in range(0, len(app), PAGE):
    f.write_chunk(off, app[off:off + PAGE])
f.verify(len(app), crc32(app)); f.boot()
c.enumerate()
print("   app restored, LED Button at 0x%02X" % c.ledbutton[0].address)
print("\nALL PASS - a non-stage-1 image with a valid CRC is refused (error 8)")
