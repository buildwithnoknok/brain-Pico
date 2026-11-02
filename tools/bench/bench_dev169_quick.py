#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev169_quick.py IP [N] - DEV-169: N plain requests one after another (hello, 0.5 s
# apart), then status - the answer time of each, nothing else on the network. The quick
# "is the app channel itself slow?" check before any load test.
import json, socket, sys, time

IP = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 25


def one(op, timeout=4):
    b = json.dumps({"id": 1, "op": op}).encode()
    t = time.monotonic()
    try:
        s = socket.create_connection((IP, 80), timeout=timeout)
        s.sendall(b"POST /rpc HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(b) + b)
        d = b""
        while True:
            c = s.recv(4096)
            if not c:
                break
            d += c
        s.close()
        return round((time.monotonic() - t) * 1000), d
    except Exception as e:
        return round((time.monotonic() - t) * 1000), None


ms = []
for i in range(N):
    t, d = one("hello")
    ms.append(t if d else None)
    time.sleep(0.5)
ok = sorted(x for x in ms if x is not None)
print("hello x%d: answered %d, median %s ms, max %s ms, all: %s"
      % (N, len(ok), ok[len(ok) // 2] if ok else None, ok[-1] if ok else None, ms))
t, d = one("status", 8)
link = None
if d:
    try:
        link = json.loads(d.split(b"\r\n\r\n", 1)[1]).get("result", {}).get("link")
    except Exception:
        pass
print("status: %d ms, %s bytes, link %s" % (t, len(d) if d else None, link))
