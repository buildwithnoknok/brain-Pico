# noknok_rpc.py  v0.1 — Device Protocol v1: message dispatcher + carriers (DEV-34)
#
# One JSON message protocol, transport-agnostic (Confluence 113868802 §2):
#
#   → {"id": 7, "op": "settings.set", "args": {...}, "token": "..."}
#   ← {"id": 7, "ok": true, ...result fields...}
#   ← {"id": 8, "ok": false, "error": "unknown op: foo"}
#
# The Dispatcher knows ops, not transports. A carrier turns bytes on some
# medium into dispatch() calls: today the HttpCarrier (POST /rpc over the
# setup AP and over home WiFi + mDNS); a BLE NUS carrier (DEV-35) will feed
# the same dispatcher with newline-delimited JSON when the platform has BLE.
#
# Staying reachable while a product runs (spec §5): CircuitPython runs one
# thing at a time, and after setup that is the maker's `while True:` loop.
# service() is the cooperative moment, made invisible: noknok.py's module
# drivers call it at the top of every read/write (before they lock the bus),
# Conductor.sleep() calls it while sleeping, and code.py's idle loops call it
# directly. It is non-blocking and throttled (SERVICE_INTERVAL); the risk-#1
# soak of 17 Sep 2026 measured it at ~1 ms idle, 10–100 ms when a request is
# answered, ≤ 240 ms when a phone stalls mid-request (bounded by
# socket_timeout, never a hang). Re-entrancy: a handler that talks to a
# module re-enters service() → it returns at once (_busy). Work that must run
# OUTSIDE a module transaction (on_change callbacks, reboot) is queued with
# defer() and runs after the carrier's poll returns.
#
# Security (v1): no authentication. The setup AP is open today and the home
# LAN is trusted, same as the existing endpoints. The `token` field is
# reserved; Dispatcher.authorize is the hook pairing (DEV-35) plugs into.

import json
import time

__version__ = "0.1"

SERVICE_INTERVAL = 0.05     # s between carrier polls from the implicit hook
SOCKET_TIMEOUT   = 0.2      # s a stalled client may hold poll() (default lib: 1 s)
MAX_BODY         = 4096     # bytes; a settings.set is ~100, a provision ~600
LINK_CHECK_S     = 30       # how often service() looks at the WiFi link
LINK_JOIN_S      = 5        # blocking budget for one rejoin attempt


class BodyTooLarge(Exception):
    pass


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
        from adafruit_httpserver import Server, Request, Response, POST
        srv = Server(pool, debug=debug)
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
                self.log("[rpc] bad json (%r)" % (e,))
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

    BODY_GRACE = 0.3        # s to wait for the rest of a body that is still in flight

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
        end = time.monotonic() + self.BODY_GRACE
        try:
            conn.settimeout(self.BODY_GRACE)
            while len(body) < need and time.monotonic() < end:
                n = conn.recv_into(buf)
                if not n:
                    break
                body += bytes(buf[:n])
        except Exception:
            pass
        if len(body) < need:
            self.log("[rpc] short body: %d of %d bytes" % (len(body), need))
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

_carrier  = None
_next     = 0.0
_busy     = False
_deferred = []
_logfn    = print
_err_next = 0.0             # rate limit for poll-error log lines
_link     = {"ssid": None, "password": None, "next": 0.0, "was_up": True,
             "drops": 0, "rejoins": 0, "port": 80, "backoff": LINK_CHECK_S}

def set_wifi_credentials(ssid, password, port=80):
    """Let service() re-join the home network after a router reboot and bring
    the HTTP carrier back (Sam #6). Rate-limited (LINK_CHECK_S), bounded
    (LINK_JOIN_S), only from the servicing slot — never inside a module read."""
    _link["ssid"], _link["password"], _link["port"] = ssid, password, port

def _watch_link(now):
    """Called from service(): note drops, rejoin once per LINK_CHECK_S."""
    if not _link["ssid"] or now < _link["next"]:
        return
    try:
        import wifi
        up = bool(wifi.radio.connected)
    except Exception:
        return
    if up:
        _link["next"] = now + LINK_CHECK_S
        _link["backoff"] = LINK_CHECK_S
        if not _link["was_up"]:
            _logfn("[wifi] link is back")
        _link["was_up"] = True
        if _carrier is None or _carrier.server is None:
            _restart_http()                 # first time online, or after a drop
        return
    if _link["was_up"]:
        _link["drops"] += 1
        _logfn("[wifi] link DOWN — rejoin with backoff (%d s .. 300 s)" % LINK_CHECK_S)
    _link["was_up"] = False
    # Each failed attempt blocks the product for up to LINK_JOIN_S, so a brain
    # whose router is off for hours must not pay that every 30 s: back off
    # 30 → 60 → 120 → 240 → 300 s.
    backoff = _link.get("backoff", LINK_CHECK_S)
    _link["next"] = now + backoff
    _link["backoff"] = min(300, backoff * 2)
    _link["rejoins"] += 1
    try:
        import gc
        gc.collect()
        wifi.radio.connect(_link["ssid"], _link["password"] or "", timeout=LINK_JOIN_S)
        _logfn("[wifi] rejoined — %s" % wifi.radio.ipv4_address)
        _link["was_up"] = True
        _link["backoff"] = LINK_CHECK_S
        _restart_http()
    except Exception as e:
        _logfn("[wifi] rejoin failed: %r" % (e,))

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

def start_http(pool, port=80, logfn=print):
    """Bind the HTTP carrier (setup AP or home WiFi). Returns the carrier."""
    global _carrier, _logfn
    _logfn = logfn
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
