#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev169_latency.py IP [SECONDS] [LABEL] [PATTERNS] - DEV-169: how long does the app channel hold
# the brain's product loop (the loop that reads the LED Button), measured, per traffic pattern.
#
# Runs on the bench host (the Pi) against a RUNNING product. The brain serves /rpc from inside
# the product loop, so while one client holds the loop every other client waits too: a
# "probe" client sends a small hello every 200 ms and its answer time is the loop-hold time
# as seen from outside (works for every noknok_rpc version). From 0.6 on, status.link also
# carries the brain's own number: poll_max_ms (longest single poll since the last read).
#
# Patterns, SECONDS each (default 60), all with the app's normal traffic underneath
# (settings.get every 3 s, like the app's settings page):
#   app          - nothing else
#   late_body    - every 3 s a client sends the headers, the body 0.6 s later (weak link /
#                  a phone's trailing body packet)
#   later_body   - the same with 1.5 s
#   half_open    - every 3 s a client connects, waits 0.5 s, closes (app scan, flaky WiFi)
#   trickle      - every 3 s a client sends its headers one byte every 50 ms
# PATTERNS: comma list (default all). On noknok_rpc 0.3/0.4 leave out half_open and trickle:
# they trigger the DEV-170 freeze there (the brain then needs a reset).
# Output: one line per pattern (probe p50 / p95 / max ms, probes over 100 / 200 ms, failed
# probes, the brain's poll_max_ms and slow_polls) and a JSON line per pattern in OUT
# (~/dev169/latency.jsonl). A failed probe (no answer within 3 s) counts as 3000 ms.
import json, os, socket, statistics, sys, threading, time

IP = sys.argv[1]
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 60
LABEL = sys.argv[3] if len(sys.argv) > 3 else ""
OUT = os.path.expanduser("~/dev169")
os.makedirs(OUT, exist_ok=True)


def req(op, args=None):
    body = json.dumps({"id": 1, "op": op, "args": args or {}}).encode()
    head = ("POST /rpc HTTP/1.1\r\nHost: %s\r\nContent-Type: application/json\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n" % (IP, len(body))).encode()
    return head, body


def call(op, args=None, timeout=3.0):
    """One request; (seconds, parsed reply) or (None, None)."""
    head, body = req(op, args)
    t = time.monotonic()
    try:
        s = socket.create_connection((IP, 80), timeout=timeout)
        s.sendall(head + body)
        d = b""
        while True:
            c = s.recv(4096)
            if not c:
                break
            d += c
        s.close()
        dt = time.monotonic() - t
        if not d.startswith(b"HTTP/1.1 200"):
            return None, None
        r = json.loads(d.split(b"\r\n\r\n", 1)[1].decode())
        return dt, r.get("result", r)
    except Exception:
        return None, None


def bad_client(kind):
    head, body = req("hello")
    try:
        s = socket.create_connection((IP, 80), timeout=5)
        if kind in ("late_body", "later_body"):
            s.sendall(head)
            time.sleep(0.6 if kind == "late_body" else 1.5)
            s.sendall(body)
            try:
                s.recv(4096)
            except Exception:
                pass
        elif kind == "half_open":
            time.sleep(0.5)
        elif kind == "trickle":
            for i in range(len(head)):
                s.send(head[i:i + 1])
                time.sleep(0.05)
        s.close()
    except Exception:
        pass


def every(period, fn, stop):
    while not stop.is_set():
        t = time.monotonic()
        threading.Thread(target=fn, daemon=True).start()
        stop.wait(max(0, period - (time.monotonic() - t)))


def run(kind):
    stop = threading.Event()
    lat = []
    call("status")                                   # resets poll_max_ms on 0.6
    threading.Thread(target=every, args=(3.0, lambda: call("settings.get"), stop),
                     daemon=True).start()
    if kind != "app":
        threading.Thread(target=every, args=(3.0, lambda: bad_client(kind), stop),
                         daemon=True).start()
    t_end = time.monotonic() + SECS
    while time.monotonic() < t_end:
        t = time.monotonic()
        dt, r = call("hello")
        lat.append(dt if dt is not None else 3.0)
        time.sleep(max(0, 0.2 - (time.monotonic() - t)))
    stop.set()
    time.sleep(2)
    _, st = call("status", timeout=5)
    link = (st or {}).get("link") or {}
    ms = sorted(x * 1000 for x in lat)
    rec = {"label": LABEL, "pattern": kind, "at": time.strftime("%F %T"), "n": len(ms),
           "p50": round(statistics.median(ms)), "p95": round(ms[int(len(ms) * 0.95) - 1]),
           "max": round(ms[-1]), "over100": sum(x > 100 for x in ms),
           "over200": sum(x > 200 for x in ms), "failed": sum(x >= 3000 for x in ms),
           "poll_max_ms": link.get("poll_max_ms"), "slow_polls": link.get("slow_polls"),
           "polls": link.get("polls")}
    with open(os.path.join(OUT, "latency.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    print("%-10s %-10s n=%3d p50=%4d p95=%4d max=%4d >100=%3d >200=%3d failed=%2d | brain poll_max_ms=%s slow_polls=%s"
          % (LABEL, kind, rec["n"], rec["p50"], rec["p95"], rec["max"], rec["over100"],
             rec["over200"], rec["failed"], rec["poll_max_ms"], rec["slow_polls"]), flush=True)


for _ in range(12):                      # a brain just reset: wait up to a minute
    dt, h = call("hello", timeout=5)
    if dt is not None:
        break
    time.sleep(5)
if dt is None:
    sys.exit("brain %s does not answer" % IP)
print("%s: rpc %s, uptime %s s" % (IP, (h or {}).get("rpc"), (h or {}).get("uptime")), flush=True)
PATTERNS = (sys.argv[4].split(",") if len(sys.argv) > 4
            else ["app", "late_body", "later_body", "half_open", "trickle"])
for k in PATTERNS:
    run(k)
