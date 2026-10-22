# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_display_fallback.py - DEV-108 host test: a missed GET_INFO reply must not
# give a landscape product portrait geometry (Smart Lamp Maxi crashed with
# "region 'bell' is off the panel" after a disturbed cold boot).
#
#   python tools/test_display_fallback.py      -> "N/N passed", exit 1 on a failure
import sys
from display_sim import FakeI2C, VirtualPanel, _stub_modules

_stub_modules()
import noknok

fails = []
def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)

class FlakyI2C(FakeI2C):
    """GET_INFO (0x15) goes unanswered `drop` times (reads come back all zero)."""
    def __init__(self, panel, drop):
        FakeI2C.__init__(self, panel)
        self.drop = drop
        self.asked = 0
    def writeto(self, addr, data):
        FakeI2C.writeto(self, addr, data)
        if bytes(data)[:1] == b"\x15":
            self.asked += 1
            if self.drop > 0:
                self.drop -= 1
                self._pending = None          # the reply is lost

def make(drop, w=160, h=80):
    panel = VirtualPanel(w, h)
    bus = FlakyI2C(panel, drop)
    return noknok.NoknokDisplay(bus), bus

# 1. one lost reply: the retry inside info() gets the real geometry
d, bus = make(1)
check("one lost GET_INFO reply -> real 160x80", (d.width, d.height) == (160, 80))
check("  ... asked twice", bus.asked == 2)

# 2. the module never answers: fallback = boot geometry, the Maxi layout works
d, bus = make(99)
check("no GET_INFO answer -> fallback 160x80 (landscape boot)", (d.width, d.height) == (160, 80))
try:
    d.region("bell", 144, 46, 16, 16)          # Smart Lamp Maxi _layout_home()
    check("Maxi region 'bell' at x=144 accepted on the fallback", True)
except ValueError as e:
    check("Maxi region 'bell' at x=144 accepted on the fallback (%s)" % e, False)
n = bus.asked
for _ in range(20):
    d.width
check("failed GET_INFO is not re-asked on every draw (back-off)", bus.asked == n)

# 3. the module comes back after the back-off: the real geometry is used
d, bus = make(3)
d.width                                         # 3 tries, all lost
d._info_next = 0                                # back-off over
check("module answers later -> real geometry", (d.width, d.height) == (160, 80) and d.info() is not None)

# 4. rotation(PORTRAIT) with the module silent: fallback follows the rotation
d, bus = make(99)
d.rotation(noknok.PORTRAIT)
check("rotation(PORTRAIT), no answer -> fallback 80x160", (d.width, d.height) == (80, 160))
d.rotation("landscape_flipped")
check("rotation(LANDSCAPE_FLIPPED), no answer -> fallback 160x80", (d.width, d.height) == (160, 80))

# 5. a stale size is not kept after a failed refresh (rotation changed the panel)
d, bus = make(0)
d.width                                         # cached 160x80
bus.drop = 99
d.rotation(noknok.PORTRAIT)
check("refresh fails after rotation -> 80x160, not the stale 160x80", (d.width, d.height) == (80, 160))

total = 9
print("%d/%d passed" % (total - len(fails), total))
sys.exit(1 if fails else 0)
