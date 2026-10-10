# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_display_rotation.py - visual check of all four Display orientations (DEV-42).
# Run: pico.py run, with a person watching the panel. Each rotation shows for HOLD seconds:
#   - a 1-px WHITE frame on all four edges  (nothing cropped, no leftovers from the last picture)
#   - a RED block top-left, a GREEN block bottom-right
#   - the text "Fj7R r=N" upright and not mirrored
# The console prints the geometry the module reports per rotation. Ends in landscape (boot default).
import time
from noknok import Conductor, BLACK, WHITE, RED, GREEN, YELLOW

HOLD = 8
c = Conductor()
c.enumerate()
d = c.display[0]
for r in (0, 1, 2, 3):
    d.rotation(r)
    w, h = d.width, d.height
    print("rotation", r, "->", w, "x", h)
    d.clear(BLACK)
    d.fill_rect(0, 0, w, 1, WHITE); d.fill_rect(0, h - 1, w, 1, WHITE)
    d.fill_rect(0, 0, 1, h, WHITE); d.fill_rect(w - 1, 0, 1, h, WHITE)
    d.fill_rect(2, 2, 10, 10, RED)
    d.fill_rect(w - 12, h - 12, 10, 10, GREEN)
    d.text("Fj7R r=%d" % r, size=8, x=16, y=4, color=YELLOW)
    print("  status", d.status())
    time.sleep(HOLD)
d.rotation(1)
d.clear(BLACK)
d.text("rotation test done", size=8, x=4, y=4, color=WHITE)
print("back to landscape:", d.status())
