# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# flip_v_once.py — the orientation recipe for the Pi4RFID bench display (a rev 1.0
# "180° rework" board): boot MADCTL 0xC0 with offset (24,0); MADCTL 0x40 = same
# picture mirrored vertically. Christopher chose 0x40 on 19 Sep 2026 ("keep it").
# It is RUNTIME state — a power cycle returns the module to its firmware default.
# Draws a red square + up-arrow + "TOP" at logical (0,0) so top/bottom is obvious,
# and prints the module's sticky error byte after every step (a non-zero value
# that appears mid-run = the I2C lost-command race, see DEV-41).
import board, time
from noknok import Conductor, BLACK, WHITE, RED, GREEN

c = Conductor(sda=board.GP20, scl=board.GP21)
for _ in range(2):
    c.enumerate()
    if c.display:
        break
    time.sleep(0.4)
d = c.display[0]

def probe(label):
    print("%-30s err=%s" % (label, d.status()[1]))

probe("start")
d.rotation(0);                                            probe("rotation(0)")
d.clear(BLACK); d.clear(BLACK);                           probe("clear x2")
d.orient(0x40, 24, 0, 80, 160);                           probe("orient 0x40 (24,0)")
w, h = d.width, d.height
d.fill_rect(0, 0, w, 2, WHITE); d.fill_rect(0, h - 2, w, 2, WHITE)
d.fill_rect(2, 2, 12, 12, RED);                           probe("bars + red square")
d.icon("arrow_up", x=32, y=4, size=32, color=GREEN);      probe("icon arrow (blit)")
d.text("TOP", size=16, x=20, y=40, color=WHITE)
d.text("bottom", size=8, x=16, y=h - 12, color=WHITE);    probe("text")
print("MADCTL 0x40 off(24,0) %dx%d — red square = logical top-left" % (w, h))
c.i2c.deinit()
