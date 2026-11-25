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
# "Connection: close": these cases test reading and the reply; keep-alive (DEV-198, a
# client that keeps its connection) has its own section below with HEAD_KA.
HEAD = (b"POST /rpc HTTP/1.1\r\nHost: brain\r\nContent-Type: application/json\r\n"
        b"Connection: close\r\nContent-Length: %d\r\n\r\n" % len(BODY))
HEAD_KA = HEAD.replace(b"Connection: close\r\n", b"")      # HTTP/1.1: keep by default
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


class Hiccup(Client):
    """DEV-286: the first segment goes out, then ONE send times out (116: the ACK is late),
    then the rest goes out - a busy WiFi link. The library retries 11 only."""
    def __init__(self, script):
        super().__init__(script)
        self.hiccups = 1

    def send(self, data):
        if self.sent and self.hiccups:
            self.hiccups -= 1
            raise OSError(116, "timed out")
        n = min(len(data), 1460)
        self.sent += bytes(data[:n])
        return n


class Broken(Client):
    """DEV-286: the link breaks after the first segment (113, no route to host)."""
    def send(self, data):
        if self.sent:
            raise OSError(113, "no route")
        self.sent += bytes(data[:1460])
        return min(len(data), 1460)


def _drive(srv, cl, limit=3):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit and not cl.closed:
        try:
            srv.poll()
        except OSError:
            pass                                  # the carrier logs and goes on
        time.sleep(0.01)


cut0 = noknok_rpc.link_stats()["cut_replies"]
srv, _ = make(Safe)
srv._routes = []
srv.route("/rpc", POST)(_big)
hc = Hiccup([(0, HEAD + BODY)])
srv._sock = Listener([(0, hc)])
_drive(srv, hc)
check("DEV-286: a send timeout after the first segment -> retried, the whole reply goes out",
      hc.sent.endswith(b'"}') and len(hc.sent) > 3000 and not hc.hiccups, len(hc.sent))
check("... and it is not counted as cut", noknok_rpc.link_stats()["cut_replies"] == cut0)

st0 = noknok_rpc.link_stats()
srv, _ = make(Safe)
srv._routes = []
srv.route("/rpc", POST)(_big)
br = Broken([(0, HEAD + BODY)])
srv._sock = Listener([(0, br)])
_drive(srv, br)
st1 = noknok_rpc.link_stats()
check("DEV-286: a reply a send error ends -> counted (cut_replies, send_errors, last_send_err)",
      br.closed and st1["cut_replies"] == st0["cut_replies"] + 1
      and st1["send_errors"] == st0["send_errors"] + 1 and st1["last_send_err"] == 113,
      (st0, st1))

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

print("\nDEV-198: HTTP keep-alive for POST /rpc")
REQ_KA = HEAD_KA + BODY


def polls(srv, n=1, every=0.01):
    for _ in range(n):
        srv.poll()
        time.sleep(every)


def fresh_server(clients):
    srv, handled = make(Safe)
    srv._sock = Listener(clients)
    return srv, handled


before = noknok_rpc.link_stats()
ka = Client([(0, REQ_KA), (0.3, REQ_KA), (0.6, REQ_KA)])
srv, h = fresh_server([(0, ka)])
polls(srv, 90)
check("3 requests on one kept connection -> all answered, ONE connection, still open",
      len(h) == 3 and ka.sent.count(b"HTTP/1.1 200") == 3 and not ka.closed,
      (len(h), ka.sent.count(b"HTTP/1.1 200"), ka.closed))
check("... replies say Connection: keep-alive + Keep-Alive timeout",
      b"connection: keep-alive" in ka.sent.lower() and b"keep-alive: timeout=" in ka.sent.lower(),
      ka.sent[:200])
after = noknok_rpc.link_stats()
check("... link_stats(): requests +3, reused +2",
      after["requests"] - before["requests"] == 3 and after["reused"] - before["reused"] == 2,
      (after["requests"] - before["requests"], after["reused"] - before["reused"]))

# DEV-293: after a "Connection: close" reply the CLIENT closes first (curl, urllib and
# the app do so at once after Content-Length bytes); the brain closes its end when it
# sees that, so the TIME_WAIT record stays with the client. These clients close 50 ms
# after their request.
before = noknok_rpc.link_stats()
c1 = Client([(0, HEAD + BODY), (0.05, "CLOSE")])
srv, h = fresh_server([(0, c1)])
polls(srv, 10)
check("client sends Connection: close -> closed after the reply, no keep-alive header",
      len(h) == 1 and c1.closed and b"keep-alive" not in c1.sent
      and b"connection: close" in c1.sent.lower())
check("... the brain waited for the client's close (client_closed +1, brain_closed +0)",
      noknok_rpc.link_stats()["client_closed"] - before["client_closed"] == 1
      and noknok_rpc.link_stats()["brain_closed"] == before["brain_closed"])

h10 = REQ_KA.replace(b"HTTP/1.1", b"HTTP/1.0", 1)
c10 = Client([(0, h10), (0.05, "CLOSE")])
srv, h = fresh_server([(0, c10)])
polls(srv, 10)
check("HTTP/1.0 without Connection: keep-alive -> closed after the reply", len(h) == 1 and c10.closed)

cget = Client([(0, b"GET / HTTP/1.1\r\nHost: brain\r\n\r\n"), (0.05, "CLOSE")])
srv, h = fresh_server([(0, cget)])
srv.route("/", "GET")(lambda r: Response(r, "page"))
polls(srv, 10)
check("legacy page (GET /) -> closed after the reply (keep-alive is /rpc only)",
      cget.closed and b"page" in cget.sent, (cget.closed, cget.sent[:40]))

before = noknok_rpc.link_stats()
cstay = Client([(0, HEAD + BODY)])            # reads the reply, never closes
srv, h = fresh_server([(0, cstay)])
t0 = time.monotonic()
longest = 0
while time.monotonic() - t0 < noknok_rpc.LINGER_S + 0.5 and not cstay.closed:
    t1 = time.monotonic()
    srv.poll()
    longest = max(longest, (time.monotonic() - t1) * 1000)
    time.sleep(0.01)
check("client that never closes -> the brain closes after LINGER_S (brain_closed +1)",
      cstay.closed and noknok_rpc.LINGER_S - 0.1 <= cstay.closed_at - t0 <= noknok_rpc.LINGER_S + 0.3
      and noknok_rpc.link_stats()["brain_closed"] - before["brain_closed"] == 1,
      round((cstay.closed_at or 0) - t0, 2))
check("... waiting for it holds the loop <= %d ms" % FAST, longest <= FAST, round(longest))

many = [Client([(0, HEAD + BODY)]) for _ in range(noknok_rpc.LINGER_MAX + 2)]
fill = [Client([(9.0, HEAD)]) for _ in range(noknok_rpc.MAX_PENDING)]
srv, h = fresh_server([(0.02 * i, c) for i, c in enumerate(many)]
                      + [(0.3, c) for c in fill])
polls(srv, 45)
check("lingering connections take no MAX_PENDING slot; past LINGER_MAX the oldest is closed",
      len(h) == len(many) and [c.closed for c in many[:2]] == [True, True]
      and sum(1 for p in srv._noknok_pending if p[7]) <= noknok_rpc.LINGER_MAX
      and all(any(p[0] is c for p in srv._noknok_pending) for c in fill),
      (len(h), [c.closed for c in many], len(srv._noknok_pending)))

cbye = Client([(0, REQ_KA), (0.2, "CLOSE")])
srv, h = fresh_server([(0, cbye)])
polls(srv, 40)
check("client closes its idle kept connection -> server drops + closes it",
      len(h) == 1 and cbye.closed and not srv._noknok_pending, (len(h), cbye.closed))

old_idle = noknok_rpc.KEEPALIVE_IDLE
noknok_rpc.KEEPALIVE_IDLE = 0.3
cidle = Client([(0, REQ_KA)])
srv, h = fresh_server([(0, cidle)])
t0 = time.monotonic()
while time.monotonic() - t0 < 1.0 and not cidle.closed:
    polls(srv)
check("kept connection idle for KEEPALIVE_IDLE -> closed by the server",
      len(h) == 1 and cidle.closed and 0.25 <= cidle.closed_at - t0 <= 0.6,
      round((cidle.closed_at or 0) - t0, 2))
noknok_rpc.KEEPALIVE_IDLE = old_idle

kas = [Client([(0, REQ_KA)]) for _ in range(2)] + [Client([(0, REQ_KA), (0.2, "CLOSE")])]
srv, h = fresh_server([(0, kas[0]), (0.05, kas[1]), (0.1, kas[2])])
polls(srv, 30)
check("3 keep-alive clients -> only KEEPALIVE_MAX (2) kept, the 3rd closed after its reply",
      len(h) == 3 and [c.closed for c in kas] == [False, False, True],
      [c.closed for c in kas])

a, b = Client([(0, REQ_KA)]), Client([(0, REQ_KA)])
late = Client([(0, HEAD + BODY)])
filler = Client([(9.0, HEAD)])                 # connected, silent: holds the 3rd slot
srv, h = fresh_server([(0, a), (0.05, b), (0.1, filler), (0.3, late)])
polls(srv, 60)
check("all slots taken (2 idle kept + 1 busy), a new client -> oldest idle closed, new one answered",
      a.closed and not b.closed and late.sent and noknok_rpc.link_stats()["ka_evicted"] >= 1,
      (a.closed, b.closed, bool(late.sent)))

old_reqs = noknok_rpc.KEEPALIVE_REQS
noknok_rpc.KEEPALIVE_REQS = 3
cmax = Client([(0.2 * i, REQ_KA) for i in range(5)])
srv, h = fresh_server([(0, cmax)])
polls(srv, 120)
check("KEEPALIVE_REQS reached -> the 3rd reply closes the connection",
      len(h) == 3 and cmax.closed and cmax.sent.lower().count(b"connection: keep-alive") == 2,
      (len(h), cmax.closed, cmax.sent.lower().count(b"connection: keep-alive")))
noknok_rpc.KEEPALIVE_REQS = old_reqs

cpipe = Client([(0, REQ_KA + REQ_KA)])
srv, h = fresh_server([(0, cpipe)])
polls(srv, 20)
check("two requests in one packet (pipelined) -> both answered in order",
      len(h) == 2 and cpipe.sent.count(b"HTTP/1.1 200") == 2, len(h))

cslow = Client([(0, REQ_KA), (0.3, HEAD_KA), (0.6, BODY)])
srv, h = fresh_server([(0, cslow)])
polls(srv, 100)
check("next request on a kept connection with a late body -> answered (request deadline)",
      len(h) == 2, len(h))
longest, cs, h, when = run(Safe, [(0, Client([(0, REQ_KA), (0.2, REQ_KA)]))], limit=0.8)
check("keep-alive polling holds the loop <= %d ms" % FAST,
      longest != "SPUN" and longest <= FAST and len(h) == 2, (longest, len(h)))

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
