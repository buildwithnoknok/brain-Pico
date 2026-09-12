# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_stage1_rollout.py — install a new stage-1 on EVERY enumerated I2C module
# through the Conductor (the real product path), restore each module's app, and
# read the bootloader version back as five bytes to prove the layout byte.
#
# Generalised from Sam's bench_stage1_buzzer.py for the stage-1 1.2.0 rollout
# (12 Sep 2026). Bench-only; not part of the regression. This is the "M" tier
# for a stage-1 change: one real self-update per module, no SWD.
#
# Files on CIRCUITPY: noknok_stage1_new.bin      (the stage-1 to install)
#                     buzzer_firmware.bin        (buzzer app, layout 2)
#                     keyboard_firmware.bin      (LED Button app, layout 2)
#                     knob_firmware.bin          (knob app, layout 2 — if present)
from noknok import Conductor
from module_flasher import ModuleFlasher, BL_ADDR

STAGE1_NEW = "noknok_stage1_new.bin"
EXPECT     = (1, 2)          # (major, minor) the new stage-1 must report
EXPECT_LAY = 2               # layout byte it must report

APPS = {                     # Conductor list attr -> (manifest type, app .bin)
    "buzzer":    ("buzzer",     "buzzer_firmware.bin"),
    "ledbutton": ("led_button", "keyboard_firmware.bin"),
    "knob":      ("knob",       "knob_firmware.bin"),
}


def read_five(c):
    """0xB1 at 0x7E read as five bytes: proto, major, minor, patch, layout."""
    f = ModuleFlasher(c.i2c)
    f._write(BL_ADDR, [0xB1])
    buf = bytearray(5)
    while not c.i2c.try_lock():
        pass
    try:
        c.i2c.readfrom_into(BL_ADDR, buf)
    finally:
        c.i2c.unlock()
    return tuple(buf)


def entry_for(c, attr):
    c.enumerate()
    lst = getattr(c, attr, [])
    if not lst:
        return None
    m = lst[0]
    return {"type": APPS[attr][0], "bus": "i2c", "address": m.address,
            "uid": getattr(m, "_uid_hex", None)}


c = Conductor()
with open(STAGE1_NEW, "rb") as f:
    s1 = f.read()

results = []
for attr, (mtype, app_bin) in APPS.items():
    entry = entry_for(c, attr)
    if entry is None:
        print("-- no %s on the bus, skipping" % mtype)
        continue
    try:
        with open(app_bin, "rb") as f:
            app = f.read()
    except OSError:
        print("-- %s: %s not on CIRCUITPY, skipping (module left as is)" % (mtype, app_bin))
        continue

    print("== %s at 0x%02X  uid %s" % (mtype, entry["address"], entry["uid"]))
    print("   bootloader before:", c.bootloader_version(entry))
    entry = entry_for(c, attr)
    print("   stage1_update:", c.stage1_update(entry, s1, app))

    entry = entry_for(c, attr)
    if entry is None:
        print("   FAIL: %s did not come back after the update" % mtype)
        results.append((mtype, False))
        continue
    fl = ModuleFlasher(c.i2c)
    fl.enter_bootloader(entry["address"])
    fl.wait_for_bootloader()
    v = read_five(c)
    fl.boot()
    ok = (v[1], v[2]) == EXPECT and v[4] == EXPECT_LAY
    print("   0xB1 at 0x7E (5 bytes): %s -> stage-1 %d.%d.%d layout %d  %s"
          % (v, v[1], v[2], v[3], v[4], "OK" if ok else "WRONG"))
    results.append((mtype, ok))

c.enumerate()
print()
for mtype, ok in results:
    print("%-11s %s" % (mtype, "PASS" if ok else "FAIL"))
print("ALL PASS" if results and all(ok for _, ok in results) else "FAIL")
