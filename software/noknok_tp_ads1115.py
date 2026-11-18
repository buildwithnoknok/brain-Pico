# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
"""noknok_thirdparty driver: TI ADS1115 4-channel 16-bit ADC (Adafruit 1085 / generic). Loaded by noknok_thirdparty only when a chip answers
at one of its addresses (DEV-248, from the RDM-13 exploration)."""
import time
from noknok_thirdparty import ThirdPartyModule, _probe_reg


class ADS1115(ThirdPartyModule):
    """4-channel 16-bit ADC (TI ADS1115; Adafruit 1085 and generic copies). No ID register.
    Fingerprint = power-on defaults of the threshold registers (Lo 0x8000, Hi 0x7FFF) plus
    comparator disabled (config low bits 11); this driver never changes them. TMP102 at the
    same addresses reads Lo 0x4B00 / Hi 0x5000, so the two never cross-match.
    Single-shot, ±4.096 V range (125 uV/LSB), 860 SPS. Non-blocking: every read() collects
    the finished conversion and starts the next channel, so all 4 refresh every 4 reads."""
    TYPE, NAME, VENDOR = "ads1115", "Analog input (ADS1115)", "Adafruit 1085 / generic"
    ADDRESSES   = (0x48, 0x49, 0x4A, 0x4B)
    CONFIDENCE  = "fingerprint"
    PULLUP_OHMS = 10000           # Adafruit 1085: 10 k, no level shifter. Generic copies: check the code
    CAPABILITIES = {"a0": "V", "a1": "V", "a2": "V", "a3": "V"}

    REG_CONV, REG_CONF, REG_LO, REG_HI = 0x00, 0x01, 0x02, 0x03
    CONF_LO = 0xE3                # DR = 860 SPS, comparator disabled
    LSB_V = 4.096 / 32768

    @staticmethod
    def _fp(lo, hi, conf):
        return (lo is not None and hi is not None and conf is not None
                and lo[0] == 0x80 and lo[1] == 0x00 and hi[0] == 0x7F and hi[1] == 0xFF
                and (conf[1] & 0x03) == 0x03)

    @classmethod
    def identify(cls, i2c, address):
        return cls._fp(_probe_reg(i2c, address, cls.REG_LO, 2), _probe_reg(i2c, address, cls.REG_HI, 2),
                       _probe_reg(i2c, address, cls.REG_CONF, 2))

    def _start(self, ch):
        # OS=1 start, MUX=100+ch (AINch vs GND), PGA=001 (+-4.096 V), MODE=1 single-shot
        return self._write_reg(self.REG_CONF, (0x80 | ((4 + ch) << 4) | 0x02 | 0x01, self.CONF_LO))

    def setup(self):
        if not self._fp(self._read_reg(self.REG_LO, 2), self._read_reg(self.REG_HI, 2),
                        self._read_reg(self.REG_CONF, 2)):
            return False
        self._ch = 0
        self.values = [None, None, None, None]
        return self._start(0)

    def verify(self):
        # power loss -> config back to 0x8583 (128 SPS); another chip -> thresholds differ
        conf = self._read_reg(self.REG_CONF, 2)
        return (self._fp(self._read_reg(self.REG_LO, 2), self._read_reg(self.REG_HI, 2), conf)
                and conf[1] == self.CONF_LO)

    def read(self):
        if not self._check():
            return None
        conf = self._read_reg(self.REG_CONF, 2)
        if conf is None:
            return None
        if conf[0] & 0x80:                      # conversion finished
            b = self._read_reg(self.REG_CONV, 2)
            if b is None:
                return None
            raw = (b[0] << 8) | b[1]
            if raw & 0x8000:
                raw -= 0x10000
            self.values[self._ch] = round(raw * self.LSB_V, 4)
            self._ch = (self._ch + 1) % 4
            self._start(self._ch)
        return {"a%d" % i: v for i, v in enumerate(self.values)}

    def voltage(self, ch):
        self.read()
        return self.values[ch] if not self._lost else None


DRIVER = ADS1115
