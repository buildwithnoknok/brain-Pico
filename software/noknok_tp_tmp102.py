# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
"""noknok_thirdparty driver: TI TMP102 temperature (SparkFun SEN-16304). Loaded by noknok_thirdparty only when a chip answers
at one of its addresses (DEV-248, from the RDM-13 exploration)."""
import time
from noknok_thirdparty import ThirdPartyModule, _probe_reg


# ── TI TMP102 temperature (SparkFun SEN-16304 Qwiic) ──────────────────────────

class TMP102(ThirdPartyModule):
    """Temperature, 0.0625 °C/LSB, ±0.5 °C. No ID register: identify() checks the
    read-only bits R1:R0 = 11 in the config register (and the 4 always-zero bits),
    which rejects ADS1115 / TMP117 / most other 0x48 chips. Confidence 'fingerprint'."""
    TYPE, NAME, VENDOR = "tmp102", "Temperature sensor (TMP102)", "SparkFun SEN-16304"
    ADDRESSES   = (0x48, 0x49, 0x4A, 0x4B)
    CONFIDENCE  = "fingerprint"
    PULLUP_OHMS = 2200            # SparkFun: 2.2 k, cuttable jumper (verify per board)
    PULLUP_REMOVABLE = True
    PULLUP_HOWTO = "cut the I2C pull-up jumper (marked 'I2C') on the back of the board"
    CAPABILITIES = {"temperature": "°C"}

    REG_TEMP, REG_CONF = 0x00, 0x01

    @staticmethod
    def _fingerprint(b):
        return b is not None and (b[0] & 0x60) == 0x60 and (b[1] & 0x0F) == 0

    @classmethod
    def identify(cls, i2c, address):
        return cls._fingerprint(_probe_reg(i2c, address, cls.REG_CONF, 2))

    def setup(self):
        b = self._read_reg(self.REG_CONF, 2)
        if not self._fingerprint(b):
            return False                   # gone, or another chip answers on the same address
        self._em = bool(b[1] & 0x10)       # extended (13-bit) mode
        return True

    def verify(self):
        # Nothing to lose on power loss (the power-on default is what we use), but the
        # IDENTITY can be lost: bench 17 Nov 2026, an ADS1115 plugged in on 0x48 answered
        # together with the TMP102 (wired-AND: config 0x60A0 & 0x8583 = 0x0080) and the
        # temperature register read 0x0000 = a plausible-looking 0 C. A failed fingerprint
        # makes _check() re-run setup(), which fails too, so reads return None.
        return self._fingerprint(self._read_reg(self.REG_CONF, 2))

    @property
    def temperature(self):
        if not self._check():
            return None
        b = self._read_reg(self.REG_TEMP, 2)
        if b is None:
            return None
        if getattr(self, "_em", False):
            raw = ((b[0] << 8) | b[1]) >> 3
            if raw & 0x1000:
                raw -= 0x2000
        else:
            raw = ((b[0] << 8) | b[1]) >> 4
            if raw & 0x800:
                raw -= 0x1000
        return raw * 0.0625

    def read(self):
        t = self.temperature
        return None if t is None else {"temperature": t}


DRIVER = TMP102
