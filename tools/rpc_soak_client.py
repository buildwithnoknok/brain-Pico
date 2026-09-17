#!/usr/bin/env python3
"""rpc_soak_client.py — the phone stand-in for the DEV-34 /rpc soak.

Polls POST /rpc on the brain every INTERVAL seconds (the app's cadence from
Sam's review: 2–3 s) and judges reachability from the outside:

  * latency per request, max, distribution
  * failures (timeout / connection refused / bad reply), longest outage
  * every SLOW_EVERY-th request is a deliberately SLOW client: it sends half
    the HTTP headers, waits SLOW_PAUSE s, then the rest — this is the case
    Sam's point 2 asks about (poll() blocked on a client mid-request). The
    Pico's own log shows what that costs its loop; this log shows whether the
    request still completes.

Usage:  rpc_soak_client.py <pico-ip> [minutes]     (stdlib only, runs on the Pi)
Exit code 0 = PASS (no outage longer than OUTAGE_FAIL_S, < 1 % failures).
"""
import json
import socket
import sys
import time

INTERVAL      = 2.0
SLOW_EVERY    = 15          # every 15th request (≈ every 30 s) is a slow one
SLOW_PAUSE    = 3.0
TIMEOUT       = 5.0
OUTAGE_FAIL_S = 30.0        # a gap longer than this = FAIL
STATS_EVERY   = 60

host = sys.argv[1]
minutes = float(sys.argv[2]) if len(sys.argv) > 2 else 65
t0 = time.monotonic()

def log(msg):
    print("[%7.1f] %s" % (time.monotonic() - t0, msg), flush=True)

def rpc(msg, slow=False):
    """One POST /rpc. Returns (reply_dict, seconds) or raises."""
    body = json.dumps(msg).encode()
    head = ("POST /rpc HTTP/1.1\r\nHost: %s\r\nContent-Type: application/json\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n" % (host, len(body))).encode()
    t = time.monotonic()
    s = socket.create_connection((host, 80), timeout=TIMEOUT)
    try:
        if slow:
            cut = len(head) // 2
            s.sendall(head[:cut])
            time.sleep(SLOW_PAUSE)
            s.sendall(head[cut:] + body)
        else:
            s.sendall(head + body)
        raw = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            raw += chunk
            if b"\r\n\r\n" in raw:
                hdr, _, rest = raw.partition(b"\r\n\r\n")
                n = 0
                for line in hdr.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        n = int(line.split(b":")[1])
                if len(rest) >= n:
                    break
    finally:
        s.close()
    hdr, _, rest = raw.partition(b"\r\n\r\n")
    reply = json.loads(rest.decode())
    return reply, time.monotonic() - t

stats = {"n": 0, "ok": 0, "fail": 0, "slow_n": 0, "slow_ok": 0, "slow_fail": 0,
         "lat_sum": 0.0, "lat_max": 0.0, "slow_lat_max": 0.0,
         "outage_max": 0.0, "outages": 0}
last_ok = time.monotonic()
in_outage = False
next_stats = time.monotonic() + STATS_EVERY
end = time.monotonic() + minutes * 60
seq = 0
log("polling http://%s/rpc every %.0f s for %.0f min (slow client every %d)"
    % (host, INTERVAL, minutes, SLOW_EVERY))

while time.monotonic() < end:
    seq += 1
    slow = (seq % SLOW_EVERY == 0)
    stats["n"] += 1
    if slow:
        stats["slow_n"] += 1
    try:
        reply, dt = rpc({"id": seq, "op": "hello"}, slow=slow)
        if reply.get("id") != seq or not reply.get("ok"):
            raise ValueError("bad reply %r" % (reply,))
        stats["ok"] += 1
        if slow:
            stats["slow_ok"] += 1
            stats["slow_lat_max"] = max(stats["slow_lat_max"], dt)
        else:
            stats["lat_sum"] += dt
            stats["lat_max"] = max(stats["lat_max"], dt)
        if in_outage:
            gap = time.monotonic() - last_ok
            log("recovered after %.1f s outage" % gap)
            stats["outage_max"] = max(stats["outage_max"], gap)
            in_outage = False
        last_ok = time.monotonic()
    except Exception as e:
        if slow:
            # Expected: the server drops a client that stalls longer than its
            # socket_timeout. Counted, never judged — the number that matters
            # is what the stall cost the Pico's loop (its own log).
            stats["slow_fail"] += 1
            continue
        stats["fail"] += 1
        if not in_outage:
            stats["outages"] += 1
            in_outage = True
        log("FAIL #%d: %r" % (seq, e))
    if time.monotonic() >= next_stats:
        next_stats += STATS_EVERY
        fast = max(1, stats["ok"] - stats["slow_ok"])
        log("stats n=%d ok=%d fail=%d avg=%.0fms max=%.0fms | slow ok=%d/%d max=%.1fs | outages=%d longest=%.1fs"
            % (stats["n"], stats["ok"], stats["fail"], 1000 * stats["lat_sum"] / fast,
               1000 * stats["lat_max"], stats["slow_ok"], stats["slow_n"],
               stats["slow_lat_max"], stats["outages"], stats["outage_max"]))
    time.sleep(INTERVAL)

if in_outage:
    stats["outage_max"] = max(stats["outage_max"], time.monotonic() - last_ok)
fast = max(1, stats["ok"] - stats["slow_ok"])
fail_pct = 100.0 * stats["fail"] / max(1, stats["n"] - stats["slow_n"])
verdict = []
if stats["outage_max"] > OUTAGE_FAIL_S: verdict.append("outage %.0f s" % stats["outage_max"])
if fail_pct >= 1.0:                      verdict.append("%.1f %% failures" % fail_pct)
log("FINAL n=%d ok=%d fail=%d (%.2f %%) avg=%.0fms max=%.0fms | slow ok=%d dropped=%d of %d max=%.1fs | outages=%d longest=%.1fs"
    % (stats["n"], stats["ok"], stats["fail"], fail_pct, 1000 * stats["lat_sum"] / fast,
       1000 * stats["lat_max"], stats["slow_ok"], stats["slow_fail"], stats["slow_n"],
       stats["slow_lat_max"], stats["outages"], stats["outage_max"]))
log("CLIENT %s" % ("PASS" if not verdict else "FAIL: " + "; ".join(verdict)))
sys.exit(0 if not verdict else 1)
