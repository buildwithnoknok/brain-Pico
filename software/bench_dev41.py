# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev41.py — hardware check of the DEV-41 Display maker API (noknok.py 1.9):
# native text sizes (the v1.9 fix), print(), icons, image, regions. Run from the
# Pi4 bench host:  python3 pico.py run bench_dev41.py 90
#
# Pass = every step prints "ok" with no module error and the screen shows what
# the step says. Geometry is read from the module, so it runs on the boot default
# (landscape 160x80, display fw 0.6.0+) and on portrait 80x160 alike; the layouts
# below adapt to d.width / d.height.
# Desk check without hardware (DEV-69): python tools/sim_bench_dev41.py [w h]
# runs this file against tools/display_sim.py's virtual module.
#
# Error byte: on display fw 0.7.1+ (DEV-44) it is READ-TO-CLEAR, and the driver
# reads it in wait_ready() before every draw and drops it. So a plain d.status()
# after a step only shows the step's LAST command. ErrorTap below records every
# error byte any read sees, so each step reports all its errors. (On fw <= 0.7.0
# the byte was sticky: start from a freshly power-cycled module there.)
# Error 4 mid-run = blit data without a blit (the old lost-command race, DEV-41).

import time
import board
from noknok import (Conductor, Bitmap, icon_names, __version__,
                    BLACK, WHITE, YELLOW, RED, GREEN, CYAN, NOKNOK)

# Pins come from settings.toml (DEV-74: no pins in code); the display may sit on
# bus A or bus B.
print("noknok.py", __version__)
c = Conductor()
for attempt in (1, 2):
    n = c.enumerate()
    if c.display:
        break
    time.sleep(0.4)
print("enumerated %d, display=%s" % (n, c.display))
if not c.display:
    raise SystemExit("no display on the I2C buses in settings.toml")

d = c.display[0]
print("info:", d.info(refresh=True), " fw:", d.firmware_version)
W, H = d.width, d.height
LANDSCAPE = W > H
print("geometry %dx%d (%s)" % (W, H, "landscape" if LANDSCAPE else "portrait"))
fails = []


class ErrorTap:
    """Wrap d._read_raw so every non-zero error byte the driver reads (its own
    wait_ready() polls included) is kept. Bench-only; the driver is untouched."""
    def __init__(self, disp):
        self.seen = []
        self._raw = disp._read_raw
        disp._read_raw = self._read

    def _read(self, n=2):
        buf = self._raw(n)
        if buf is not None and n == 2 and buf[1]:
            self.seen.append(buf[1])
        return buf

    def take(self):
        seen, self.seen = self.seen, []
        return seen


tap = ErrorTap(d)


def step(name, fn, hold=1.2):
    """Run one step, report the module's error byte, pause so it can be seen."""
    d.clear(BLACK)
    d.wait_ready()
    tap.take()                                   # errors from before this step
    t0 = time.monotonic()
    try:
        r = fn()
        d.wait_ready()
        ms = int((time.monotonic() - t0) * 1000)
        busy, err = d.status()                   # also lands in the tap
        errs = tap.take()
        ok = (err is not None) and not errs
        print("%s %-28s %4d ms  err=%s  %s" % ("ok  " if ok else "FAIL", name, ms,
                                                errs or err, "" if r is None else r))
        if not ok:
            fails.append(name)
    except Exception as e:
        print("FAIL %-28s exception: %r" % (name, e))
        fails.append(name)
    time.sleep(hold)


# 1. Native sizes — after the v1.9 fix 16 px must be 16 px tall (was 8).
#    Portrait: one column. Landscape (80 px tall): 8/16/24 left, 32 top right.
def s_native():
    x = y = 0
    for size in (8, 16, 24, 32):
        if y + size > H:                         # no room below: next column
            x, y = W // 2, 0
        y = d.text("%d" % size, size=size, x=x, y=y, color=WHITE, bg=GREEN)
    return "look: 8/16/24/32 each exactly its height, green boxes, nothing cut off"
step("native 8/16/24/32", s_native, 2.5)

# 2. Blitted (non-native) size + the Pico 8x16 font.
step("blit 20px + native=False",
     lambda: (d.text("Twenty", size=20, color=CYAN),
              d.text("8x16 font", size=16, y=30, native=False))[1], 2.0)

# 3. print() as a terminal, then scroll.
def s_print():
    d.print("Hello World")
    d.print("Temp:", 22.5, "C")
    d.print("loading", end="")
    d.print(" ok")
    for i in range(1, 10):
        d.print("line", i)
        time.sleep(0.05)
    return "look: scrolled terminal, no flash"
step("print + scroll", s_print, 2.5)

# 4. Every icon in a grid.
def s_icons():
    names = icon_names()
    cols = (W - 4) // 19
    for i, name in enumerate(names):
        d.icon(name, x=4 + (i % cols) * 19, y=4 + (i // cols) * 19, color=CYAN)
    return "%d icons, %d per row, none cut off" % (len(names), cols)
step("icon grid", s_icons, 2.5)

# 5. Scaled icons + transparent bg.
def s_scaled():
    d.icon("wifi", x=0, y=0, size=24, color=NOKNOK)
    d.icon("wifi", x=28, y=0, size=48, color=NOKNOK)
    bx, by = (80, 0) if LANDSCAPE else (0, 60)   # yellow box right / below
    d.fill_rect(bx, by, 80, 80, YELLOW)
    d.icon("heart", x=bx + 8, y=by + 8, size=64, color=RED, bg=None)
    return "look: 24/48 px wifi, whole red heart on yellow (transparent)"
step("icon scale + transparent", s_scaled, 2.5)

# 6. image() from an in-memory Bitmap (ASCII art) resized to 64 wide.
ART = ["#......#......#.",
       ".#....###....#..",
       "..#..#####..#...",
       "...#..###..#....",
       "....#..#..#.....",
       ".....#####......",
       "......###.......",
       ".......#........"]
step("image (ASCII art x4)",
     lambda: d.image(Bitmap.from_rows(ART), x=8, y=20, w=64, color=WHITE), 2.0)

# 6b. A real 1-bit .bmp decoded ON THE PICO (built in RAM, no file write needed):
#     exercises struct/seek/classmethod on CircuitPython and the size cap.
def s_bmp():
    import io, struct
    rows = ["#........#", ".#......#.", "..#....#..", "..#....#..", ".#......#.", "#........#"]
    w, h = 10, 6
    stride = ((w + 31) // 32) * 4
    pix = bytearray()
    for r in reversed(rows):                              # BMP rows are bottom-up
        b = bytearray(stride)
        for x, ch in enumerate(r):
            if ch == "#":
                b[x >> 3] |= 0x80 >> (x & 7)
        pix += b
    off = 14 + 40 + 8
    hdr = b"BM" + struct.pack("<IHHI", off + len(pix), 0, 0, off)
    dib = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 1, 0, len(pix), 2835, 2835, 2, 2)
    pal = bytes([0, 0, 0, 0, 255, 255, 255, 0])
    bm = Bitmap.from_bmp(io.BytesIO(hdr + dib + pal + pix))
    if bm.rows() != rows:
        raise ValueError("bmp rows decoded wrong: %r" % bm.rows())
    big = struct.pack("<IiiHHIIiiII", 40, 4000, 4000, 1, 1, 0, 0, 0, 0, 2, 2)
    try:
        Bitmap.from_bmp(io.BytesIO(hdr + big + pal + pix))
        raise ValueError("size cap did not trigger")
    except ValueError as e:
        if "too big" not in str(e):
            raise
    d.image(bm, x=0, y=0, w=80, color=CYAN)               # 10x6 -> 80x48 (fits both)
    return "bmp decoded + size cap ok; look: cyan X"
step("bmp decode on Pico", s_bmp, 2.0)

# 7. Regions: a live value, an icon slot, right/center alignment.
def s_regions():
    d.region("title", 0, 0, W, 16, size=16, align="center")
    d.region("val", 0, 40, W - 20, 32, size=32, align="right", color=YELLOW)
    d.region("net", W - 18, H - 18, 18, 18)     # bottom-right corner
    d.set("title", text="counter")
    d.set("net", icon="wifi")
    t0 = time.monotonic()
    for n in range(0, 21):
        d.set("val", text=str(n * 5))
    per = int((time.monotonic() - t0) * 1000 / 21)
    d.set("net", icon="check", color=GREEN)
    return "look: counter to 100 right-aligned, icon bottom right, no flicker; %d ms/update" % per
step("regions", s_regions, 2.5)

d.clear(BLACK)
d.print("DEV-41", size=24, color=NOKNOK)
d.print("bench done")
d.print(("FAIL: " + ", ".join(fails)) if fails else "all ok", color=RED if fails else GREEN)
print()
print("FAILED:", fails) if fails else print("ALL STEPS OK")
c.deinit()
