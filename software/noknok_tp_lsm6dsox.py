# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
"""noknok_thirdparty driver: ST LSM6DSOX accelerometer + gyroscope (Adafruit 4438). Loaded by noknok_thirdparty only when a chip answers
at one of its addresses (DEV-248, from the RDM-13 exploration)."""
import time
from noknok_thirdparty import ThirdPartyModule, _probe_reg


class LSM6DSOX(ThirdPartyModule):
    """Accelerometer + gyroscope. WHO_AM_I 0x0F = 0x6C (shared by LSM6DSO / DSO32,
    which use the same registers). Setup: 104 Hz, ±2 g, 250 dps, BDU on."""
    TYPE, NAME, VENDOR = "lsm6dsox", "Motion sensor (LSM6DSOX)", "Adafruit 4438"
    ADDRESSES   = (0x6A, 0x6B)
    CONFIDENCE  = "id"
    PULLUP_OHMS = 5000            # 10 k bus side || 10 k behind the BSS138 shifter (schematic)
    CAPABILITIES = {"acceleration": "m/s²", "gyro": "dps", "temperature": "°C"}

    WHO_AM_I, CTRL1_XL, CTRL2_G, CTRL3_C, OUT_TEMP_L = 0x0F, 0x10, 0x11, 0x12, 0x20
    ACC_LSB  = 0.061e-3 * 9.80665     # ±2 g: 0.061 mg/LSB -> m/s²
    GYRO_LSB = 8.75e-3                # 250 dps: 8.75 mdps/LSB
    # Bench 17 Nov 2026: after a (re)setup the gyro reads garbage for ~55 ms (up to
    # 44 dps at rest) and the accel one bad sample at ~16 ms; clean from 66 ms on.
    # Non-blocking: read() returns None until the window is over.
    SETTLE_S = 0.1
    _ready_at = 0.0

    @classmethod
    def identify(cls, i2c, address):
        b = _probe_reg(i2c, address, cls.WHO_AM_I, 1)
        return b is not None and b[0] == 0x6C

    def setup(self):
        ok = self._write_reg(self.CTRL3_C, (0x44,))     # BDU + register auto-increment
        ok = ok and self._write_reg(self.CTRL1_XL, (0x40,))   # 104 Hz, ±2 g
        ok = ok and self._write_reg(self.CTRL2_G, (0x40,))    # 104 Hz, 250 dps
        self._ready_at = time.monotonic() + self.SETTLE_S
        return ok

    @staticmethod
    def _s16(b, i):
        v = b[i] | (b[i + 1] << 8)
        return v - 0x10000 if v & 0x8000 else v

    def verify(self):
        b = self._read_reg(self.CTRL1_XL, 2)        # CTRL1_XL, CTRL2_G (0x00 after power-on)
        return b is not None and b[0] == 0x40 and b[1] == 0x40

    def read(self):
        if not self._check() or time.monotonic() < self._ready_at:
            return None                             # lost, or still settling after setup
        b = self._read_reg(self.OUT_TEMP_L, 14)    # temp, gyro xyz, accel xyz
        if b is None:
            return None
        s = self._s16
        return {"temperature": round(s(b, 0) / 256 + 25, 2),
                "gyro": tuple(round(s(b, i) * self.GYRO_LSB, 2) for i in (2, 4, 6)),
                "acceleration": tuple(round(s(b, i) * self.ACC_LSB, 3) for i in (8, 10, 12))}

    @property
    def acceleration(self):
        r = self.read()
        return None if r is None else r["acceleration"]

    @property
    def gyro(self):
        r = self.read()
        return None if r is None else r["gyro"]


DRIVER = LSM6DSOX
