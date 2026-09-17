# bench_rpc_soak.py — DEV-34 risk #1: is a socket SERVER safe after a Conductor exists?
#
# Background. DEV-32 found that an HTTPS download made after a Conductor has
# ever existed in the process can hang with no timeout (reproduced twice). The
# DEV-34 design (Device Protocol v1 §5) puts a `POST /rpc` server INSIDE the
# product loop — serviced from module reads, for the product's whole lifetime —
# which is the same "radio after Conductor" situation, as a server instead of a
# client. Sam's review (DEV-34 comment 10582, point 1): soak it FIRST, before a
# line of c.settings is written. Point 2: measure what a poll() costs the
# product loop (idle / with a request / with a slow client).
#
# What this script does — a stand-in for a product while it runs:
#   1. joins home WiFi from the saved credentials (wifi.json / Store copy)
#   2. creates a Conductor + enumerate_all()  ← the "Conductor exists" condition
#   3. serves POST /rpc (ops: hello, ping, status) on the STA address, port 80
#   4. loops like smart_lamp.py: reads every LED Button / Knob each pass
#      (~30 ms), beeps the buzzer + drives USB LEDs now and then (writes),
#      and calls server.poll() at most every POLL_EVERY_MS — the throttle the
#      design proposes. Every poll() is timed in µs and bucketed.
#   5. prints a stats line every 60 s, a summary + verdict at the end
#
# Hang detection: the hardware watchdog (8 s, RESET). A poll() that never
# returns resets the Pico — the run log stops, the client log shows the
# outage, and the bench needs no hand to unstick it. Once armed the RP2
# watchdog cannot be disarmed, so the Pico also resets ~8 s after a clean end
# (into code.py — normal boot). Expected; not a failure.
#
# Run from the Pi (see tools/rpc_soak.sh, which also starts the client):
#   ./pico.py run bench_rpc_soak.py 4500        # 2nd arg = serial timeout in s
# Pass criteria (Sam #1/#2): no watchdog reset, no poll() > 100 ms without a
# slow client, idle poll() well under 2 ms, module reads unaffected, WiFi
# still up. The CLIENT log judges reachability (tools/rpc_soak_client.py).

import gc
import json
import time
import wifi
import socketpool
import microcontroller
from adafruit_httpserver import Server, Request, Response, POST
import noknok as nk
from noknok import Conductor

SOAK_MINUTES   = 65        # ≥ 1 h (Sam #1)
POLL_EVERY_MS  = 50        # servicing throttle from the design (§5)
LOOP_SLEEP_S   = 0.03      # smart_lamp.py's loop period
STATS_EVERY_S  = 60
BEEP_EVERY_S   = 20        # a module WRITE now and then, like a real product
LEDS_EVERY_S   = 5
SOCKET_TIMEOUT = 0.2       # s per recv inside poll(); default is 1 s. Lower =
                           # smaller worst-case stall from a slow client.
USE_WATCHDOG   = True
WIFI_RETRY_S   = 30        # rate limit for reconnect attempts (Sam #6)
WIFI_TIMEOUT_S = 5

t0 = time.monotonic()
def log(msg):
    print("[%7.1f] %s" % (time.monotonic() - t0, msg))

# ── 1. WiFi ──────────────────────────────────────────────────────────────────
creds = (nk.read_json(nk.DATA_DIR + "/wifi.json") or nk.read_json("/wifi.json")
         or nk.store().get("wifi"))
if not creds or not creds.get("ssid"):
    raise SystemExit("no WiFi credentials on this brain — provision it first")

def join():
    """Blocking join with an explicit timeout; returns seconds it took or None."""
    t = time.monotonic()
    try:
        wifi.radio.connect(creds["ssid"], creds.get("password") or "", timeout=WIFI_TIMEOUT_S)
        return time.monotonic() - t
    except Exception as e:
        log("wifi join failed after %.1f s: %r" % (time.monotonic() - t, e))
        return None

for attempt in range(3):
    took = join()
    if took is not None:
        break
    time.sleep(3)
else:
    raise SystemExit("could not join WiFi")
ip = str(wifi.radio.ipv4_address)
log("wifi up in %.1f s — SOAK IP=%s ssid=%s" % (took, ip, creds["ssid"]))

# ── 2. Conductor — the condition under test ──────────────────────────────────
c = Conductor()
n = c.enumerate_all()
c.load_roles()
log("conductor up: %d module(s) — buzzer=%d knob=%d ledbutton=%d display=%d leds=%d store=%s"
    % (n, len(c.buzzer), len(c.knob), len(c.ledbutton), len(c.display), len(c.leds),
       nk.store().backend))
if not (c.knob or c.ledbutton):
    log("WARNING: no readable module on the bus — the loop will have no I2C reads")

# ── 3. /rpc server ───────────────────────────────────────────────────────────
stats = {
    "req": {},                      # op -> count
    "poll_n": 0, "poll_idle": 0, "poll_handled": 0, "poll_err": 0, "poll_timeout": 0,
    "poll_max_us": 0, "poll_sum_us": 0,
    "poll_b": [0, 0, 0, 0, 0, 0],   # <1ms <5 <20 <100 <1000 >=1000
    "read_n": 0, "read_err": 0, "read_max_us": 0, "read_sum_us": 0,
    "write_n": 0, "write_err": 0,
    "loop_max_ms": 0,
    "wifi_drops": 0, "wifi_rejoins": 0, "wifi_rejoin_max_s": 0,
}
BUCKETS_US = (1000, 5000, 20000, 100000, 1000000)

def bucket(us):
    for i, lim in enumerate(BUCKETS_US):
        if us < lim:
            return i
    return len(BUCKETS_US)

uid = "".join("%02x" % b for b in microcontroller.cpu.uid)

def handle(msg):
    """The dispatcher-to-be, minimal: id echoed, op switched."""
    op = msg.get("op")
    stats["req"][op] = stats["req"].get(op, 0) + 1
    if op == "hello":
        return {"id": msg.get("id"), "ok": True, "device": uid, "state": "soak",
                "uptime": round(time.monotonic() - t0, 1), "online": True}
    if op == "ping":
        return {"id": msg.get("id"), "ok": True}
    if op == "status":
        return {"id": msg.get("id"), "ok": True, "stats": stats, "mem": gc.mem_free()}
    return {"id": msg.get("id"), "ok": False, "error": "unknown op"}

pool   = socketpool.SocketPool(wifi.radio)
server = Server(pool, debug=False)

@server.route("/rpc", POST)
def _rpc(request: Request):
    try:
        msg = json.loads(request.body)
    except Exception:
        return Response(request, json.dumps({"id": None, "ok": False, "error": "bad json"}),
                        content_type="application/json")
    return Response(request, json.dumps(handle(msg)), content_type="application/json")

server.start("0.0.0.0", port=80)          # all interfaces, as code.py does
try:
    server.socket_timeout = SOCKET_TIMEOUT   # property in adafruit_httpserver ≥ 4.x
except Exception as e:
    log("socket_timeout not settable (%r) — library default applies" % (e,))
log("rpc server listening on http://%s/rpc (socket_timeout=%s)" % (ip, SOCKET_TIMEOUT))

# ── 4. Watchdog ──────────────────────────────────────────────────────────────
wdt = None
if USE_WATCHDOG:
    try:
        from watchdog import WatchDogMode
        wdt = microcontroller.watchdog
        wdt.timeout = 8
        wdt.mode = WatchDogMode.RESET
        wdt.feed()
        log("watchdog armed: 8 s RESET (a hang = a reset, visible in the logs)")
    except Exception as e:
        log("watchdog unavailable (%r) — running without" % (e,))
        wdt = None

# ── 5. The product-like loop ─────────────────────────────────────────────────
def timed_poll():
    t = time.monotonic_ns()
    try:
        r = server.poll()
    except OSError as e:
        # A client that stalls mid-request makes the library's recv() time out
        # (ETIMEDOUT 116 / 110) and the request is dropped — expected for the
        # deliberately slow client, and the product-friendly outcome (the app
        # retries; the loop is not held). Counted apart from real errors.
        r = None
        if e.errno in (110, 116):
            stats["poll_timeout"] += 1
        else:
            stats["poll_err"] += 1
            log("poll() raised %r" % (e,))
    except Exception as e:
        stats["poll_err"] += 1
        r = None
        log("poll() raised %r" % (e,))
    us = (time.monotonic_ns() - t) // 1000
    stats["poll_n"] += 1
    stats["poll_sum_us"] += us
    stats["poll_b"][bucket(us)] += 1
    if us > stats["poll_max_us"]:
        stats["poll_max_us"] = us
    # adafruit_httpserver returns a string constant ("no_request",
    # "request_handled_response_sent", ...); anything but no_request means a
    # request was accepted this call.
    if r is None or "no_request" in str(r).lower():
        stats["poll_idle"] += 1
    else:
        stats["poll_handled"] += 1
    if us >= 100000:
        log("SLOW poll(): %d ms (result %s)" % (us // 1000, r))

def timed_read(mod):
    t = time.monotonic_ns()
    v = mod.read()
    us = (time.monotonic_ns() - t) // 1000
    stats["read_n"] += 1
    stats["read_sum_us"] += us
    if us > stats["read_max_us"]:
        stats["read_max_us"] = us
    if v is None:
        stats["read_err"] += 1
    return v

def print_stats(final=False):
    pn = max(1, stats["poll_n"])
    rn = max(1, stats["read_n"])
    log("%s polls=%d idle=%d handled=%d timeout=%d err=%d avg=%dus max=%dus buckets(<1ms,<5,<20,<100,<1s,>=1s)=%s"
        % ("FINAL" if final else "stats", stats["poll_n"], stats["poll_idle"],
           stats["poll_handled"], stats["poll_timeout"], stats["poll_err"], stats["poll_sum_us"] // pn,
           stats["poll_max_us"], stats["poll_b"]))
    log("      reads=%d err=%d avg=%dus max=%dus | writes=%d err=%d | loop max=%dms | "
        "wifi drops=%d rejoins=%d rejoin_max=%.1fs | req=%s | mem=%d"
        % (stats["read_n"], stats["read_err"], stats["read_sum_us"] // rn,
           stats["read_max_us"], stats["write_n"], stats["write_err"],
           stats["loop_max_ms"], stats["wifi_drops"], stats["wifi_rejoins"],
           stats["wifi_rejoin_max_s"], stats["req"], gc.mem_free()))

readers = list(c.ledbutton) + list(c.knob)
end        = time.monotonic() + SOAK_MINUTES * 60
next_poll  = 0
next_stats = time.monotonic() + STATS_EVERY_S
next_beep  = time.monotonic() + BEEP_EVERY_S
next_leds  = time.monotonic() + LEDS_EVERY_S
next_wifi  = 0
was_up     = True
hue        = 0
log("soak running for %d min — poll every %d ms, loop %d ms, %d reader(s)"
    % (SOAK_MINUTES, POLL_EVERY_MS, LOOP_SLEEP_S * 1000, len(readers)))

while time.monotonic() < end:
    loop_t = time.monotonic_ns()
    if wdt:
        wdt.feed()

    # module reads — what a product does every pass
    for m in readers:
        timed_read(m)

    now = time.monotonic()

    # occasional module writes
    if now >= next_beep:
        next_beep = now + BEEP_EVERY_S
        if c.buzzer:
            stats["write_n"] += 1
            if not c.buzzer[0]._send([0x01, 880 >> 8, 880 & 0xFF, 1, 30]):  # play() returns None
                stats["write_err"] += 1
    if now >= next_leds and c.leds:
        next_leds = now + LEDS_EVERY_S
        hue = (hue + 40) % 256
        try:
            c.leds[0].set_all(hue, 255 - hue, 64)
            stats["write_n"] += 1
        except Exception:
            stats["write_err"] += 1

    # implicit servicing, throttled — the design's §5 step 1
    if now >= next_poll:
        next_poll = now + POLL_EVERY_MS / 1000
        timed_poll()

    # link watch (Sam #6): count drops, rejoin rate-limited, measure the block
    up = wifi.radio.connected
    if was_up and not up:
        stats["wifi_drops"] += 1
        log("wifi DROPPED")
    if not up and now >= next_wifi:
        next_wifi = now + WIFI_RETRY_S
        took = join()
        stats["wifi_rejoins"] += 1
        if took is not None:
            log("wifi rejoined in %.1f s (ip %s)" % (took, wifi.radio.ipv4_address))
            if took > stats["wifi_rejoin_max_s"]:
                stats["wifi_rejoin_max_s"] = took
    was_up = up

    if now >= next_stats:
        next_stats = now + STATS_EVERY_S
        print_stats()

    ms = (time.monotonic_ns() - loop_t) // 1000000
    if ms > stats["loop_max_ms"]:
        stats["loop_max_ms"] = ms
    time.sleep(LOOP_SLEEP_S)

# ── 6. Verdict ───────────────────────────────────────────────────────────────
print_stats(final=True)
slow_polls = stats["poll_b"][4] + stats["poll_b"][5]
verdict = []
if stats["poll_err"]:                       verdict.append("poll() raised %d×" % stats["poll_err"])
if stats["poll_b"][5]:                      verdict.append("%d poll(s) >= 1 s" % stats["poll_b"][5])
if stats["read_err"] > stats["read_n"] // 100: verdict.append("module read errors %d" % stats["read_err"])
if not wifi.radio.connected:                verdict.append("wifi down at end")
log("SOAK %s — idle poll avg %d us, max %d us, %d poll(s) 100 ms..1 s (slow-client tests expected here), %d handled"
    % ("PASS" if not verdict else "FAIL: " + "; ".join(verdict),
       stats["poll_sum_us"] // max(1, stats["poll_n"]), stats["poll_max_us"],
       stats["poll_b"][4], stats["poll_handled"]))
server.stop()
log("done — the watchdog will reset the Pico in ~8 s (normal)")
