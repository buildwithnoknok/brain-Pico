# SPDX-License-Identifier: MIT
# Copyright 2026 noknok (Christopher Houben)
import time, urllib.request, json, sys
ip, dur = sys.argv[1], float(sys.argv[2])
int(len(sys.argv))
end = time.time() + dur
n = 0
while time.time() < end:
    t0 = time.time()
    try:
        req = urllib.request.Request("http://%s/rpc" % ip, data=b'{"id":1,"op":"hello","args":{}}', method="POST")
        urllib.request.urlopen(req, timeout=4).read()
        res = "ok"
    except Exception as e:
        res = "ERR " + type(e).__name__
    dt = time.time() - t0
    print("MON %.2f %.3f %s" % (t0, dt, res), flush=True)
    time.sleep(max(0, float(sys.argv[3]) - dt))
