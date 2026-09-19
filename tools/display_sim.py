# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# display_sim.py — run the NoknokDisplay driver on a desktop Python against a
# VIRTUAL display module, and look at the result as ASCII art.
#
# The virtual module decodes the I2C byte stream exactly the way the real
# firmware does (module-I2C-1.42-display/firmware/src/display_firmware.c):
# same style-byte layout, same 8x8 font (parsed from the firmware's font8x8.h),
# same clipping and blit rules. So what you see here is what the panel shows,
# minus colour. It exists so driver work (DEV-41 and after) can be checked
# without a bench, and so a regression shows up as a diff, not a blank screen.
#
#   python tools/display_sim.py            # run the built-in checks + demo
#
# From your own script:
#   from display_sim import make_display
#   d, panel = make_display()
#   d.print("Hello"); print(panel.dump())

import os
import sys
import types
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SOFTWARE_DIR = os.path.join(HERE, "..", "software")
FONT8X8_H = os.path.join(HERE, "..", "..", "module-I2C-1.42-display",
                         "firmware", "src", "font8x8.h")


# ── Stub the CircuitPython-only modules so noknok.py imports on a PC ─────────

def _stub_modules():
    for name in ("board", "busio", "storage", "microcontroller", "digitalio"):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    sys.modules["board"].GP8 = "GP8"
    sys.modules["board"].GP9 = "GP9"
    sys.modules["busio"].I2C = object
    sys.modules["microcontroller"].nvm = bytearray(4096)
    if SOFTWARE_DIR not in sys.path:
        sys.path.insert(0, SOFTWARE_DIR)


# ── The firmware's 8x8 font (LSB = leftmost pixel, ASCII 0x20-0x7E) ──────────

def load_font8x8(path=FONT8X8_H):
    """Parse font8x8.h into {char code: [8 row bytes]}. Falls back to an
    all-blocks font if the header isn't there (geometry still checks out)."""
    font = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
    except OSError:
        for c in range(0x20, 0x7F):
            font[c] = [0xFF] * 8
        return font
    first = int(re.search(r"FONT8X8_FIRST\s+(0x[0-9A-Fa-f]+|\d+)", src).group(1), 0)
    rows = re.findall(r"\{\s*((?:0x[0-9A-Fa-f]{2}\s*,\s*){7}0x[0-9A-Fa-f]{2})\s*\}", src)
    for i, r in enumerate(rows):
        font[first + i] = [int(v, 16) for v in re.findall(r"0x[0-9A-Fa-f]{2}", r)]
    return font


# ── Virtual module ───────────────────────────────────────────────────────────

class VirtualPanel:
    """Decodes commands like display_firmware.c and keeps an RGB565 frame."""

    def __init__(self, w=80, h=160):
        self.w, self.h = w, h
        self.px = [[0] * w for _ in range(h)]
        self.font = load_font8x8()
        self.last_err = 0
        self.log = []                       # every command, for assertions
        # blit state
        self._blit_left = 0
        self._blit = None

    # -- helpers mirroring the C code --
    def fill_rect(self, x, y, w, h, c):
        if x >= self.w or y >= self.h or w == 0 or h == 0:
            self.last_err = 3
            return
        w = min(w, self.w - x)
        h = min(h, self.h - y)
        for yy in range(y, y + h):
            row = self.px[yy]
            for xx in range(x, x + w):
                row[xx] = c

    def draw_glyph(self, x, y, ch, scale, fg, bg, transparent):
        if ch < 0x20 or ch > 0x7E:
            ch = ord("?")
        rows = self.font[ch]
        if x >= self.w or y >= self.h:
            return
        for r in range(8):
            bits = rows[r]
            for c in range(8):
                on = (bits >> c) & 1
                if transparent and not on:
                    continue
                self.fill_rect(x + c * scale, y + r * scale, scale, scale,
                               fg if on else bg)

    def blit_begin(self, x, y, w, h, fg, bg, flags):
        if w == 0 or h == 0 or x + w > self.w or y + h > self.h:
            self.last_err = 3
            self._blit_left = 0
            return
        self._blit = dict(fg=fg, bg=bg, tr=flags & 1, w=w, x0=x, y0=y,
                          row=0, pos=0)
        self._blit_left = w * h

    def blit_data(self, data):
        if self._blit_left == 0:
            self.last_err = 4
            return
        b = self._blit
        for byte in data:
            if not self._blit_left:
                break
            for bit in range(7, -1, -1):
                if not self._blit_left:
                    break
                if b["pos"] >= b["w"]:
                    break
                on = (byte >> bit) & 1
                if b["tr"]:
                    if on:
                        self.fill_rect(b["x0"] + b["pos"], b["y0"] + b["row"], 1, 1, b["fg"])
                else:
                    self.px[b["y0"] + b["row"]][b["x0"] + b["pos"]] = b["fg"] if on else b["bg"]
                b["pos"] += 1
                self._blit_left -= 1
            if b["pos"] >= b["w"]:
                b["pos"] = 0
                b["row"] += 1

    # -- the command processor --
    def command(self, cmd):
        self.log.append(bytes(cmd))
        n = len(cmd)
        op = cmd[0]
        if op == 0x01 and n >= 3:
            self.fill_rect(0, 0, self.w, self.h, (cmd[1] << 8) | cmd[2])
        elif op == 0x02 and n >= 7:
            self.fill_rect(cmd[1], cmd[2], cmd[3], cmd[4], (cmd[5] << 8) | cmd[6])
        elif op == 0x03 and n >= 9:
            x, y = cmd[1], cmd[2]
            scale = ((cmd[3] >> 2) & 0x07) + 1
            trans = (cmd[3] >> 7) & 1
            fg = (cmd[4] << 8) | cmd[5]
            bg = (cmd[6] << 8) | cmd[7]
            for i in range(8, n):
                cx = x + (i - 8) * 8 * scale
                if cx >= self.w:
                    break
                self.draw_glyph(cx, y, cmd[i], scale, fg, bg, trans)
        elif op == 0x05 and n >= 10:
            self.blit_begin(cmd[1], cmd[2], cmd[3], cmd[4],
                            (cmd[5] << 8) | cmd[6], (cmd[7] << 8) | cmd[8], cmd[9])
        elif op == 0x06 and n >= 2:
            self.blit_data(cmd[1:])
        elif op in (0x10, 0x11, 0x12, 0x14, 0x15, 0xB0, 0xB1):
            pass
        else:
            self.last_err = 1

    def dump(self, x=0, y=0, w=None, h=None, palette=None):
        """ASCII art of a window. Background(0) = '.', white = '#', other
        colours get letters in order of appearance (or from `palette`)."""
        w = self.w - x if w is None else w
        h = self.h - y if h is None else h
        names = dict(palette or {})
        names.setdefault(0x0000, ".")
        names.setdefault(0xFFFF, "#")
        letters = iter("abcdefghijklmnopqrstuvwxyz")
        out = []
        for yy in range(y, min(y + h, self.h)):
            line = []
            for xx in range(x, min(x + w, self.w)):
                c = self.px[yy][xx]
                if c not in names:
                    names[c] = next(letters, "*")
                line.append(names[c])
            out.append("".join(line))
        return "\n".join(out)

    def count(self, color):
        return sum(1 for row in self.px for c in row if c == color)


class FakeI2C:
    """Just enough of busio.I2C for the driver: routes writes to the panel and
    answers status / GET_INFO reads."""

    def __init__(self, panel, address=0x08):
        self.panel = panel
        self.address = address
        self._pending = None

    def try_lock(self):
        return True

    def unlock(self):
        pass

    def writeto(self, addr, data):
        if addr != self.address:
            raise OSError("no device at 0x%02X" % addr)
        data = bytes(data)
        if data and data[0] == 0x15:
            self._pending = bytes([self.panel.w, self.panel.h, 0x10, 1, 0])
        else:
            self.panel.command(data)

    def readfrom_into(self, addr, buf):
        if addr != self.address:
            raise OSError("no device at 0x%02X" % addr)
        if self._pending and len(buf) >= len(self._pending):
            buf[:len(self._pending)] = self._pending
            self._pending = None
        else:
            for i in range(len(buf)):
                buf[i] = 0
            buf[1] = self.panel.last_err if len(buf) > 1 else 0


def make_display(w=80, h=160):
    _stub_modules()
    import noknok
    panel = VirtualPanel(w, h)
    d = noknok.NoknokDisplay(FakeI2C(panel))
    return d, panel


# ── Checks ───────────────────────────────────────────────────────────────────

def _checks():
    import noknok
    from noknok import Bitmap, WHITE, BLACK, RED, YELLOW, GREEN, icon_names
    fails = []

    def check(name, cond, detail=""):
        print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  " + str(detail)))
        if not cond:
            fails.append(name)

    d, p = make_display()
    print("noknok.py", noknok.__version__)

    # 1. native text sizes match the firmware: size N draws an N px tall cell
    #    (measured on the opaque background box, since glyphs have blank rows).
    for size in (8, 16, 24, 32, 40, 48, 56, 64):
        d.clear(BLACK)
        log_len = len(p.log)
        y_after = d.text("H", size=size, bg=GREEN)
        box_rows = [yy for yy in range(p.h) if any(p.px[yy])]
        check("native %2d px text is %2d px tall" % (size, size),
              box_rows and (box_rows[-1] - box_rows[0] + 1) == size,
              box_rows and (box_rows[-1] - box_rows[0] + 1))
        check("native %2d px uses DRAW_TEXT" % size,
              any(c[0] == 0x03 for c in p.log[log_len:]))
        check("native %2d px returns y below line" % size,
              y_after == size + size // 8, y_after)

    # 2. non-native size goes through the blit and is exact too.
    d.clear(BLACK)
    log_len = len(p.log)
    d.text("H", size=27, bg=GREEN)
    box_rows = [yy for yy in range(p.h) if any(p.px[yy])]
    check("blitted 27 px text is 27 px tall",
          box_rows and (box_rows[-1] - box_rows[0] + 1) == 27)
    check("27 px uses BLIT", any(c[0] == 0x05 for c in p.log[log_len:]))

    # 3. icons: every name renders, right size, blit accepted by the module.
    for name in icon_names():
        d.clear(BLACK)
        p.last_err = 0
        y_after = d.icon(name, x=4, y=4)
        check("icon %-12s draws 16x16 (err=%d)" % (name, p.last_err),
              p.last_err == 0 and p.count(0xFFFF) > 10 and y_after == 20)
    d.clear(BLACK)
    d.icon("wifi", x=0, y=0, scale=3)
    lit_rows = [yy for yy in range(p.h) if any(p.px[yy])]
    check("icon scale=3 -> 48 px tall region", lit_rows[-1] < 48 and lit_rows[0] >= 0)
    d.clear(BLACK)
    d.icon("check", x=70, y=150, size=32)          # hangs off the panel
    check("icon clipped at the edge, no module error", p.last_err == 0)
    try:
        d.icon("nope")
        check("unknown icon raises", False)
    except ValueError as e:
        check("unknown icon raises with the list", "wifi" in str(e))

    # 4. image from a BMP (write a tiny 1-bit BMP: 10x6, bottom-up).
    path = os.path.join(HERE, "_sim_test.bmp")
    rows = ["#........#", ".#......#.", "..#....#..", "..#....#..", ".#......#.", "#........#"]
    w, h = 10, 6
    stride = ((w + 31) // 32) * 4
    pix = bytearray()
    for r in reversed(rows):                       # bottom-up
        b = bytearray(stride)
        for x, ch in enumerate(r):
            if ch == "#":
                b[x >> 3] |= 0x80 >> (x & 7)
        pix += b
    import struct
    off = 14 + 40 + 8
    hdr = b"BM" + struct.pack("<IHHI", off + len(pix), 0, 0, off)
    dib = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 1, 0, len(pix), 2835, 2835, 2, 2)
    pal = bytes([0, 0, 0, 0, 255, 255, 255, 0])    # 0 = black, 1 = white
    with open(path, "wb") as f:
        f.write(hdr + dib + pal + pix)
    bm = Bitmap.from_bmp(path)
    check("bmp loads as 10x6", bm.width == 10 and bm.height == 6)
    check("bmp rows decoded (bright = lit)", bm.rows() == rows, bm.rows())
    # same file, palette swapped (black-on-white) -> auto-inverted
    with open(path, "wb") as f:
        f.write(hdr + dib + bytes([255, 255, 255, 0, 0, 0, 0, 0]) + pix)
    check("bmp black-on-white auto-inverts", Bitmap.from_bmp(path).rows()
          == [r.replace("#", "x").replace(".", "#").replace("x", ".") for r in rows])
    os.remove(path)
    d.clear(BLACK)
    d.image(bm, x=2, y=3)
    check("image() drawn at 2,3", p.dump(2, 3, 10, 6) == "\n".join(rows), p.dump(0, 0, 14, 10))
    d.clear(BLACK)
    d.image(bm, x=0, y=0, w=20)
    check("image(w=20) keeps aspect -> 20x12",
          p.dump(0, 0, 20, 12).split("\n")[0] == "##................##"
          and not any(p.px[12]))

    # 5. print(): cursor, wrap, scroll (no full clear), end="".
    #    Default = the Pico's 8x16 font -> 10 columns at 16 px.
    d.clear(BLACK)
    log_len = len(p.log)
    y1 = d.print("Hello")
    y2 = d.print("World")
    check("print advances 18 px per 16 px line", (y1, y2) == (18, 36), (y1, y2))
    check("print draws text", p.count(0xFFFF) > 0)
    check("print uses the 8x16 blit font by default",
          not any(c[0] == 0x03 for c in p.log[log_len:]))
    d.print("loading", end="")
    d.print(" ok")                                 # "loading ok" = 10 chars = 1 line
    check("print end='' continues the line", d._term_y == 54 and len(d._term) == 3,
          (d._term_y, len(d._term)))
    d.print("a" * 25)                              # wraps onto 3 lines (10 per line)
    check("print wraps a long line", d._term_y == 54 + 3 * 18, d._term_y)
    d.print_native = True
    d.clear(BLACK)
    log_len = len(p.log)
    d.print("Hello World")                         # 5 columns -> "Hello" / "World"
    check("print_native uses DRAW_TEXT, 5 columns",
          any(c[0] == 0x03 for c in p.log[log_len:]) and d._term_y == 36, d._term_y)
    d.print_native = False
    d.clear(BLACK)
    d.print("Hello")
    d.print("World")
    d.print("loading", end="")
    d.print(" ok")
    d.print("a" * 25)
    # fill until it scrolls
    log_len = len(p.log)
    for i in range(6):
        d.print("line", i)
    check("print scrolls without a CLEAR command",
          d._term_y <= 160 and not any(c[0] == 0x01 for c in p.log[log_len:]))
    check("terminal keeps only what fits", sum(d._term_height(e) for e in d._term) <= 160)
    check("last line is at the bottom",
          any(any(p.px[yy]) for yy in range(d._term_y - 18, d._term_y - 2))
          and not any(any(p.px[yy]) for yy in range(d._term_y, p.h)))
    d.clear(BLACK)
    check("clear resets the terminal", d._term == [] and d._term_y == 0)

    # 6. regions
    d.clear(BLACK)
    d.region("val", 0, 40, 80, 32, size=32, color=YELLOW, align="right")
    d.region("net", 60, 0, 20, 16)
    d.set("val", text="12345678")                  # 8 chars * 32 = 256 px, clipped
    check("region text clipped to box", not any(p.px[72]) and not any(p.px[39]))
    d.set("val", text="5")
    yellow = noknok.rgb565(YELLOW)
    check("region right-aligned", p.count(yellow) > 0
          and any(p.px[50][x] == yellow for x in range(48, 80))
          and not any(p.px[50][x] == yellow for x in range(0, 48)))
    d.set("net", icon="wifi")
    check("region icon drawn inside box", p.count(0xFFFF) > 0
          and not any(p.px[yy][x] for yy in range(0, 16) for x in range(0, 60)))
    d.set("net")
    check("set(name) wipes", not any(p.px[yy][x] for yy in range(0, 16) for x in range(60, 80)))
    d.set("val", text="7", bg=RED)
    check("region bg override", p.count(noknok.rgb565(RED)) > 0)
    try:
        d.set("nothere", text="x")
        check("unknown region raises", False)
    except ValueError:
        check("unknown region raises", True)

    # 7. native_text=False forces the blit path
    d.native_text = False
    log_len = len(p.log)
    d.text("A", size=16)
    check("native_text=False uses blit", not any(c[0] == 0x03 for c in p.log[log_len:]))
    d.native_text = True

    print()
    if fails:
        print("FAILED:", ", ".join(fails))
        return 1
    print("all checks passed")
    return 0


def _demo():
    from noknok import BLACK, YELLOW, icon_bitmap, rgb565
    d, p = make_display()
    d.clear(BLACK)
    d.print("Hello World")
    d.print("Temp:", 22.5, "C")
    d.icon("wifi", x=62, y=140)
    d.region("big", 0, 60, 80, 32, size=32, align="center", color=YELLOW)
    d.set("big", text="42")
    d.image(icon_bitmap("heart"), x=4, y=100, w=32)
    print(p.dump(palette={rgb565(YELLOW): "y"}))


if __name__ == "__main__":
    _stub_modules()
    rc = _checks()
    if "--demo" in sys.argv:
        print()
        _demo()
    sys.exit(rc)
