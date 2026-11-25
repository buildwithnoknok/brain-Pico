# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# sim_bench_dev41.py - run software/bench_dev41.py on a desktop Python against
# display_sim's virtual module, so the bench script's layouts and error checks
# can be checked without a rig (DEV-69). It does NOT replace the bench run:
# timings here are PC timings, and it cannot see colours or the real panel.
#
#   python tools/sim_bench_dev41.py              # landscape 160x80 (boot default)
#   python tools/sim_bench_dev41.py 80 160       # portrait
#   python tools/sim_bench_dev41.py 160 80 --dump  # ASCII art of every step

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import display_sim  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
w, h = (int(args[0]), int(args[1])) if len(args) >= 2 else (display_sim.DEFAULT_W,
                                                              display_sim.DEFAULT_H)
d, panel = display_sim.make_display(w, h)
import noknok  # noqa: E402  (stubbed by make_display)


class FakeConductor:
    """Just enough of noknok.Conductor for bench_dev41.py."""
    def __init__(self, *a, **k):
        self.display = [d]

    def enumerate(self):
        return 1

    def deinit(self):
        pass


noknok.Conductor = FakeConductor
time.sleep = lambda s: None                     # no holds between steps

screens = []
_clear = d.clear


def _clear_and_keep(*a, **k):
    screens.append(panel.dump())                # what the step left on screen
    return _clear(*a, **k)


d.clear = _clear_and_keep
path = os.path.join(HERE, "..", "software", "bench_dev41.py")
with open(path, encoding="utf-8") as f:
    src = f.read()
exec(compile(src, "bench_dev41.py", "exec"), {"__name__": "__main__"})
if "--dump" in sys.argv:
    for i, s in enumerate(screens[1:], 1):
        print("---- screen %d" % i)
        print(s)
