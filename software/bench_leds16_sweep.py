# SPDX-License-Identifier: MIT
# bench_leds16_sweep.py - DEV-73 finding, step 2: the Smart Lamp Maxi Sundown
# (colour once, then only SET_BRIGHTNESS 0x03 commands) made single LEDs of the
# LEDs 16x go off-colour, while static frames at the same levels were uniform.
#   F  colour once, then brightness sweeps 43 -> 8 -> 43 every 0.3 s (no I2C)
#   G  the same sweep while the Display is redrawn continuously (I2C load,
#      like the Maxi loop)
#   H  the same fast rate as G (~9/s), NO I2C: rate vs. Pico load
# Watch per phase: do all 16 LEDs keep the same colour? Each phase ends by
# holding the last frame 5 s so a stuck odd LED is easy to spot.
# Run: ./pico.py run bench_leds16_sweep.py 120
import time
from noknok import Conductor, BLACK, WHITE

c = Conductor()
c.enumerate_all()
lamp = c.leds16[0]
d = c.display[0] if c.display else None
if d:
    d.native_text = False
    d.clear(BLACK)

WARM = (160, 80, 0, 95)
SWEEP = list(range(43, 7, -1)) + list(range(8, 44))   # 43..8..43, passes 0x10-0x21

def label(t):
    if d:
        d.fill_rect(0, 0, 160, 32, BLACK)
        d.text(t, size=32, color=WHITE, x=0, y=0)

RUN = "H"            # which phases to run, e.g. "FGH"
PHASES = (("F sweep", False, 0.3), ("G sweep+I2C", True, 0), ("H fast noI2C", False, 0.11))
for name, load, gap in [p for p in PHASES if p[0][0] in RUN]:
    print("[phase]", name)
    label(name)
    lamp.set_brightness(43)
    lamp.set_all(*WARM)
    sends = 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < 25:
        for lv in SWEEP:
            lamp.set_brightness(lv)
            sends += 1
            if load and d:                   # ~the Maxi clock + info redraw
                d.text("%02d:%02d" % (lv, sends % 60), size=40, color=WHITE, x=30, y=36)
            else:
                time.sleep(gap)
            if time.monotonic() - t0 >= 25:
                break
    print("  brightness commands sent:", sends)
    label(name + " hold")
    time.sleep(5)
lamp.off()
label("done")
print("DONE")
