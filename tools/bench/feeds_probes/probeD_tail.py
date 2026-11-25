# SPDX-License-Identifier: MIT
# Copyright 2026 noknok (Christopher Houben)
# D: Wi-Fi lost mid-run and recovery
import adafruit_connection_manager as _cm
if not _w.radio.connected:
    print("PROBE join:", _join())
feeds = Feeds()
feeds.add("deps", departures, every=10, station="Bern, Bahnhof", limit=15)
t_run = time.monotonic()
def attempt(phase, wait=10):
    feeds.refresh("deps")
    t = time.monotonic(); gc.collect()
    r = feeds.poll()
    dt = time.monotonic() - t
    print("PROBE %-12s t=%3ds  %-5s %.2fs  err=%s  free=%d" % (phase, time.monotonic() - t_run, "OK" if r else "FAIL", dt, feeds.error("deps"), gc.mem_free()))
    time.sleep(wait)
    return bool(r)
for i in range(4):
    attempt("before")
print("PROBE radio OFF now")
_w.radio.enabled = False
for i in range(4):
    attempt("radio-off")
print("PROBE radio ON now")
_w.radio.enabled = True
print("PROBE join took", _join(), "connected", _w.radio.connected)
ok = False
for i in range(6):
    ok = attempt("after-no-reset", 6)
    if ok: break
if not ok:
    print("PROBE session reset (close_all + new Session)")
    try:
        _cm.connection_manager_close_all(release_references=True)
    except Exception as e:
        print("PROBE close_all err", repr(e))
    _session[0] = None
    gc.collect()
    for i in range(5):
        if attempt("after-reset", 6):
            break
else:
    for i in range(3):
        attempt("steady", 10)
print("PROBE D done")
