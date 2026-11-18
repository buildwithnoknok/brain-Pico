# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
"""noknok_thirdparty driver: SSD1306 monochrome OLED (Adafruit 4440 0.91" 128x32 and
compatibles). Loaded by noknok_thirdparty only when a chip answers at 0x3C / 0x3D
(DEV-248, from the RDM-13 exploration).

    d = c.ssd1306[0]
    d.text("Hello", 0, 0)       # built-in noknok 8x16 font: 16 chars x 2 lines on 128x32
    d.show()                    # sends only the changed 128-byte pages

Size: the controller cannot report the panel size. Default 128x32 (the board on the bench);
a 128x64 panel needs d.set_size(64) - the app would store that per board.
Cost: one page = 129 bytes = ~12 ms at 100 kHz; show() sends the changed pages (all 4 of a
128x32 = ~50 ms). show(max_pages=1) spreads that over several loop passes."""
import time
from noknok_thirdparty import ThirdPartyModule

try:
    from noknok import _font8x16, _glyph_index      # the font the noknok Display uses
except ImportError:                                 # pragma: no cover
    _font8x16 = None


class SSD1306(ThirdPartyModule):
    """Identification is weak: SSD1306 has no ID register, only a status byte (read
    0x42 = display off on the bench). identify() accepts a 0x3C/0x3D chip whose status
    byte has bit 7 (busy) clear. CONFIDENCE 'family': SSD1306-type, size unknown."""
    TYPE, NAME, VENDOR = "ssd1306", "OLED display (SSD1306)", "Adafruit 4440 0.91in / compatible"
    ADDRESSES   = (0x3C, 0x3D)
    CONFIDENCE  = "family"
    PULLUP_OHMS = 5000            # Adafruit 4440 rev B: 10 k bus side || 10 k behind BSS138 (schematic)
    CAPABILITIES = {"display": "128x32 mono"}
    WIDTH, HEIGHT = 128, 32

    @classmethod
    def identify(cls, i2c, address):
        b = bytearray(1)
        while not i2c.try_lock():
            pass
        try:
            i2c.readfrom_into(address, b)
        except OSError:
            return False
        finally:
            i2c.unlock()
        return (b[0] & 0x80) == 0

    def set_size(self, height):
        """32 or 64 rows. Re-initialises the panel and clears the buffer."""
        self.HEIGHT = height
        self.CAPABILITIES = {"display": "128x%d mono" % height}
        self.buf = None
        return self.setup()

    def setup(self):
        h = self.HEIGHT
        if getattr(self, "buf", None) is None:
            self.buf = bytearray(self.WIDTH * h // 8)
        self._on = True
        cmds = (0xAE, 0xD5, 0x80, 0xA8, h - 1, 0xD3, 0x00, 0x40, 0x8D, 0x14, 0x20, 0x00,
                0xA1, 0xC8, 0xDA, 0x02 if h == 32 else 0x12, 0x81, 0x8F if h == 32 else 0xCF,
                0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0x2E, 0xAF)
        ok = self._xfer(bytes((0x00,) + cmds))
        self._dirty = set(range(h // 8))          # panel RAM is undefined after init: resend all
        return ok

    def verify(self):
        # status bit 6 = display OFF. After a power loss the panel comes back off.
        b = bytearray(1)
        if not self._xfer(None, b):
            return False
        return bool(b[0] & 0x40) != self._on

    def power(self, on):
        self._on = bool(on)
        return self._xfer(bytes((0x00, 0xAF if on else 0xAE)))

    # ── drawing (buffer only; show() sends it) ──
    def fill(self, on=0):
        v = 0xFF if on else 0x00
        for i in range(len(self.buf)):
            self.buf[i] = v
        self._dirty = set(range(self.HEIGHT // 8))

    def pixel(self, x, y, on=1):
        if 0 <= x < self.WIDTH and 0 <= y < self.HEIGHT:
            i = (y >> 3) * self.WIDTH + x
            if on:
                self.buf[i] |= 1 << (y & 7)
            else:
                self.buf[i] &= ~(1 << (y & 7)) & 0xFF
            self._dirty.add(y >> 3)

    def text(self, s, x=0, y=0, on=1):
        """8x16 text at pixel (x, y); y a multiple of 8 is fastest (whole bytes)."""
        if _font8x16 is None:
            return
        font = _font8x16()
        for ch in s:
            if x > self.WIDTH - 8:
                break
            g = _glyph_index(ch) * 16
            for col in range(8):
                bit = 0x80 >> col
                for half in (0, 1):
                    yy = y + half * 8
                    if yy & 7 == 0 and yy < self.HEIGHT:
                        v = 0
                        for r in range(8):
                            if font[g + half * 8 + r] & bit:
                                v |= 1 << r
                        i = (yy >> 3) * self.WIDTH + x + col
                        self.buf[i] = v if on else (~v & 0xFF)
                        self._dirty.add(yy >> 3)
                    else:
                        for r in range(8):
                            if font[g + half * 8 + r] & bit:
                                self.pixel(x + col, yy + r, on)
            x += 8

    def show(self, max_pages=None):
        """Send changed pages. Returns the number of pages still waiting (0 = done),
        or None if the display is not reachable."""
        if not self._check():
            return None
        n = 0
        for p in sorted(self._dirty):
            if max_pages is not None and n >= max_pages:
                break
            if not self._xfer(bytes((0x00, 0x21, 0, self.WIDTH - 1, 0x22, p, p))):
                return None
            mv = memoryview(self.buf)[p * self.WIDTH:(p + 1) * self.WIDTH]
            if not self._xfer(b"\x40" + bytes(mv)):
                return None
            self._dirty.discard(p)
            n += 1
        return len(self._dirty)

    def read(self):
        """Generic read for the app status: the display is an output, so just 'on'."""
        return None if not self._check() else {"display": "on" if self._on else "off"}


DRIVER = SSD1306
