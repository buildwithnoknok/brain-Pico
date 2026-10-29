# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_http_freeze.py - DEV-170, tier S (host Python, no hardware).
#
# Runs the REAL adafruit_httpserver 4.8.2 Server.poll() (source copy in
# tools/fixtures/adafruit_httpserver, the version the brain ships as .mpy) against
# fake client sockets, once with the library's own receive loops and once with
# noknok_rpc's _SafeServer. Proves:
#   bug     - the library spins forever on a client that closes after a partial request
#             (a fake socket stops it after 2000 reads and reports "spun")
#   fix     - _SafeServer: peer closed (nothing / partial headers / headers + partial body)
#             -> poll() returns at once, the socket is closed, nothing is handled
#   deadline- a client trickling one byte every 0.3 s is dropped after REQUEST_DEADLINE,
#             not held for as long as it keeps trickling
#   normal  - a complete request (one piece, or headers and body apart) is still answered
#
# Run from the repo root:   python -I tools/test_http_freeze.py

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fixtures"))       # adafruit_httpserver 4.8.2
sys.path.insert(0, os.path.join(HERE, "..", "software"))
import types   # noqa: E402
for name in ("microcontroller", "wifi", "socketpool", "mdns"):
    sys.modules.setdefault(name, types.ModuleType(name))

from adafruit_httpserver import Server, Response, POST   # noqa: E402
import noknok_rpc   # noqa: E402

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-64s %s %s" % (name, "PASS" if cond else "FAIL", extra if not cond else ""))
    if not cond:
        fails += 1


class Spun(Exception):
    pass


class Client:
    """A connected client socket as the server sees it. `script`: bytes to deliver,
    a float (that many seconds of silence -> the server's read times out or waits), or
    "CLOSE" (recv_into returns 0 from then on, like a real closed connection)."""
    def __init__(self, script):
        self.script = list(script)
        self.reads = 0
        self.closed = False
        self.sent = b""
        self.timeout = None

    def settimeout(self, t):
        self.timeout = t

    def recv_into(self, buf, n=0):
        self.reads += 1
        if self.reads > 2000:
            raise Spun()
        if not self.script:
            return 0
        step = self.script[0]
        if step == "CLOSE":
            return 0
        if isinstance(step, float):
            limit = self.timeout if self.timeout is not None else step
            if step <= limit:                        # data arrives within this read's wait
                time.sleep(step)
                self.script.pop(0)
                return self.recv_into(buf, n)
            time.sleep(limit)                        # the read times out first
            self.script[0] = step - limit
            raise OSError(116)                       # ETIMEDOUT on CircuitPython
        self.script.pop(0)
        buf[:len(step)] = step
        return len(step)

    def send(self, data):
        self.sent += bytes(data)
        return len(data)

    def sendall(self, data):
        self.send(data)

    def close(self):
        self.closed = True


class Listener:
    def __init__(self, client):
        self.client = client

    def accept(self):
        return self.client, ("192.168.1.50", 40000)


def make(cls):
    srv = cls(types.SimpleNamespace(), debug=False)
    srv.stopped = False
    srv.socket_timeout = 1.0
    handled = []

    @srv.route("/rpc", POST)
    def _rpc(request):
        handled.append(request.body)
        return Response(request, '{"ok": true}', content_type="application/json")
    return srv, handled


def run(cls, script):
    srv, handled = make(cls)
    c = Client(script)
    srv._sock = Listener(c)
    t0 = time.monotonic()
    try:
        res = srv.poll()
    except Spun:
        res = "SPUN"
    except Exception as e:
        res = "raised %r" % (e,)
    return res, time.monotonic() - t0, c, handled


BODY = b'{"id": 1, "op": "hello", "args": {}}'
HEAD = (b"POST /rpc HTTP/1.1\r\nHost: brain\r\nContent-Type: application/json\r\n"
        b"Content-Length: %d\r\n\r\n" % len(BODY))
Safe = noknok_rpc._safe_server_class()

print("DEV-170: the library's own loops (the bug)")
res, dt, c, h = run(Server, [HEAD[:20], "CLOSE"])
check("partial headers + close -> library spins (frozen)", res == "SPUN", res)
res, dt, c, h = run(Server, [HEAD, BODY[:5], "CLOSE"])
check("headers + partial body + close -> library spins (frozen)", res == "SPUN", res)

print("\nnoknok_rpc._SafeServer")
for name, script in (("connect, send nothing, close", ["CLOSE"]),
                     ("partial headers, close", [HEAD[:20], "CLOSE"]),
                     ("headers in two pieces, then close", [HEAD[:30], HEAD[30:60], "CLOSE"])):
    res, dt, c, h = run(Safe, script)
    check("%s -> returns at once, socket closed, nothing handled" % name,
          res != "SPUN" and dt < 0.2 and c.closed and not h, (res, round(dt, 2), c.closed, h))
res, dt, c, h = run(Safe, [HEAD, BODY[:5], "CLOSE"])
check("headers + partial body + close -> no spin, returns at once",
      res != "SPUN" and dt < 1.6 and c.reads < 50, (res, round(dt, 2), c.reads))
trickle = []
for i in range(40):
    trickle += [HEAD[i:i + 1], 0.3]
res, dt, c, h = run(Safe, trickle)
check("client trickling 1 byte / 0.3 s -> dropped after REQUEST_DEADLINE",
      res != "SPUN" and dt <= noknok_rpc.REQUEST_DEADLINE + 0.4 and c.closed and not h,
      (res, round(dt, 2), c.closed))
res, dt, c, h = run(Safe, [3.0])
check("silent client (sends nothing, stays open) -> dropped within the deadline",
      dt <= noknok_rpc.REQUEST_DEADLINE + 0.4 and c.closed and not h, (res, round(dt, 2)))

print("\nnormal requests still answered")
res, dt, c, h = run(Safe, [HEAD + BODY])
check("complete request in one piece -> handled, answered", h == [BODY] and b'"ok": true' in c.sent,
      (res, h))
res, dt, c, h = run(Safe, [HEAD, 0.2, BODY])
check("headers, 0.2 s pause, body -> handled", h == [BODY], (res, h))
res, dt, c, h = run(Safe, [HEAD[:40], HEAD[40:], BODY[:10], BODY[10:]])
check("request in four pieces -> handled", h == [BODY], (res, h))

print("\nthe carrier uses it")
src = open(os.path.join(HERE, "..", "software", "noknok_rpc.py"), encoding="utf-8").read()
check("HttpCarrier.start() builds _safe_server_class(), not the plain Server",
      "_safe_server_class()(pool" in src and "srv = Server(pool" not in src)

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
