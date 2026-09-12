# module_flasher.py — noknok I2C OTA flasher (Pico / CircuitPython)
#
# Re-flashes a CH32V003 noknok module's APPLICATION over the I2C bus, talking to
# the shared noknok bootloader (module-I2C-bootloader). No SWDIO cable needed.
#
# The .bin you pass is the OFFSET-LINKED application image (linked at the app base via
# app.ld) — i.e. exactly what `make build` produces in a module's firmware/src.
# It is flashed at app-relative offset 0 — the bootloader adds the base, so the host never
# knows it (0x1000 under the legacy monolithic bootloader, 0x1400 under stage-0/stage-1).
#
# Bootloader protocol @ 0x7E:
#   write [0x01]                          ERASE app region + metadata
#   write [0x02, offHi, offLo, <64 B>]    WRITE_CHUNK (one 64-byte page)
#   write [0x04, len(4 LE), crc32(4 LE)]  VERIFY  -> writes validity marker
#   write [0x05]                          BOOT (jump to app if valid; or, if a
#                                         stage-1 update is armed, reset so
#                                         stage-0 installs it)
#   write [0x06, len(4 LE), crc32(4 LE)]  VERIFY_STAGE1 -> arms a bootloader
#                                         self-update (stage-0/stage-1 only)
#   write [0xB1], then read 5 bytes       GET_VERSION -> [proto, major, minor,
#                                         patch, layout] (stage-0/stage-1 only;
#                                         layout byte = 0 before stage-1 1.2.0)
#   read  2 bytes -> [state, last_error]  state: 0 IDLE 1 BUSY 2 READY 3 ERROR
#
# A running app is flipped into the bootloader with app command 0xB0.
#
# Bootloader self-update (DEV-31): the transfer is IDENTICAL to an app update —
# same ERASE / WRITE_CHUNK into the same staging area — only the closing command
# differs (VERIFY_STAGE1 instead of VERIFY). The app is destroyed in the process
# (staging IS the app region) and must be re-pushed afterwards; flash_stage1()
# does not do that for you.
#
# GET_VERSION is also the fleet discriminator: the legacy monolithic bootloader
# does not implement 0xB1, so "no answer" means old world / not self-updatable.
#
# CRC32 = zlib (poly 0xEDB88320, init/final 0xFFFFFFFF) to match the bootloader.

import time

BL_ADDR        = 0x7E    # bootloader flash-mode address
APP_CMD_ENTER  = 0xB0    # app command: reset into bootloader

CMD_ERASE          = 0x01
CMD_WRITE          = 0x02
CMD_VERIFY         = 0x04
CMD_BOOT           = 0x05
CMD_VERIFY_STAGE1  = 0x06   # DEV-31
CMD_GET_VERSION    = 0xB1   # DEV-31 (same shape as the app's GET_VERSION)
CMD_GET_UID        = 0xB3   # DEV-31 hardening D: 8-byte chip UID

ST_IDLE, ST_BUSY, ST_READY, ST_ERROR = 0, 1, 2, 3

PAGE = 64

# Human-readable bootloader error codes (last_error byte).
_ERRMSG = {
    0: "none",
    1: "bad WRITE_CHUNK length",
    2: "bad VERIFY length",
    3: "write offset out of range",
    4: "verify length invalid",
    5: "CRC mismatch",
    6: "BOOT with no valid app",
    7: "app unhealthy - stage-1 stopped booting it after 3 failed attempts",
    8: "VERIFY_STAGE1: staged image is not a stage-1 for this layout (bad header)",
}


def crc32(data):
    """zlib CRC32 (poly 0xEDB88320). Matches the bootloader's crc32_buf()."""
    try:
        import binascii
        return binascii.crc32(data) & 0xFFFFFFFF
    except (ImportError, AttributeError):
        pass
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if (crc & 1) else (crc >> 1)
    return crc ^ 0xFFFFFFFF


class FlashError(Exception):
    pass


class ModuleFlasher:
    """
    OTA flasher sharing an existing busio.I2C bus (e.g. the Conductor's).

        from module_flasher import ModuleFlasher
        f = ModuleFlasher(conductor.i2c)
        with open("keyboard_firmware.bin", "rb") as fh:
            f.flash(fh.read(), runtime_addr=0x0A)   # 0x0A = the running module
    """

    def __init__(self, i2c):
        self.i2c = i2c

    # ── low-level bus helpers ────────────────────────────────────────────────
    def _write(self, addr, data):
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.writeto(addr, bytes(data))
            return True
        except OSError:
            return False
        finally:
            self.i2c.unlock()

    def _read_status(self):
        """Return (state, last_error), or None if the bootloader isn't answering."""
        buf = bytearray(2)
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.readfrom_into(BL_ADDR, buf)
            return buf[0], buf[1]
        except OSError:
            return None
        finally:
            self.i2c.unlock()

    def present(self):
        """True if the bootloader answers at 0x7E. NOTE: i2c.scan() can't see
        0x7E (reserved address, outside CircuitPython's 0x08-0x77 scan range),
        so we probe it by a direct read."""
        return self._read_status() is not None

    def _wait_ready(self, timeout=3.0):
        """Poll status until READY. Raise FlashError on ERROR/timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            st = self._read_status()
            if st is None:
                time.sleep(0.002)
                continue
            state, err = st
            if state == ST_READY:
                return
            if state == ST_ERROR:
                raise FlashError("bootloader error: %s (code %d)" %
                                 (_ERRMSG.get(err, "unknown"), err))
            time.sleep(0.002)   # IDLE or BUSY — keep waiting
        raise FlashError("timeout waiting for READY")

    # ── high-level steps ─────────────────────────────────────────────────────
    def enter_bootloader(self, runtime_addr, settle=0.20):
        """Tell a running module (at runtime_addr) to reset into the bootloader."""
        self._write(runtime_addr, [APP_CMD_ENTER])
        time.sleep(settle)   # warm reset + bootloader I2C bring-up (immediate, no enum backoff)

    def wait_for_bootloader(self, timeout=2.0):
        """Block until 0x7E answers. Returns True, or raises FlashError on timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._read_status() is not None:
                return True
            time.sleep(0.02)
        raise FlashError("bootloader did not appear at 0x%02X" % BL_ADDR)

    def erase(self):
        if not self._write(BL_ADDR, [CMD_ERASE]):
            raise FlashError("ERASE not acknowledged")
        self._wait_ready(timeout=3.0)

    def write_chunk(self, offset, data):
        """Program one ≤64-byte page at app-relative offset (0xFF-padded to 64)."""
        page = bytes(data) + b"\xff" * (PAGE - len(data))
        pkt  = bytes([CMD_WRITE, (offset >> 8) & 0xFF, offset & 0xFF]) + page
        if not self._write(BL_ADDR, pkt):
            raise FlashError("WRITE_CHUNK @0x%04X not acknowledged" % offset)
        self._wait_ready(timeout=1.0)

    def verify(self, length, crc):
        pkt = bytes([CMD_VERIFY,
                     length & 0xFF, (length >> 8) & 0xFF,
                     (length >> 16) & 0xFF, (length >> 24) & 0xFF,
                     crc & 0xFF, (crc >> 8) & 0xFF,
                     (crc >> 16) & 0xFF, (crc >> 24) & 0xFF])
        if not self._write(BL_ADDR, pkt):
            raise FlashError("VERIFY not acknowledged")
        self._wait_ready(timeout=2.0)

    def boot(self):
        """Jump to the freshly-flashed app. No status afterwards (module re-enumerates)."""
        self._write(BL_ADDR, [CMD_BOOT])

    # ── DEV-31: bootloader self-update ───────────────────────────────────────
    def get_version(self):
        """Bootloader version: write 0xB1, read [proto, major, minor, patch, layout].
        Returns the 5-tuple, or None if the bootloader doesn't answer — which is
        how you tell a legacy monolithic bootloader (not self-updatable) from a
        stage-0/stage-1 one.

        Byte 4 is the flash layout the bootloader installs apps for (stage-1
        v1.2.0+). A stage-1 older than that returns 0 there — "did not say" —
        and the Conductor treats that as unknown, never as a guess. (The app's
        own 0xB1 reply is still 4 bytes; that is a different address.)"""
        if not self._write(BL_ADDR, [CMD_GET_VERSION]):
            return None
        buf = bytearray(5)
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.readfrom_into(BL_ADDR, buf)
            return tuple(buf)
        except OSError:
            return None
        finally:
            self.i2c.unlock()

    def get_uid(self):
        """Chip UID of the module in the bootloader: write 0xB3, read 8 bytes.
        Returns the lower-case hex string — the SAME format the app reports
        during enumeration and the Conductor keys its registry by — so a module
        parked at 0x7E can be matched to the type it enumerated as. None if the
        bootloader doesn't answer (legacy, no 0xB3)."""
        if not self._write(BL_ADDR, [CMD_GET_UID]):
            return None
        buf = bytearray(8)
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.readfrom_into(BL_ADDR, buf)
            return bytes(buf).hex()
        except OSError:
            return None
        finally:
            self.i2c.unlock()

    def verify_stage1(self, length, crc):
        """Same payload as VERIFY, different opcode. READY means the staged image
        passed its CRC and the control block is written: stage-0 will install it
        on the next reset (which boot() triggers)."""
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
        Essential after boot() on a stage-1 update: the OLD stage-1 is still at
        0x7E for a moment, so wait_for_bootloader() alone would return at once
        with the old one and the caller would never see the new version."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._read_status() is None:
                return True
            time.sleep(0.01)
        raise FlashError("bootloader never went away after BOOT")

    def flash_stage1(self, data, runtime_addr=None, progress=None):
        """
        Replace the module's stage-1 bootloader over I2C (DEV-31).

        Same transfer as flash(), closed with VERIFY_STAGE1 instead of VERIFY;
        then BOOT hands off to stage-0, which installs the new stage-1 and reboots
        into it. Returns (version_before, version_after) as 4-tuples.

        The module is left in the bootloader with NO valid app — the staging area
        is the app region. Re-push the application with flash(app, runtime_addr=None).

        Raises FlashError if the module has no stage-0/stage-1 (legacy bootloader,
        no 0xB1) — such modules cannot self-update and need SWD.
        """
        total = len(data)
        if total == 0:
            raise FlashError("empty stage-1 image")

        if runtime_addr is not None:
            self.enter_bootloader(runtime_addr)
        self.wait_for_bootloader()

        before = self.get_version()
        if before is None:
            raise FlashError("legacy bootloader (no GET_VERSION) — cannot self-update, needs SWD")

        self.erase()
        off = 0
        while off < total:
            self.write_chunk(off, data[off:off + PAGE])
            off += PAGE
            if progress:
                progress(min(off, total), total)

        self.verify_stage1(total, crc32(data))
        self.boot()
        self.wait_for_bootloader_gone(timeout=2.0)
        self.wait_for_bootloader(timeout=5.0)

        after = self.get_version()
        if after is None:
            raise FlashError("new stage-1 did not answer GET_VERSION after install")
        return before, after

    # ── orchestration ────────────────────────────────────────────────────────
    def flash(self, data, runtime_addr=None, progress=None):
        """
        Flash an offset-linked app image (bytes).

        runtime_addr : if given, first send 0xB0 to that running module to drop it
                       into the bootloader. If None, assume the module is already
                       in the bootloader (e.g. a blank board, app invalid).
        progress     : optional callback(done_bytes, total_bytes).
        """
        total = len(data)
        if total == 0:
            raise FlashError("empty image")

        if runtime_addr is not None:
            self.enter_bootloader(runtime_addr)
        self.wait_for_bootloader()

        self.erase()

        off = 0
        while off < total:
            self.write_chunk(off, data[off:off + PAGE])
            off += PAGE
            if progress:
                progress(min(off, total), total)

        self.verify(total, crc32(data))
        self.boot()
        return True
