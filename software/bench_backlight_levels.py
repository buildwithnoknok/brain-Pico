# SPDX-License-Identifier: MIT
# bench_backlight_levels.py - find the lowest still-visible backlight level of
# the noknok Display (Smart Lamp Maxi night mode, DEV-73). Shows each raw level
# 0-255 big on screen for 4 s, lowest first. Note the first number you can read.
# Run: ./pico.py run bench_backlight_levels.py 90 ; then ./pico.py reset
import time
from noknok import Conductor, BLACK, WHITE

c = Conductor()
c.enumerate()
d = c.display[0]
d.native_text = False
d.clear(BLACK)
LEVELS = (1, 2, 3, 4, 6, 8, 12, 16, 24)
for raw in LEVELS:
    d.backlight(raw / 255)           # a fraction: backlight(1) would be 100 %
    d.fill_rect(0, 16, 160, 48, BLACK)
    d.text("%d" % raw, size=48, color=WHITE, x=60, y=16)
    print("backlight raw", raw)
    time.sleep(4)
d.backlight(0.6)
d.fill_rect(0, 16, 160, 48, BLACK)
d.text("done", size=48, color=WHITE, x=32, y=16)
print("DONE")
