# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# dev169_server_trace.py - DEV-169, runs ON the brain (pico.py run, product stopped): starts
# the /rpc carrier alone (hello only) and polls it every 10 ms for 40 s, printing one line
# whenever the server's state changes: open connections reading / replying, bytes received,
# reply bytes sent, poll result, poll duration. Send requests from the Pi meanwhile
# (bench_dev169_quick.py) and read where the time goes. Prints no credentials.
import json, time
import wifi, socketpool
import noknok_rpc as rpc

try:
    c = json.load(open("/data/wifi.json"))
except OSError:
    c = json.load(open("/wifi.json"))
if not wifi.radio.connected:
    wifi.radio.connect(c["ssid"], c["password"], timeout=15)
print("ip", wifi.radio.ipv4_address, "rpc", rpc.__version__)
rpc.dispatcher().register("hello", lambda a, m: {"uptime": time.monotonic()})
srv = rpc.start_http(socketpool.SocketPool(wifi.radio), 80, print).server
t_start = time.monotonic()
last = None
while time.monotonic() - t_start < 40:
    t0 = time.monotonic_ns()
    try:
        r = srv.poll()
    except Exception as e:
        r = "EXC %r" % (e,)
    dt = (time.monotonic_ns() - t0) // 1000
    pend = srv._noknok_pending or []
    out = srv._noknok_out or []
    st = ([len(p[2]) for p in pend], [(o[2], len(o[1])) for o in out], r)
    if st != last or dt > 20000:
        print("%8.3f poll %5d us  reading %s  replying %s  -> %s"
              % (time.monotonic() - t_start, dt, st[0], st[1], r))
        last = st
    time.sleep(0.01)
print("done")
