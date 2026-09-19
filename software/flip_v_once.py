# flip_v_once.py — is the vertical mirror (MADCTL MY bit) honoured by this panel?
# Draws a red square + up-arrow + "TOP" at LOGICAL (0,0) in the mirrored
# orientation and nothing else. Physically at the top  = MY ignored.
# Physically at the bottom (upside-down) = mirror works. Revert: d.rotation(0).
import board, time
from noknok import Conductor, BLACK, WHITE, RED, GREEN

c = Conductor(sda=board.GP20, scl=board.GP21)
for _ in range(2):
    c.enumerate()
    if c.display:
        break
    time.sleep(0.4)
d = c.display[0]

d.rotation(0)                                   # known-good boot orientation
d.clear(BLACK); d.clear(BLACK)
# Vertical mirror: boot MADCTL 0xC0 (MX|MY) -> 0x40 (MX only), offset (24,0), 80x160.
d.orient(0x40, 24, 0, 80, 160)
w, h = d.width, d.height
d.fill_rect(0, 0, w, 2, WHITE); d.fill_rect(0, h - 2, w, 2, WHITE)   # top/bottom bars
d.fill_rect(2, 2, 12, 12, RED)                                        # red = logical top-left
d.icon("arrow_up", x=32, y=4, size=32, color=GREEN)
d.text("TOP", size=16, x=20, y=40, color=WHITE)
d.text("bottom", size=8, x=16, y=h - 12, color=WHITE)
print("mirrored: MADCTL 0x40 off(24,0) %dx%d. Where is the RED square: physical top or bottom?" % (w, h))
c.i2c.deinit()
