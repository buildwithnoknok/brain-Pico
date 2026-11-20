# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev249_product.py - the smallest product with third-party boards, for DEV-249/250 bench runs.
#
# Put on the bench Pico as /data/product.py. It does what a real product with
# third-party boards does (manifest lists a board with "thirdparty": true):
#   enumerate the noknok modules, import noknok_thirdparty, attach() the boards
#   (applies the namings saved by the app), then idle with c.sleep() so /rpc is
#   answered. Two live readings for the app as `info` settings: lux and
#   temperature (None = no reading right now, the app shows a dash).
from noknok import Conductor
import noknok_thirdparty as tp

c = Conductor()
c.enumerate_all()
tp.attach(c)
print("bench dev249: %d board(s), %d unknown" % (len(c.thirdparty), len(c.unknown_devices)))

s = c.settings
s.defaults({"on": True})


def _first(lst, attr):
    try:
        return getattr(lst[0], attr) if lst else None
    except Exception:
        return None


s.info("lux", lambda: _first(c.veml7700, "lux"))
s.info("temperature", lambda: _first(c.tmp102, "temperature"))

while True:
    c.sleep(0.5)
