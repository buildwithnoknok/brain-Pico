# SPDX-License-Identifier: MIT
# bench_leds16_lowlevel.py - DEV-73 finding: does the LEDs 16x stay uniform at
# low brightness? Separates "low-PWM die spread" from "data-line corruption".
# Five 15-s phases, each named on the noknok Display. Watch per phase: are all
# 16 LEDs the same colour, and does anything change while the phase holds?
#   A  warm white, level 43, sent once           (static)
#   B  warm white, level 43, re-sent every 0.5 s (does a refresh corrupt LEDs?)
#   C  warm white, level 8,  sent once
#   D  warm white, level 8,  re-sent every 0.5 s
#   E  W channel only, level 8, sent once        (white die alone, no RGB mix)
# Run: ./pico.py run bench_leds16_lowlevel.py 120
import time
from noknok import Conductor, BLACK, WHITE

c = Conductor()
c.enumerate_all()
lamp = c.leds16[0]
d = c.display[0] if c.display else None
if d:
    d.native_text = False
    d.clear(BLACK)

WARM = (160, 80, 0, 95)      # warm white as Smart Lamp Maxi sends it (RGB + W)
PHASES = [("A 43 once", 43, WARM, False), ("B 43 resend", 43, WARM, True),
          ("C 8 once", 8, WARM, False), ("D 8 resend", 8, WARM, True),
          ("E 8 W only", 8, (0, 0, 0, 255), False)]

for name, level, rgbw, resend in PHASES:
    print("[phase]", name)
    if d:
        d.fill_rect(0, 30, 160, 32, BLACK)
        d.text(name, size=32, color=WHITE, x=0, y=30)
    lamp.set_brightness(level)
    lamp.set_all(*rgbw)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 15:
        if resend:
            lamp.set_brightness(level)
            lamp.set_all(*rgbw)
        time.sleep(0.5)
    try:
        print("  status:", lamp.status())
    except Exception as e:
        print("  status failed:", e)
lamp.off()
if d:
    d.fill_rect(0, 30, 160, 32, BLACK)
    d.text("done", size=32, color=WHITE, x=0, y=30)
print("DONE")
