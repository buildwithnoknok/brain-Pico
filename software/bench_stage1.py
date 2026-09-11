# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_stage1.py — DEV-31 bench test: exercise the stage-1 bootloader's new
# commands over I2C, then perform a real stage-1 self-update.
#
# Run from the Pico REPL / Thonny with the module connected on the Qwiic bus and
# the module already SWD-flashed with stage-0 + stage-1 (bench_i2c_start.bin).
# Put noknok_stage1_v101.bin on CIRCUITPY alongside this file.
#
# The three DEV-31 commands (get_version, verify_stage1, wait_for_bootloader_gone)
# live in ModuleFlasher itself; this script drives them step by step so each one
# is visible. For the product-shaped flow see bench_conductor_stage1.py.
#
# What it proves, in order:
#   1. GET_VERSION (0xB1) answers at 0x7E   -> stage-1 is up and speaks the new command
#   2. Reported version is the base build   -> the SWD-flashed build is what is running
#   3. ERASE -> WRITE_CHUNK xN of the PATCH+1 image into the staging area
#   4. VERIFY_STAGE1 (0x06) returns READY   -> CRC matched, control block written
#   5. BOOT -> module vanishes -> comes back at 0x7E
#   6. GET_VERSION now reports PATCH+1       -> stage-0 installed the new stage-1
#
# Step 6 is the authoritative proof. Same bytes before and after would prove
# nothing; a CHANGED version can only mean the copy happened.
#
# Afterwards, verify over SWD from the Pi (see the walkthrough): stage-1 region
# == the pushed image, control block cleared, staging copy still in the app region.

import time
import board
import busio
from module_flasher import ModuleFlasher, PAGE, crc32


STAGE1_IMAGE = "noknok_stage1_v101.bin"
# The runner builds the payload as the running version with PATCH+1, so the
# proof is relative: the version must change, by exactly one patch step.


def banner(s):
    print("\n" + "=" * 62)
    print(s)
    print("=" * 62)


def fmt(v):
    return "proto=%d  v%d.%d.%d" % v if v else "no answer"


def main():
    # Same pins + speed as the Conductor (noknok.py): SCL=GP9, SDA=GP8, 100 kHz.
    i2c = busio.I2C(board.GP9, board.GP8, frequency=100_000)
    f = ModuleFlasher(i2c)          # get_version / verify_stage1 / wait_for_bootloader_gone live here now

    # ── 1-2. is stage-1 up, and is it the version we flashed? ─────────────────
    banner("1. GET_VERSION on the freshly SWD-flashed stage-1")
    f.wait_for_bootloader(timeout=3.0)
    v = f.get_version()
    print("   0x7E answers 0xB1:", fmt(v))
    if v is None:
        raise SystemExit("FAIL: stage-1 did not answer GET_VERSION")
    print("   PASS — stage-1 v%d.%d.%d is running and speaks 0xB1" % tuple(v[1:]))

    # ── 3. stage the v1.0.1 image into the app region ─────────────────────────
    banner("2. Stage the new image (ERASE + WRITE_CHUNK)")
    with open(STAGE1_IMAGE, "rb") as fh:
        img = fh.read()
    n_pages = (len(img) + PAGE - 1) // PAGE
    print("   image %d B = %d pages" % (len(img), n_pages))
    f.erase()
    print("   ERASE ok")
    for i in range(n_pages):
        off = i * PAGE
        f.write_chunk(off, img[off:off + PAGE])
    print("   %d pages written" % n_pages)

    # ── 4. arm the update ─────────────────────────────────────────────────────
    banner("3. VERIFY_STAGE1 — CRC check, then arm stage-0")
    crc = crc32(img)
    f.verify_stage1(len(img), crc)
    print("   READY — CRC 0x%08X matched, control block written" % crc)

    # ── 5. reset; stage-0 installs; new stage-1 comes back ────────────────────
    banner("4. BOOT -> stage-0 installs -> new stage-1 boots")
    f.boot()
    f.wait_for_bootloader_gone(timeout=2.0)
    print("   0x7E went away (module reset into stage-0)")
    t0 = time.monotonic()
    f.wait_for_bootloader(timeout=5.0)
    print("   0x7E is back after %.0f ms" % ((time.monotonic() - t0) * 1000))

    # ── 6. THE proof: the version changed ─────────────────────────────────────
    banner("5. GET_VERSION after the update")
    v2 = f.get_version()
    print("   0x7E answers 0xB1:", fmt(v2))
    if v2 is None:
        raise SystemExit("FAIL: new stage-1 did not answer GET_VERSION")
    if tuple(v2[1:]) != (v[1], v[2], v[3] + 1):
        raise SystemExit("FAIL: expected v%d.%d.%d after update, got v%d.%d.%d"
                         % (v[1], v[2], v[3] + 1, v2[1], v2[2], v2[3]))

    banner("ALL PASS — stage-1 updated itself over I2C: v%d.%d.%d -> v%d.%d.%d" % (tuple(v[1:]) + tuple(v2[1:])))
    print("Now verify over SWD from the Pi (stage-1 region, control block, staging).")


main()
