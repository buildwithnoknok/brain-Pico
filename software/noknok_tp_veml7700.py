# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
"""noknok_thirdparty driver: Vishay VEML7700 ambient light (Adafruit 4162). Loaded by noknok_thirdparty only when a chip answers
at one of its addresses (DEV-248, from the RDM-13 exploration)."""
import time
from noknok_thirdparty import ThirdPartyModule, _probe_reg


# ── Vishay VEML7700 ambient light (Adafruit 4162) ─────────────────────────────

class VEML7700(ThirdPartyModule):
    """Ambient light, 0.0042 lx resolution at best. Registers are 16-bit little endian.
    ID register 0x07: low byte 0x81 (verified on the bench 16 Nov 2026: 0x81 0xC4)."""
    TYPE, NAME, VENDOR = "veml7700", "Light sensor (VEML7700)", "Adafruit 4162"
    ADDRESSES   = (0x10,)
    CONFIDENCE  = "id"
    # Effective load when the line is LOW: 4.7 k on the bus side || 4.7 k behind the BSS138 level
    # shifter (the shifter conducts when low, so both pairs are sunk). Adafruit schematic
    # 'VEML7700 STEMMA QT', checked 17 Nov 2026. Not 10 k as first assumed.
    PULLUP_OHMS = 2350
    CAPABILITIES = {"lux": "lx", "white": "counts"}

    REG_CONF, REG_ALS, REG_WHITE, REG_ID = 0x00, 0x04, 0x05, 0x07
    # gain code -> factor ; integration-time code -> ms (datasheet table)
    GAINS = {0b00: 1.0, 0b01: 2.0, 0b10: 0.125, 0b11: 0.25}
    ITS   = {0b1100: 25, 0b1000: 50, 0b0000: 100, 0b0001: 200, 0b0010: 400, 0b0011: 800}

    @classmethod
    def identify(cls, i2c, address):
        b = _probe_reg(i2c, address, cls.REG_ID, 2)
        return b is not None and b[0] == 0x81

    # Auto-range ladder, least to most sensitive (Vishay app note flow):
    # gain 1/8 .. 2 at 100 ms, then longer integration at gain 2.
    LADDER = ((0b10, 0b0000), (0b11, 0b0000), (0b00, 0b0000), (0b01, 0b0000),
              (0b01, 0b0001), (0b01, 0b0010), (0b01, 0b0011))
    LOW, HIGH = 100, 20000      # raw counts: step more / less sensitive outside this

    start_step, autorange = 1, True     # the product's choice, kept for recovery

    def setup(self, step=None, autorange=None):
        """Start at gain 1/4, 100 ms (step 1, up to ~17,600 lx). With autorange, every
        lux read moves ONE ladder step when the raw count is outside LOW..HIGH,
        so a product loop is never blocked; the value settles after a few reads
        (worst case dark: 6 steps, ~2.5 s). Powers the chip on (SD = 0).
        Arguments a product passes are kept: after a power loss the recovery calls
        setup() without arguments and gets the same start step and autorange back.
        Sleeps one integration time (~110 ms) for the first result; during a recovery
        that happens inside the product loop, once per power loss."""
        if step is not None:
            self.start_step = step
        if autorange is not None:
            self.autorange = autorange
        self._last = None
        ok = self._apply(self.start_step)
        time.sleep(self.ITS[self._it] / 1000 + 0.01)   # first result after one IT
        return ok

    def _conf(self):
        return (self._gain << 11) | (self._it << 6)      # SD = 0: powered on

    def verify(self):
        b = self._read_reg(self.REG_CONF, 2)
        if b is None:
            return False
        return (b[0] | b[1] << 8) == self._conf()

    def _apply(self, step):
        self._step = step
        self._gain, self._it = self.LADDER[step]
        conf = self._conf()
        ok = self._write_reg(self.REG_CONF, (conf & 0xFF, conf >> 8))
        # A changed setting gives a valid result only after one full new
        # integration (plus margin for the one already running).
        self._settle = time.monotonic() + 2 * self.ITS[self._it] / 1000 + 0.01
        return ok

    def _resolution(self):
        # Vishay app note (2020): 0.0042 lx/count at gain 2, 800 ms. (Older
        # datasheets and the Adafruit library use 0.0036: 14 % lower readings.)
        return 0.0042 * (2.0 / self.GAINS[self._gain]) * (800 / self.ITS[self._it])

    def _raw(self, reg):
        b = self._read_reg(reg, 2)
        return None if b is None else b[0] | (b[1] << 8)

    @property
    def lux(self):
        """Lux, or None (no reading yet / I2C error). While a new range settles it may
        repeat the last real value for up to 2 x the integration time."""
        if not self._check():
            return None
        if time.monotonic() < self._settle:
            return self._last                 # still integrating with a new setting
        raw = self._raw(self.REG_ALS)
        if raw is None:
            return None
        if raw == 0 and self.autorange and self._step < len(self.LADDER) - 1:
            # Bench 18 Nov 2026: in a dim room (0.7 lx) the start range (gain 1/4) counts 0.
            # That is "below this range", not "dark": step up, report no value yet.
            self._apply(self._step + 1)
            return self._last
        x = raw * self._resolution()
        if self.GAINS[self._gain] < 1.0 and x > 100:
            # Vishay non-linearity correction for low gain / high light
            x = (6.0135e-13 * x ** 4 - 9.3924e-9 * x ** 3 + 8.1488e-5 * x ** 2 + 1.0023 * x)
        x = round(x, 2)
        if self.autorange:
            if raw < self.LOW and self._step < len(self.LADDER) - 1:
                self._apply(self._step + 1)
            elif raw > self.HIGH and self._step > 0:
                self._apply(self._step - 1)
        self._last = x
        return x

    @property
    def white(self):
        return self._raw(self.REG_WHITE) if self._check() else None

    def read(self):
        lx = self.lux
        return None if lx is None else {"lux": lx, "white": self.white}


DRIVER = VEML7700
