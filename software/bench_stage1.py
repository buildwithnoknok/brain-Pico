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
# What it proves, in order:
#   1. GET_VERSION (0xB1) answers at 0x7E   -> stage-1 is up and speaks the new command
#   2. Reported version is 1.0.0            -> the SWD-flashed build is what is running
#   3. ERASE -> WRITE_CHUNK xN of the v1.0.1 image into the staging area
#   4. VERIFY_STAGE1 (0x06) returns READY   -> CRC matched, control block written
#   5. BOOT -> module vanishes -> comes back at 0x7E
#   6. GET_VERSION now reports 1.0.1        -> stage-0 installed the new stage-1
#
# Step 6 is the authoritative proof. Same bytes before and after would prove
# nothing; a CHANGED version can only mean the copy happened.
#
# Afterwards, verify over SWD from the Pi (see the walkthrough): stage-1 region
# == v1.0.1 image, control block cleared, staging copy still in the app region.

import time
import board
import busio
from module_flasher import ModuleFlasher, FlashError, BL_ADDR, PAGE, crc32

CMD_VERIFY_STAGE1 = 0x06
CMD_GET_VERSION   = 0xB1

STAGE1_IMAGE = "noknok_stage1_v101.bin"
EXPECT_BEFORE = (1, 0, 0)
EXPECT_AFTER  = (1, 0, 1)


class Stage1Flasher(ModuleFlasher):
    """ModuleFlasher plus the two DEV-31 commands."""

    def get_version(self):
        """Write 0xB1, then read 4 bytes: [proto, major, minor, patch].
        Returns the tuple, or None if the bootloader isn't answering."""
        if not self._write(BL_ADDR, [CMD_GET_VERSION]):
            return None
        buf = bytearray(4)
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.readfrom_into(BL_ADDR, buf)
            return tuple(buf)
        except OSError:
            return None
        finally:
            self.i2c.unlock()

    def verify_stage1(self, length, crc):
        """Same payload as VERIFY, different opcode. READY means the control
        block is now written and stage-0 will install on the next reset."""
        pkt = bytes([CMD_VERIFY_STAGE1,
                     length & 0xFF, (length >> 8) & 0xFF,
                     (length >> 16) & 0xFF, (length >> 24) & 0xFF,
                     crc & 0xFF, (crc >> 8) & 0xFF,
                     (crc >> 16) & 0xFF, (crc >> 24) & 0xFF])
        if not self._write(BL_ADDR, pkt):
            raise FlashError("VERIFY_STAGE1 not acknowledged")
        self._wait_ready(timeout=2.0)

    def wait_for_bootloader_gone(self, timeout=2.0):
        """Block until 0x7E STOPS answering — the module has reset into stage-0.
        This matters: after BOOT the bootloader is still at 0x7E for a moment,
        so a plain wait_for_bootloader() would return immediately with the OLD
        stage-1 and the test would be blind."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._read_status() is None:
                return True
            time.sleep(0.01)
        raise FlashError("bootloader never went away after BOOT")


def banner(s):
    print("\n" + "=" * 62)
    print(s)
    print("=" * 62)


def fmt(v):
    return "proto=%d  v%d.%d.%d" % v if v else "no answer"


def main():
    # Same pins + speed as the Conductor (noknok.py): SCL=GP9, SDA=GP8, 100 kHz.
    i2c = busio.I2C(board.GP9, board.GP8, frequency=100_000)
    f = Stage1Flasher(i2c)

    # ── 1-2. is stage-1 up, and is it the version we flashed? ─────────────────
    banner("1. GET_VERSION on the freshly SWD-flashed stage-1")
    f.wait_for_bootloader(timeout=3.0)
    v = f.get_version()
    print("   0x7E answers 0xB1:", fmt(v))
    if v is None:
        raise SystemExit("FAIL: stage-1 did not answer GET_VERSION")
    if v[1:] != EXPECT_BEFORE:
        raise SystemExit("FAIL: expected v%d.%d.%d before update" % EXPECT_BEFORE)
    print("   PASS — stage-1 v1.0.0 is running and speaks 0xB1")

    # ── 3. stage the v1.0.1 image into the app region ─────────────────────────
    banner("2. Stage the v1.0.1 image (ERASE + WRITE_CHUNK)")
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
    if v2[1:] != EXPECT_AFTER:
        raise SystemExit("FAIL: expected v%d.%d.%d after update, got v%d.%d.%d"
                         % (EXPECT_AFTER + v2[1:]))

    banner("ALL PASS — stage-1 updated itself over I2C: v1.0.0 -> v1.0.1")
    print("Now verify over SWD from the Pi (stage-1 region, control block, staging).")


main()
