# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev41.py — hardware check of the DEV-41 Display maker API (noknok.py 1.9):
# native text sizes (the v1.9 fix), print(), icons, image, regions. Run from the
# Pi4 bench host:  python3 pico.py run bench_dev41.py 90
#
# Pass = every step prints "ok" with module error 0 and the screen shows what the
# step says. Nothing here needs firmware beyond display v0.2.0.

import time
import board
from noknok import (Conductor, Bitmap, icon_names, __version__,
                    BLACK, WHITE, YELLOW, RED, GREEN, CYAN, NOKNOK)

SDA, SCL = board.GP20, board.GP21      # Pi4RFID bench: PicoHub far port

print("noknok.py", __version__)
c = Conductor(sda=SDA, scl=SCL)
for attempt in (1, 2):
    n = c.enumerate()
    if c.display:
        break
    time.sleep(0.4)
print("enumerated %d, display=%s" % (n, c.display))
if not c.display:
    raise SystemExit("no display on GP20/21")

d = c.display[0]
print("info:", d.info(refresh=True), " fw:", d.firmware_version)
fails = []


def step(name, fn, hold=1.2):
    """Run one step, report the module's error byte, pause so it can be seen."""
    d.clear(BLACK)
    t0 = time.monotonic()
    try:
        r = fn()
        busy, err = d.status()
        ms = int((time.monotonic() - t0) * 1000)
        ok = (err == 0)
        print("%s %-28s %4d ms  err=%s  %s" % ("ok  " if ok else "FAIL", name, ms, err,
                                                "" if r is None else r))
        if not ok:
            fails.append(name)
    except Exception as e:
        print("FAIL %-28s exception: %r" % (name, e))
        fails.append(name)
    time.sleep(hold)


# 1. Native sizes — after the v1.9 fix 16 px must be 16 px tall (was 8).
def s_native():
    y = 0
    for size in (8, 16, 24, 32):
        y = d.text("Hi%d" % size, size=size, y=y, color=WHITE, bg=GREEN)
    return "look: 4 lines, each exactly its size, green boxes touching"
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
    for i, name in enumerate(names):
        d.icon(name, x=4 + (i % 4) * 19, y=4 + (i // 4) * 19, color=CYAN)
    return "%d icons" % len(names)
step("icon grid", s_icons, 2.5)

# 5. Scaled icons + transparent bg.
def s_scaled():
    d.icon("wifi", x=0, y=0, size=24, color=NOKNOK)
    d.icon("wifi", x=28, y=0, size=48, color=NOKNOK)
    d.fill_rect(0, 60, 80, 70, YELLOW)
    d.icon("heart", x=8, y=64, size=64, color=RED, bg=None)
    return "look: 24/48 px wifi, red heart on yellow (transparent)"
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

# 7. Regions: a live value, an icon slot, right/center alignment.
def s_regions():
    d.region("title", 0, 0, 80, 16, size=16, align="center")
    d.region("val", 0, 40, 80, 32, size=32, align="right", color=YELLOW)
    d.region("net", 62, 140, 18, 18)
    d.set("title", text="counter")
    d.set("net", icon="wifi")
    t0 = time.monotonic()
    for n in range(0, 21):
        d.set("val", text=str(n * 5))
    per = int((time.monotonic() - t0) * 1000 / 21)
    d.set("net", icon="check", color=GREEN)
    return "look: counter to 100 right-aligned, no flicker; %d ms/update" % per
step("regions", s_regions, 2.5)

d.clear(BLACK)
d.print("DEV-41", size=24, color=NOKNOK)
d.print("bench done")
d.print(("FAIL: " + ", ".join(fails)) if fails else "all ok", color=RED if fails else GREEN)
print()
print("FAILED:", fails) if fails else print("ALL STEPS OK")
c.i2c.deinit()
