# SPDX-License-Identifier: MIT
# Copyright 2026 noknok (Christopher Houben)


# ----------------------------- probe common (appended to the library source) -----------------------------
import wifi as _w, os
def _lfb():
    gc.collect()
    lo, hi = 0, min(65536, gc.mem_free())
    while lo < hi:
        mid = (lo + hi + 1) // 2
        try:
            b = bytearray(mid); del b; lo = mid
        except MemoryError:
            hi = mid - 1
    return lo
def _join():
    """reconnect from the brain's own /data/wifi.json like the bench scripts (nothing printed)"""
    try:
        try:
            c = json.load(open("/data/wifi.json"))
        except OSError:
            c = json.load(open("/wifi.json"))
        t = time.monotonic()
        _w.radio.connect(c["ssid"], c["password"], timeout=20)
        c = None
        return time.monotonic() - t
    except Exception as e:
        return "failed %s" % type(e).__name__
def stamp():
    return "%d.%02d" % (int(time.time()), int((time.time() % 1) * 100))
