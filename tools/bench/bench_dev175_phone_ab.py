#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev175_phone_ab.py IP LABEL [MINUTES] [OUTFILE] - DEV-175/DEV-169: how often does the
# brain's product loop stall while a REAL phone has the app's settings page open? A person keeps
# the page open (and presses the LED Button now and then) for MINUTES (default 10); this script
# only watches: every 30 s it reads `status.link` (poll_max_ms resets on every read, so each line
# is the longest single hold in that 30 s window). Run once per arm (e.g. LABEL=mdns1, mdns0)
# and compare: windows with a hold >= 5 s are the DEV-175 stall (send() stuck in CircuitPython).
# One JSON line per window to OUTFILE (default ~/dev175_ab.jsonl).
import json, os, socket, sys, time

IP, LABEL = sys.argv[1], sys.argv[2]
MIN = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
OUT = os.path.expanduser(sys.argv[4] if len(sys.argv) > 4 else "~/dev175_ab.jsonl")


def status():
    b = json.dumps({"id": 1, "op": "status", "args": {}}).encode()
    try:
        c = socket.create_connection((IP, 80), timeout=15)
        c.sendall(b"POST /rpc HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  b"Content-Length: %d\r\n\r\n" % len(b) + b)
        d = b""
        while True:
            x = c.recv(4096)
            if not x:
                break
            d += x
        c.close()
        return json.loads(d.split(b"\r\n\r\n", 1)[1])
    except Exception as e:
        return {"error": repr(e)}


first = status()                       # resets poll_max_ms: the arm starts clean
print("%s: start, slow_polls %s" % (LABEL, (first.get("link") or {}).get("slow_polls")), flush=True)
end = time.time() + MIN * 60
stalls = windows = 0
worst = 0
while time.time() < end:
    time.sleep(30)
    r = status()
    link = r.get("link") or {}
    pm = link.get("poll_max_ms")
    windows += 1
    if pm is not None:
        worst = max(worst, pm)
        stalls += pm >= 5000
    rec = {"arm": LABEL, "t": time.strftime("%H:%M:%S"), "poll_max_ms": pm,
           "slow_polls": link.get("slow_polls"), "polls": link.get("polls"),
           "cut": link.get("cut_replies"), "mem_free": r.get("mem_free"), "err": r.get("error")}
    with open(OUT, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print("  %s hold max %s ms%s" % (rec["t"], pm, "  <-- STALL" if pm and pm >= 5000 else ""),
          flush=True)
print("%s: %d windows, %d with a hold >= 5 s, longest %d ms" % (LABEL, windows, stalls, worst))
