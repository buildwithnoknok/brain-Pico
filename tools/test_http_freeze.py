# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_http_freeze.py - DEV-170 + DEV-169, tier S (host Python, no hardware).
#
# Runs the REAL adafruit_httpserver 4.8.2 (source copy in tools/fixtures/adafruit_httpserver,
# the version the brain ships as .mpy) against fake client sockets that deliver their bytes
# on a wall-clock schedule, once with the library's own poll() and once with noknok_rpc's
# _SafeServer. poll() is called every 10 ms like service() does; the number that matters
# is the LONGEST SINGLE poll() call - that is how long the product loop (button, LEDs) is held.
#   bug     - the library spins forever on a client that closes after a partial request
#             (DEV-170) and holds one poll() for as long as a late body takes (DEV-169)
#   fix     - _SafeServer: no poll() longer than a few ms in any case; closed / too slow
#             connections are dropped and closed; complete requests are answered, also
#             when the body trails the headers by 1.5 s, also two clients at once
#   reply   - sent the library's way, each send() bounded by SEND_TIMEOUT
#   stats   - service() records poll_max_ms / slow_polls for status.link
#
# Run from the repo root:   python -I tools/test_http_freeze.py
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fixtures"))       # adafruit_httpserver 4.8.2
sys.path.insert(0, os.path.join(HERE, "..", "software"))
for name in ("microcontroller", "wifi", "socketpool", "mdns"):
    sys.modules.setdefault(name, types.ModuleType(name))

from adafruit_httpserver import Server, Response, POST   # noqa: E402
import noknok_rpc   # noqa: E402

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-66s %s %s" % (name, "PASS" if cond else "FAIL", extra if not cond else ""))
    if not cond:
        fails += 1


class Spun(Exception):
    pass


class Client:
    """A connected client socket as the server sees it. `script`: (seconds after accept,
    bytes or "CLOSE"). Non-blocking reads (timeout 0) get EAGAIN until the next piece is
    due; blocking reads wait for it up to the timeout, then ETIMEDOUT (116) - like
    CircuitPython. After "CLOSE" every read returns 0, like a real closed connection."""
    def __init__(self, script, take=None, every=0.0):
        self.take, self.every, self.last_take = take, every, -1e9   # receive window
        self.script = list(script)
        self.t0 = None
        self.reads = 0
        self.closed = False
        self.closed_at = None
        self.sent = b""
        self.timeout = None
        self.timeout_at_send = "never sent"

    def settimeout(self, t):
        self.timeout = t

    def setblocking(self, flag):
        self.timeout = None if flag else 0

    def recv_into(self, buf, n=0):
        self.reads += 1
        if self.reads > 20000:
            raise Spun()
        if not self.script:
            if self.timeout == 0:
                raise OSError(11)
            time.sleep(self.timeout or 0)
            raise OSError(116)
        due, step = self.script[0]
        wait = self.t0 + due - time.monotonic()
        if wait > 0:
            if self.timeout == 0:
                raise OSError(11)                       # EAGAIN: nothing yet
            if self.timeout is not None and wait > self.timeout:
                time.sleep(self.timeout)
                raise OSError(116)                      # ETIMEDOUT on CircuitPython
            time.sleep(wait)
        if step == "CLOSE":
            return 0
        self.script.pop(0)
        buf[:len(step)] = step
        return len(step)

    def send(self, data):
        """`take` bytes per `every` s, EAGAIN in between (a slow reader on a weak
        link); take=0: never reads (always EAGAIN); None: takes everything."""
        if self.timeout_at_send == "never sent":
            self.timeout_at_send = self.timeout
        n = len(data)
        if self.take is not None:
            wait = self.last_take + self.every - time.monotonic()
            if self.take == 0 or wait > 0:
                if self.timeout == 0:
                    raise OSError(11)                     # non-blocking: not now
                limit = self.timeout or 1.0
                if self.take == 0 or wait > limit:
                    time.sleep(limit)                     # blocking: nothing in time
                    raise OSError(116)
                time.sleep(wait)                          # blocking: room frees up
            self.last_take = time.monotonic()
            n = min(n, self.take)
        self.sent += bytes(data[:n])
        return n

    def close(self):
        if not self.closed:
            self.closed, self.closed_at = True, time.monotonic()


class Listener:
    """The listening socket: hands out each client when its arrival time has come."""
    def __init__(self, clients):
        self.queue = list(clients)                      # (arrival s, Client)
        self.t0 = time.monotonic()

    def accept(self):
        if self.queue and time.monotonic() >= self.t0 + self.queue[0][0]:
            at, c = self.queue.pop(0)
            c.t0 = time.monotonic()
            return c, ("192.168.1.50", 40000)
        raise OSError(11, "try again")


def make(cls):
    srv = cls(types.SimpleNamespace(), debug=False)
    srv.stopped = False
    srv.socket_timeout = 1.0
    handled = []

    @srv.route("/rpc", POST)
    def _rpc(request):
        handled.append(bytes(request.body))
        return Response(request, '{"ok": true}', content_type="application/json")
    return srv, handled


def run(cls, clients, limit=4.0, every=0.01):
    """Poll every 10 ms until every client is closed (or `limit` s). Returns the longest
    single poll() in ms (or "SPUN"), the clients, what was handled, and when."""
    srv, handled = make(cls)
    lst = Listener(clients)
    srv._sock = lst
    t_start = time.monotonic()
    longest = 0.0
    when = []
    cs = [c for _, c in clients]
    while time.monotonic() - t_start < limit and not all(c.closed for c in cs):
        t0 = time.monotonic()
        try:
            srv.poll()
        except Spun:
            return "SPUN", cs, handled, when
        except OSError:
            pass                                        # HttpCarrier.poll() swallows 110/116
        dt = (time.monotonic() - t0) * 1000
        longest = max(longest, dt)
        while len(when) < len(handled):
            when.append(time.monotonic() - t_start)
        time.sleep(every)
    return round(longest), cs, handled, when


BODY = b'{"id": 1, "op": "hello", "args": {}}'
HEAD = (b"POST /rpc HTTP/1.1\r\nHost: brain\r\nContent-Type: application/json\r\n"
        b"Content-Length: %d\r\n\r\n" % len(BODY))
Safe = noknok_rpc._safe_server_class()
DL = noknok_rpc.REQUEST_DEADLINE
FAST = int(noknok_rpc.FIRST_WAIT * 1000) + 20          # ms: one pass may wait FIRST_WAIT


def one(script, cls=Safe, limit=4.0):
    longest, cs, h, when = run(cls, [(0, Client(script))], limit)
    return longest, cs[0], h, when


print("DEV-170 / DEV-169: the library's own poll() (the bugs)")
longest, c, h, w = one([(0, HEAD[:20]), (0.1, "CLOSE")], Server)
check("partial headers + close -> library spins (frozen)", longest == "SPUN", longest)
longest, c, h, w = one([(0, HEAD), (0, BODY[:5]), (0.1, "CLOSE")], Server)
check("headers + partial body + close -> library spins (frozen)", longest == "SPUN", longest)
longest, c, h, w = one([(0, HEAD), (0.6, BODY)], Server)
check("body 0.6 s after the headers -> one poll() holds the loop >= 0.5 s",
      longest != "SPUN" and longest >= 500, longest)

print("\nnoknok_rpc._SafeServer: broken clients")
for name, script in (("connect, send nothing, close", [(0.1, "CLOSE")]),
                     ("partial headers, close", [(0, HEAD[:20]), (0.3, "CLOSE")]),
                     ("headers in two pieces, then close", [(0, HEAD[:30]), (0.1, HEAD[30:60]),
                                                            (0.3, "CLOSE")]),
                     ("headers + partial body, close", [(0, HEAD), (0, BODY[:5]), (0.3, "CLOSE")])):
    longest, c, h, w = one(script)
    check("%s -> closed, nothing handled, loop held <= %d ms" % (name, FAST),
          longest != "SPUN" and longest <= FAST and c.closed and not h, (longest, c.closed, h))
trickle = [(0.3 * i, HEAD[i:i + 1]) for i in range(40)]
t0 = time.monotonic()
longest, c, h, w = one(trickle)
check("client trickling 1 byte / 0.3 s -> dropped after REQUEST_DEADLINE",
      longest != "SPUN" and longest <= FAST and c.closed and not h
      and DL - 0.1 <= c.closed_at - t0 <= DL + 0.3, (longest, round(c.closed_at - t0, 2)))
t0 = time.monotonic()
longest, c, h, w = one([(9.0, HEAD)])
check("silent client (stays open) -> dropped after REQUEST_DEADLINE",
      longest <= FAST and c.closed and not h and c.closed_at - t0 <= DL + 0.3,
      (longest, round(c.closed_at - t0, 2)))

print("\nnoknok_rpc._SafeServer: normal and slow requests are answered")
longest, c, h, w = one([(0, HEAD + BODY)])
check("complete request in one piece -> answered, loop held <= %d ms" % FAST,
      h == [BODY] and b'"ok": true' in c.sent and longest <= FAST, (longest, h))
check("the reply is sent with a timeout <= SEND_TIMEOUT",
      c.timeout_at_send is not None and 0 < c.timeout_at_send <= noknok_rpc.SEND_TIMEOUT,
      c.timeout_at_send)
for gap in (0.2, 0.6, 1.5):
    longest, c, h, w = one([(0, HEAD), (gap, BODY)])
    check("body %.1f s after the headers -> answered, loop held <= %d ms" % (gap, FAST),
          h == [BODY] and longest <= FAST and c.closed, (longest, h))
longest, c, h, w = one([(0, HEAD[:40]), (0.05, HEAD[40:]), (0.1, BODY[:10]), (0.15, BODY[10:])])
check("request in four pieces -> answered", h == [BODY] and longest <= FAST, (longest, h))
big = (b"POST /rpc HTTP/1.1\r\nHost: brain\r\nContent-Length: 10000\r\n\r\n" + b"x" * 300)
longest, c, h, w = one([(0, big)])
check("Content-Length over MAX_BODY -> handed to the route at once (no wait)",
      len(h) == 1 and longest <= FAST and c.closed, (longest, len(h)))

print("\nnoknok_rpc._SafeServer: a busy product loop (polls only every 0.3 s)")
longest, cs, h, when = run(Safe, [(0, Client([(0.02, HEAD + BODY)]))], every=0.3)
check("data 20 ms after the connect -> answered in the same pass (< 0.1 s)",
      h == [BODY] and when and when[0] < 0.1 and longest <= FAST,
      (longest, [round(x, 2) for x in when]))

print("\nnoknok_rpc._SafeServer: the reply has a total budget (the 30 Oct case)")


class Spinner(Client):
    """send() always says 'try again' at once - what CircuitPython did on the bench."""
    def send(self, data):
        raise OSError(11, "try again")


def _big(request):
    return Response(request, '{"ok": true, "x": "%s"}' % ("y" * 3000),
                    content_type="application/json")


srv, _ = make(Safe)
srv._routes = []
srv.route("/rpc", POST)(_big)
sp = Spinner([(0, HEAD + BODY)])
srv._sock = Listener([(0, sp)])
t0 = time.monotonic()
longest = 0
while time.monotonic() - t0 < 3 and not sp.closed:
    t1 = time.monotonic()
    srv.poll()
    longest = max(longest, (time.monotonic() - t1) * 1000)
    time.sleep(0.01)
check("a reply that never goes out ('try again' forever) -> cut after SEND_BUDGET, closed",
      sp.closed and longest <= noknok_rpc.SEND_BUDGET * 1000 + FAST, (round(longest), sp.closed))
check("... and counted in link_stats() cut_replies", noknok_rpc.link_stats()["cut_replies"] >= 1)

print("\nnoknok_rpc._SafeServer: two clients at once (the DEV-169 case)")
slow = Client([(0, HEAD), (1.0, BODY)])
fast = Client([(0, HEAD + BODY)])
longest, cs, h, when = run(Safe, [(0, slow), (0.1, fast)])
check("a fast client behind a slow body is answered first, within 0.1 s",
      len(h) == 2 and when[0] < 0.25 and fast.sent and longest <= FAST,
      (longest, [round(x, 2) for x in when]))
check("... and the slow one is still answered when its body arrives",
      slow.sent and 1.0 <= when[1] <= 1.3, [round(x, 2) for x in when])
silent = [Client([(9.0, HEAD)]) for _ in range(5)]
srv, _ = make(Safe)
lst = Listener([(0, c) for c in silent])
srv._sock = lst
for _ in range(10):
    srv.poll()
check("at most MAX_PENDING connections are collected; the rest wait in the backlog",
      len(srv._noknok_pending) == noknok_rpc.MAX_PENDING
      and len(lst.queue) == 5 - noknok_rpc.MAX_PENDING, (len(srv._noknok_pending), len(lst.queue)))

print("\nservice() measures how long the app channel held the loop")


class SlowCarrier:
    def poll(self):
        time.sleep(0.07)


noknok_rpc._carrier = SlowCarrier()
noknok_rpc._watch_link = lambda now: None
noknok_rpc.service(force=True)
noknok_rpc.service(force=True)
st = noknok_rpc.link_stats()
check("link_stats(): poll_max_ms, slow_polls, polls after two 70 ms polls",
      st["poll_max_ms"] >= 70 and st["slow_polls"] == 2 and st["polls"] == 2, st)
check("poll_max_ms resets on read, the counters do not",
      noknok_rpc.link_stats()["poll_max_ms"] == 0 and noknok_rpc.link_stats()["slow_polls"] == 2)
noknok_rpc._carrier = None

print("\nthe carrier uses it")
src = open(os.path.join(HERE, "..", "software", "noknok_rpc.py"), encoding="utf-8").read()
check("HttpCarrier.start() builds _safe_server_class(), not the plain Server",
      "_safe_server_class()(pool" in src and "srv = Server(pool" not in src)

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
