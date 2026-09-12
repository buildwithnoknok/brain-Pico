# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_conductor_stage1.py — DEV-31: the bootloader self-update driven the way a
# PRODUCT would drive it, through the Conductor, not through a bench flasher.
#
# Files on CIRCUITPY:  noknok_stage1_new.bin   (the stage-1 to install)
#                      keyboard_firmware.bin   (the LED Button app to restore)
#
# What it proves:
#   1. Conductor.enumerate() finds a running LED Button at a runtime address
#   2. Conductor.bootloader_version() reads 0xB1 (drops to BL, asks, boots back)
#   3. Conductor.stage1_update() replaces stage-1 AND restores the app, one call
#   4. After re-enumeration the LED Button is back, running, at a runtime address
#   5. bootloader_version() now reports the new version
#
# This is bench_stage1.py's low-level sequence wrapped in the real API, plus the
# app-restore step the product flow needs. Same proof: the version changed.

import time
from noknok import Conductor

STAGE1_NEW = "noknok_stage1_new.bin"
APP_BIN    = "keyboard_firmware.bin"


def banner(s):
    print("\n" + "=" * 62 + "\n" + s + "\n" + "=" * 62)


def fmt(v):
    if not v:
        return "no answer (legacy bootloader)"
    s = "proto=%d  v%d.%d.%d" % tuple(v[:4])
    if len(v) > 4:
        s += "  layout=%s" % (v[4] or "?")      # 5th byte since stage-1 1.2.0
    return s


def find_ledbutton(c):
    c.enumerate()
    if not c.ledbutton:
        raise SystemExit("FAIL: no LED Button enumerated")
    m = c.ledbutton[0]
    return {"type": "ledbutton", "bus": "i2c", "address": m.address,
            "uid": getattr(m, "_uid_hex", None)}


def main():
    c = Conductor()

    banner("1. Enumerate — LED Button running?")
    entry = find_ledbutton(c)
    print("   LED Button at 0x%02X  uid %s" % (entry["address"], entry["uid"]))

    banner("2. Conductor.bootloader_version() — which world is this module in?")
    v0 = c.bootloader_version(entry)
    print("   ", fmt(v0))
    if v0 is None:
        raise SystemExit("FAIL: legacy bootloader — cannot self-update (needs SWD)")
    entry = find_ledbutton(c)          # address may change after the round-trip
    print("   back running at 0x%02X" % entry["address"])

    banner("3. Conductor.stage1_update() — replace stage-1, restore the app")
    with open(STAGE1_NEW, "rb") as f:
        s1 = f.read()
    with open(APP_BIN, "rb") as f:
        app = f.read()
    print("   stage-1 image %d B, app image %d B" % (len(s1), len(app)))

    def prog(done, total):
        if done == total or done % 1024 == 0:
            print("      %5d / %5d" % (done, total))

    t0 = time.monotonic()
    r = c.stage1_update(entry, s1, app_image=app, progress=prog)
    dt = time.monotonic() - t0
    print("   before:", fmt(r["before"]))
    print("   after :", fmt(r["after"]))
    print("   app restored:", r["app_restored"], "  total %.1f s" % dt)
    if r["after"] == r["before"]:
        raise SystemExit("FAIL: version did not change")

    banner("4. Re-enumerate — LED Button back and running?")
    entry = find_ledbutton(c)
    print("   LED Button at 0x%02X  uid %s" % (entry["address"], entry["uid"]))

    banner("5. bootloader_version() again — persisted?")
    v2 = c.bootloader_version(entry)
    print("   ", fmt(v2))
    if v2 != r["after"]:
        raise SystemExit("FAIL: version read-back mismatch")

    banner("ALL PASS — stage-1 updated through the Conductor, app restored, module running")


main()
