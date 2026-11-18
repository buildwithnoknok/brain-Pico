# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
"""noknok_thirdparty driver: MEMSensing MSA311 3-axis accelerometer (Adafruit 5309 and
generic copies). Loaded by noknok_thirdparty only when a chip answers at 0x62
(DEV-248, from the RDM-13 exploration)."""
import time
from noknok_thirdparty import ThirdPartyModule, _probe_reg


class MSA311(ThirdPartyModule):
    """Accelerometer, 12-bit, ±2 g (1024 counts/g), 125 Hz. PART_ID 0x01 = 0x13
    (bench 17 Nov 2026, generic board). The chip starts in SUSPEND (0x11 = 0xDE) and
    goes back there after a power loss: verify() catches it, setup() wakes it."""
    TYPE, NAME, VENDOR = "msa311", "Motion sensor (MSA311)", "Adafruit 5309 / generic"
    ADDRESSES   = (0x62,)
    CONFIDENCE  = "id"
    PULLUP_OHMS = 5000            # assumed Adafruit-style (10 k + 10 k behind a shifter). Generic: check
    CAPABILITIES = {"acceleration": "m/s²"}

    REG_PART_ID, REG_ACC_X, REG_RANGE, REG_ODR, REG_PWR = 0x01, 0x02, 0x0F, 0x10, 0x11
    ODR = 0x07                    # 125 Hz, all three axes on (bits 7:5 = 0)
    ACC_LSB = 9.80665 / 1024      # ±2 g, 12 bit
    SETTLE_S = 0.05               # a few samples at 125 Hz after waking; read() returns None until then
    _ready_at = 0.0

    @classmethod
    def identify(cls, i2c, address):
        b = _probe_reg(i2c, address, cls.REG_PART_ID, 1)
        return b is not None and b[0] == 0x13

    def setup(self):
        ok = self._write_reg(self.REG_RANGE, (0x00,))          # ±2 g
        ok = ok and self._write_reg(self.REG_ODR, (self.ODR,))
        ok = ok and self._write_reg(self.REG_PWR, (0x00,))     # normal mode
        self._ready_at = time.monotonic() + self.SETTLE_S
        return ok

    def verify(self):
        p = self._read_reg(self.REG_PWR, 1)
        o = self._read_reg(self.REG_ODR, 1)
        return p is not None and o is not None and (p[0] & 0xC0) == 0 and o[0] == self.ODR

    def read(self):
        if not self._check() or time.monotonic() < self._ready_at:
            return None
        b = self._read_reg(self.REG_ACC_X, 6)
        if b is None:
            return None
        out = []
        for i in (0, 2, 4):
            v = b[i] | (b[i + 1] << 8)
            if v & 0x8000:
                v -= 0x10000
            out.append(round((v >> 4) * self.ACC_LSB, 3))      # 12 bit, left-aligned
        return {"acceleration": tuple(out)}

    @property
    def acceleration(self):
        r = self.read()
        return None if r is None else r["acceleration"]


DRIVER = MSA311
