# SPDX-License-Identifier: MIT
# Copyright 2026 noknok (Christopher Houben)
# A: app channel stall. /rpc server + product-like loop; a monitor on the Pi pings /rpc hello meanwhile.
import socketpool, noknok as nk, noknok_rpc as rpc
if not _w.radio.connected:
    print("PROBE join:", _join())
print("PROBE wifi", _w.radio.connected, _w.radio.ipv4_address)
rpc.dispatcher().register("hello", lambda a, m: {"uptime": time.monotonic()})
srv = rpc.start_http(socketpool.SocketPool(_w.radio), 80, print).server
nk.set_service_hook(srv.poll)
feeds = Feeds()
feeds.add("deps", departures, every=20, station="Bern, Bahnhof", limit=15)
feeds.add("wx", weather, every=60, place="Bern", hours=12)
feeds.add("pw", power_price, every=90, zone="CH")
print("PROBE A start epoch %s free %d" % (stamp(), gc.mem_free()))
t0 = time.monotonic(); last = {}
while time.monotonic() - t0 < 200:
    nk._service()
    t = time.monotonic()
    t_before = stamp()
    name = feeds.poll()
    dt = time.monotonic() - t
    if dt > 0.05:
        f = feeds
        print("PROBE fetch at %s took %.2fs  -> %s  err=%s free=%d" % (t_before, dt, name, [feeds.error(n) for n in ("deps", "wx", "pw")], gc.mem_free()))
    time.sleep(0.02)
print("PROBE A done")
