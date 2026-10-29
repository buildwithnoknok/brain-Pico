# noknok_rpc.py  v0.3 — Device Protocol v1: message dispatcher + carriers (DEV-34)
#
# v0.2 (Sue): DEV-79 — an offline brain rejoins WiFi without stalling the
#   product: most rejoin attempts only START the join (LINK_KICK_S, 0.5 s) and
#   service() then watches for the link once a second while the radio finishes
#   in the background; every LINK_FULL_EVERY-th attempt is a full LINK_JOIN_S
#   attempt as a safety net. set_wifi_credentials(on_up=) runs post-join work
#   (code.py: NTP, mDNS) once the link is back. An offline boot no longer
#   counts as a link drop.
# v0.3 (Sue): DEV-79 bench — on the Pico 2 W a 0.5 s kick finished the join in
#   the background 5/5 (link up ~3 s later; bench_dev79_kick.py, 23 Oct), so the
#   full 5 s safety-net attempts are off by default: LINK_FULL_EVERY = 0.
#
# One JSON message protocol, transport-agnostic (Confluence 113868802 §2):
#
#   → {"id": 7, "op": "settings.set", "args": {...}, "token": "..."}
#   ← {"id": 7, "ok": true, ...result fields...}
#   ← {"id": 8, "ok": false, "error": "unknown op: foo"}
#
# The Dispatcher knows ops, not transports. A carrier turns bytes on some
# medium into dispatch() calls: the HttpCarrier (POST /rpc over the setup AP
# and over home WiFi). mDNS (noknok-XXXX.local) is on by default since code.py
# 0.36 (DEV-70; settings.toml NOKNOK_MDNS = 0 switches it off); the app still
# finds brains by scanning its /24 with `hello`. BLE is not planned (skipped by
# ADR-001); another carrier would feed the same dispatcher unchanged.
#
# Staying reachable while a product runs (spec §5): CircuitPython runs one
# thing at a time, and after setup that is the maker's `while True:` loop.
# service() is the cooperative moment, made invisible: noknok.py's module
# drivers call it at the top of every read/write (before they lock the bus),
# Conductor.sleep() calls it while sleeping, and code.py's idle loops call it
# directly. It is non-blocking and throttled (SERVICE_INTERVAL); the risk-#1
# soak of 17 Sep 2026 measured it at ~1 ms idle, 10–100 ms when a request is
# answered, ≤ 240 ms when a phone stalls mid-request (bounded by
# socket_timeout, never a hang). DEV-170 (27 Oct 2026): that bound did NOT hold
# for a client that closes after a partial request - the library's read loops
# spun on 0-byte reads forever and froze the product. _SafeServer below bounds
# every request to REQUEST_DEADLINE (+ BODY_GRACE) and drops it on a close. Re-entrancy: a handler that talks to a
# module re-enters service() → it returns at once (_busy). Work that must run
# OUTSIDE a module transaction (on_change callbacks, reboot) is queued with
# defer() and runs after the carrier's poll returns.
#
# Security (v1): no authentication. The setup AP is open today and the home
# LAN is trusted, same as the existing endpoints. The `token` field is
# reserved; Dispatcher.authorize is the hook pairing / authorisation (DEV-35)
# plugs into.

import json
import time

__version__ = "0.5"

SERVICE_INTERVAL = 0.05     # s between carrier polls from the implicit hook
SOCKET_TIMEOUT   = 1.0      # s a stalled client may hold poll(). 0.2 s dropped the
                            # bodies of phone requests (18 Sep): a phone's body
                            # packet can trail its headers by hundreds of ms.
MAX_BODY         = 4096     # bytes; a settings.set is ~100, a provision ~600
REQUEST_DEADLINE = 1.0      # s for the headers + the body of ONE request (DEV-170): a
                            # client that is slower, or closes half-way, is dropped
LINK_CHECK_S     = 30       # how often service() looks at the WiFi link
LINK_JOIN_S      = 5        # blocking budget for a FULL rejoin attempt
LINK_KICK_S      = 0.5      # budget of a kick: start the join, finish in the background
LINK_FULL_EVERY  = 0        # 0 = kicks only (bench-proven 23 Oct: kicks finish in the
                            # background); N = every Nth attempt is a full LINK_JOIN_S one
LINK_POLL_S      = 1.0      # while the link is down: how often to look (cheap)
LINK_MAX_S       = 300      # longest pause between attempts (DEV-79: join <= 15 min)


class BodyTooLarge(Exception):
    pass


_SafeServer = None

def _safe_server_class():
    """adafruit_httpserver's Server with the two receive loops bounded (DEV-170).

    The library (4.8.2, also upstream main) reads headers until the blank line and
    the body until Content-Length, and stops only on a timeout or an error. A client
    that closes half-way makes recv_into() return 0 bytes at once, forever: the loop
    spins and, because /rpc is served from inside the product loop, the whole
    product freezes (buttons, LEDs, WiFi rejoin). And its timeout is per read, so a
    client sending a byte now and then could hold the loop as long as it liked.

    Here both loops stop on a 0-byte read (peer closed) and on one total deadline
    per request, REQUEST_DEADLINE, each read waiting only for the time left.
    Incomplete headers return b"" - the library then answers nothing and closes
    the socket itself ("connection timed out"), so no socket is leaked. Built
    lazily: adafruit_httpserver is only imported when a server starts."""
    global _SafeServer
    if _SafeServer is not None:
        return _SafeServer
    from adafruit_httpserver import Server

    class SafeServer(Server):
        def _recv_bounded(self, sock):
            """Bytes read into self._buffer, or None: peer closed / time up."""
            left = self._noknok_deadline - time.monotonic()
            if left <= 0:
                return None
            try:
                sock.settimeout(max(0.05, left))
                n = sock.recv_into(self._buffer, len(self._buffer))
            except OSError as e:
                if (e.args[0] if e.args else None) in (11, 110, 116):
                    return None              # nothing more within the deadline
                raise                        # reset etc.: the library handles it
            return n or None                 # 0 = the client closed the connection

        def _receive_header_bytes(self, sock):
            self._noknok_deadline = time.monotonic() + REQUEST_DEADLINE
            got = b""
            while b"\r\n\r\n" not in got:
                n = self._recv_bounded(sock)
                if n is None:
                    return b""               # incomplete: no request, socket closed
                got += bytes(self._buffer[:n])
            return got

        def _receive_body_bytes(self, sock, received_body_bytes, content_length):
            got = bytes(received_body_bytes or b"")
            while len(got) < content_length:
                n = self._recv_bounded(sock)
                if n is None:
                    break                    # short body: _full_body() logs it
                got += bytes(self._buffer[:n])
            return got[:content_length]

    _SafeServer = SafeServer
    return SafeServer


class RpcError(Exception):
    """Raise from a handler to answer {"ok": false, "error": code}."""
    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code, self.detail = code, detail


class Dispatcher:
    """op name → handler(args: dict, msg: dict) -> dict | None."""

    def __init__(self):
        self._ops = {}
        self.authorize = None       # fn(msg) -> bool; None = open (v1)

    def register(self, op, fn):
        self._ops[op] = fn
        return fn

    def op(self, name):
        """Decorator form: @dispatcher.op("hello")."""
        def deco(fn):
            self._ops[name] = fn
            return fn
        return deco

    def ops(self):
        return sorted(self._ops)

    def dispatch(self, msg):
        """Never raises. Always returns a reply dict echoing the request id."""
        if not isinstance(msg, dict):
            return {"id": None, "ok": False, "error": "bad message"}
        mid, op = msg.get("id"), msg.get("op")
        fn = self._ops.get(op)
        if fn is None:
            return {"id": mid, "ok": False, "error": "unknown op: %s" % (op,)}
        if self.authorize is not None and not self.authorize(msg):
            return {"id": mid, "ok": False, "error": "unauthorized"}
        args = msg.get("args") or {}
        if not isinstance(args, dict):
            return {"id": mid, "ok": False, "error": "bad args"}
        try:
            res = fn(args, msg)
        except RpcError as e:
            out = {"id": mid, "ok": False, "error": e.code}
            if e.detail is not None:
                out["detail"] = e.detail
            return out
        except Exception as e:
            return {"id": mid, "ok": False, "error": "internal", "detail": repr(e)}
        out = {"id": mid, "ok": True}
        if isinstance(res, dict):
            out.update(res)
        return out


_dispatcher = None
def dispatcher():
    """The brain's one Dispatcher (lazy). code.py registers the ops."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher()
    return _dispatcher


# ── HTTP carrier ─────────────────────────────────────────────────────────────

class HttpCarrier:
    """POST /rpc on an adafruit_httpserver Server. `server` is exposed so
    code.py can keep its legacy routes (/connect, /roles/*, captive-portal
    pages) on the same listener — they are adapters over the dispatcher."""

    def __init__(self, disp=None, logfn=print):
        self.dispatcher = disp or dispatcher()
        self.server = None
        self.log = logfn

    def start(self, pool, port=80, debug=False):
        from adafruit_httpserver import Request, Response, POST
        srv = _safe_server_class()(pool, debug=debug)       # DEV-170: bounded reads
        d = self.dispatcher

        @srv.route("/rpc", POST)
        def _rpc(request: Request):
            try:
                body = self._full_body(request)
                if isinstance(body, (bytes, bytearray, memoryview)):
                    body = bytes(body).decode("utf-8")
                msg = json.loads(body)
            except BodyTooLarge as e:
                reply = {"id": None, "ok": False, "error": "body too large", "limit": MAX_BODY}
            except Exception as e:
                snippet = body[:100] if isinstance(body, (str, bytes)) else body
                self.log("[rpc] bad json (%r): %r len=%s ct=%r cl=%r" % (
                    e, snippet, len(body) if body else 0,
                    request.headers.get("Content-Type"),
                    request.headers.get("Content-Length")))
                _last_bad["body"] = repr(snippet)
                _last_bad["error"] = repr(e)
                reply = {"id": None, "ok": False, "error": "bad json"}
            else:
                reply = d.dispatch(msg)
            return Response(request, json.dumps(reply), content_type="application/json")

        srv.start("0.0.0.0", port=port)
        try:
            srv.socket_timeout = SOCKET_TIMEOUT
        except Exception:
            pass                                 # older library: default applies
        self.server = srv
        return srv

    BODY_GRACE = 1.0        # s to wait for the rest of a body that is still in flight

    def _full_body(self, request):
        """The request body, complete. adafruit_httpserver hands over whatever
        arrived with the headers; when a client sends headers and body as two
        TCP segments a few ms apart (Python urllib does, phones may) the body
        can be missing (bench, 17 Sep 2026: 5 ms gap → empty body). Read the
        remainder from the connection ourselves, bounded by BODY_GRACE."""
        body = bytes(request.body or b"")
        try:
            need = int(request.headers.get("Content-Length") or 0)
        except Exception:
            need = 0
        if need > MAX_BODY or len(body) > MAX_BODY:
            raise BodyTooLarge()
        if len(body) >= need:
            return body
        conn = getattr(request, "connection", None)
        if conn is None:
            return body
        buf = bytearray(256)
        t0 = time.monotonic()
        end = t0 + self.BODY_GRACE
        why = "time up"
        # DEV-110: "not there yet" (EAGAIN 11) and "timed out" (110/116) mean the
        # rest is still in flight - keep waiting until BODY_GRACE is used up. The
        # old loop gave up at the first such error, so a body a few ms late could
        # come out as "0 of 39 bytes". Only a closed connection or another error
        # ends it early.
        while len(body) < need:
            left = end - time.monotonic()
            if left <= 0:
                break
            try:
                conn.settimeout(max(0.05, left))
                n = conn.recv_into(buf)
            except OSError as e:
                code = e.args[0] if e.args else None
                if code in (11, 110, 116):
                    time.sleep(0.01)
                    continue
                why = "error %r" % (e,)
                break
            except Exception as e:
                why = "error %r" % (e,)
                break
            if not n:
                why = "connection closed"
                break
            body += bytes(buf[:n])
        if len(body) < need:
            self.log("[rpc] short body: %d of %d bytes after %.2f s (%s)"
                     % (len(body), need, time.monotonic() - t0, why))
        return body

    def poll(self):
        """One non-blocking service pass. A client stalling mid-request makes
        the library's recv() time out (errno 110/116); that request is dropped
        and this returns None — the product loop is never held longer than
        SOCKET_TIMEOUT."""
        if self.server is None:
            return None
        try:
            return self.server.poll()
        except OSError as e:
            if e.errno in (110, 116):
                return None
            raise

    def stop(self):
        if self.server is not None:
            try:
                self.server.stop()
            except Exception:
                pass
            self.server = None


# ── Servicing ────────────────────────────────────────────────────────────────

_last_bad = {}              # last unparseable request, for `status` (diagnostics)
_carrier  = None
_next     = 0.0
_busy     = False
_deferred = []
_logfn    = print
_err_next = 0.0             # rate limit for poll-error log lines
_link     = {"ssid": None, "password": None, "next": 0.0, "was_up": True,
             "drops": 0, "rejoins": 0, "port": 80, "backoff": LINK_CHECK_S,
             "retry_at": 0.0, "on_up": None}

def set_wifi_credentials(ssid, password, port=80, on_up=None):
    """Let service() re-join the home network after a router reboot, or join it
    for the first time on a brain that booted offline (DEV-79), and bring the
    HTTP carrier back (Sam #6). Rate-limited, bounded, only from the servicing
    slot — never inside a module read. on_up() runs (deferred) each time the
    link comes back: post-join work such as NTP."""
    _link["ssid"], _link["password"], _link["port"] = ssid, password, port
    _link["on_up"] = on_up
    try:
        import wifi
        _link["was_up"] = bool(wifi.radio.connected)   # offline boot = not a "drop"
    except Exception:
        pass

def _radio_up():
    import wifi
    return bool(wifi.radio.connected) and wifi.radio.ipv4_address is not None

def _link_up(how):
    """The link is (back) up: reset the backoff, rebuild the carrier, run on_up."""
    _link["backoff"] = LINK_CHECK_S
    _link["retry_at"] = 0.0
    back = not _link["was_up"]
    _link["was_up"] = True
    if back:
        import wifi
        _logfn("[wifi] %s — %s" % (how, wifi.radio.ipv4_address))
    if _carrier is None or _carrier.server is None:
        _restart_http()                     # first time online, or after a drop
    if back and _link["on_up"] is not None:
        defer(_link["on_up"])

def _watch_link(now):
    """Called from service(): note drops, rejoin with backoff (DEV-79).

    Up: look again in LINK_CHECK_S. Down: look every LINK_POLL_S (a kicked join
    may complete in the background), and start a new attempt when its backoff
    (30 -> 60 -> 120 -> 240 -> 300 s) is over. A kick blocks the product for at
    most LINK_KICK_S; every LINK_FULL_EVERY-th attempt blocks up to LINK_JOIN_S
    so the brain still joins if a kick cannot finish on its own."""
    if not _link["ssid"] or now < _link["next"]:
        return
    try:
        up = _radio_up()
    except Exception:
        return
    if up:
        _link["next"] = now + LINK_CHECK_S
        _link_up("link is back" if _link["rejoins"] == 0 else
                 "joined (attempt %d)" % _link["rejoins"])
        return
    if _link["was_up"]:
        _link["drops"] += 1
        _logfn("[wifi] link DOWN — rejoin with backoff (%d s .. %d s)"
               % (LINK_CHECK_S, LINK_MAX_S))
    _link["was_up"] = False
    _link["next"] = now + LINK_POLL_S
    if now < _link["retry_at"]:
        return                              # waiting: backoff, or a kicked join running
    backoff = _link.get("backoff", LINK_CHECK_S)
    _link["retry_at"] = now + backoff
    _link["backoff"] = min(LINK_MAX_S, backoff * 2)
    _link["rejoins"] += 1
    full = bool(LINK_FULL_EVERY) and _link["rejoins"] % LINK_FULL_EVERY == 0
    try:
        import gc, wifi
        gc.collect()
        wifi.radio.connect(_link["ssid"], _link["password"] or "",
                           timeout=LINK_JOIN_S if full else LINK_KICK_S)
    except Exception as e:
        # A kick usually "fails" with a timeout while the join goes on; the
        # next LINK_POLL_S look sees the link if it completes.
        _logfn("[wifi] rejoin attempt %d (%s): %r — next in %d s"
               % (_link["rejoins"], "full" if full else "kick", e, backoff))
        return
    try:
        if _radio_up():
            _link_up("rejoined (attempt %d)" % _link["rejoins"])
    except Exception:
        pass

def _restart_http():
    """The listener does not survive a lost link — rebuild it."""
    try:
        import wifi, socketpool
        start_http(socketpool.SocketPool(wifi.radio), _link["port"], _logfn)
        _logfn("[rpc] carrier restarted on %s" % wifi.radio.ipv4_address)
    except Exception as e:
        _logfn("[rpc] carrier restart failed: %r" % (e,))

def link_stats():
    return {"drops": _link["drops"], "rejoins": _link["rejoins"],
            "backoff": _link.get("backoff")}

def no_power_save(logfn=print):
    """Keep the radio awake. In power-save mode the CYW43 only listens every
    beacon interval, so the second packet of a request (a phone sends headers
    and body separately) trails by hundreds of ms — bodies were arriving empty
    and every reply took ~0.4 s. A mains-powered product has no reason to save
    radio power."""
    try:
        import wifi
        wifi.radio.power_management = wifi.PowerManagement.NONE
        logfn("[wifi] power management off (low latency)")
    except Exception as e:
        logfn("[wifi] power management not settable (%r)" % (e,))

def start_http(pool, port=80, logfn=print):
    """Bind the HTTP carrier (setup AP or home WiFi). Returns the carrier."""
    global _carrier, _logfn
    _logfn = logfn
    no_power_save(logfn)
    stop_http()
    _carrier = HttpCarrier(dispatcher(), logfn)
    _carrier.start(pool, port)
    return _carrier

def stop_http():
    global _carrier
    if _carrier is not None:
        _carrier.stop()
        _carrier = None

def carrier():
    return _carrier

def defer(fn):
    """Run fn after the current service pass, outside any module transaction
    and after the reply has been sent (reboot, factory reset, on_change)."""
    _deferred.append(fn)

def service(force=False):
    """Pump pending messages. Non-blocking, throttled, re-entrancy-safe; a
    no-op when no carrier is up (offline product) — so it is always safe to
    call, from a driver, a sleep, or a loop."""
    global _next, _busy, _err_next
    if _busy:
        return
    _feed()                                  # bench watchdog, if armed
    now = time.monotonic()
    if not force and now < _next:
        return
    _next = now + SERVICE_INTERVAL
    _busy = True
    try:
        _watch_link(now)
        if _carrier is not None:
            try:
                _carrier.poll()
            except Exception as e:
                if now >= _err_next:         # one line per 10 s, not one per poll
                    _err_next = now + 10
                    _logfn("[rpc] poll error: %r" % (e,))
    finally:
        _busy = False
    while _deferred:
        fn = _deferred.pop(0)
        try:
            fn()
        except Exception as e:
            _logfn("[rpc] deferred call failed: %r" % (e,))


# ── Bench watchdog ───────────────────────────────────────────────────────────

_wdt = None

def arm_watchdog(seconds=8, logfn=print):
    """Hardware watchdog fed by service(). Once armed it cannot be disarmed on
    RP2 — a hang (or a product that never services for `seconds`) resets the
    board. Bench diagnostics only; see code.py start_app_channel()."""
    global _wdt
    try:
        import microcontroller
        from watchdog import WatchDogMode
        w = microcontroller.watchdog
        w.timeout = seconds
        w.mode = WatchDogMode.RESET
        w.feed()
        _wdt = w
        logfn("[wdt] armed: %d s RESET (bench)" % seconds)
    except Exception as e:
        logfn("[wdt] unavailable (%r)" % (e,))

def _feed():
    if _wdt is not None:
        try:
            _wdt.feed()
        except Exception:
            pass


# ── mDNS ─────────────────────────────────────────────────────────────────────

_mdns = None

def device_name():
    """`noknok-XXXX` — last two bytes of the MCU unique id (spec §2.1)."""
    import microcontroller
    uid = microcontroller.cpu.uid
    return "noknok-%02x%02x" % (uid[-2], uid[-1])

def start_mdns(port=80, logfn=print):
    """Advertise this brain as <device_name>.local so the app can find it on
    the home network without knowing its IP. Best-effort; never raises."""
    global _mdns
    try:
        import mdns
        import wifi
        srv = mdns.Server(wifi.radio)
        srv.hostname = device_name()
        srv.advertise_service(service_type="_http", protocol="_tcp", port=port)
        _mdns = srv                              # keep a reference: GC would stop it
        logfn("[mdns] advertising %s.local" % srv.hostname)
        return srv
    except Exception as e:
        logfn("[mdns] not started (%r)" % (e,))
        return None
