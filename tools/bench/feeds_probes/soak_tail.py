# SPDX-License-Identifier: MIT
# Copyright 2026 noknok (Christopher Houben)
# SOAK: product-like loop, 1 server, refresh 30 s, app server up, 60 minutes
import socketpool, noknok as nk, noknok_rpc as rpc
if not _w.radio.connected:
    print("SOAK join:", _join())
rpc.dispatcher().register("hello", lambda a, m: {"uptime": time.monotonic()})
srv = rpc.start_http(socketpool.SocketPool(_w.radio), 80, print).server
nk.set_service_hook(srv.poll)
feeds = Feeds()
feeds.add("deps", departures, every=30, station="Bern, Bahnhof", limit=15)
DUR = 3600
t0 = time.monotonic(); nxt = t0 + 300
ok = fail = 0; longest = 0.0; slow = 0; errs = {}; minfree = 10**9
print("SOAK start epoch %s free %d" % (stamp(), gc.mem_free()))
while time.monotonic() - t0 < DUR:
    nk._service()
    t = time.monotonic()
    r = feeds.poll()
    dt = time.monotonic() - t
    if r or dt > 0.05:
        if r:
            ok += 1
        else:
            fail += 1
            e = str(feeds.error("deps"))[:28]
            errs[e] = errs.get(e, 0) + 1
        longest = max(longest, dt)
        if dt > 3.0:
            slow += 1
            print("SOAK slow fetch at %s: %.1fs %s" % (stamp(), dt, "ok" if r else feeds.error("deps")))
    gc.collect() if (int(t) % 60 == 0) else None
    minfree = min(minfree, gc.mem_free())
    if time.monotonic() >= nxt:
        nxt += 300
        print("SOAK %4ds  ok %d  fail %d  longest %.1fs  >3s %d  free %d (min %d)  errs %s" % (time.monotonic() - t0, ok, fail, longest, slow, gc.mem_free(), minfree, errs))
    time.sleep(0.02)
print("SOAK FINAL ok %d fail %d longest %.1fs >3s %d min free %d errs %s" % (ok, fail, longest, slow, minfree, errs))
print("SOAK done")
