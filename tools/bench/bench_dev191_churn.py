#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev191_churn.py IP [SECONDS] [ARMS] - DEV-191: does the brain's WiFi link (ICMP ping)
# degrade when a client opens a NEW connection every few seconds, like the app's settings
# page does (settings.get every 3 s, one connection each)? No phone needed.
#
# Runs on the bench host (the Pi) against a RUNNING product. For each arm, SECONDS (default
# 180) long, a background `ping -i 0.5` measures the brain's reachability while the arm's
# traffic runs; the brain's own link counters (status.link: gw_ok/gw_miss, poll_max_ms,
# slow_polls) are read before and after. One summary line per arm + JSON in ~/dev191/churn.jsonl.
#
# ARMS (comma list, default idle,churn3,churn1,mixed):
#   idle    - no traffic (control: must be 0 % loss)
#   churn3  - settings.get every 3 s, new connection each (the app's settings page)
#   churn1  - settings.get every 1 s, new connection each (a harsher app)
#   mixed   - churn3 plus an extra request every 9 s (stand-in for a user changing values;\n#             settings.set needs product-specific keys, so this arm stays product-neutral)
# Hypothesis under test (code analysis, DEV-191): the connection churn (each request leaves a
# closed connection in the network stack's TIME_WAIT for ~2 min) exhausts the stack's
# connection/packet slots, so pings and the brain's own gateway pings start to be lost.
# Read poll_max_ms too: a hold of 5 s or more is the DEV-175 stall, not the churn.
import json, os, re, socket, subprocess, sys, threading, time

IP = sys.argv[1]
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 180
ARMS = (sys.argv[3] if len(sys.argv) > 3 else "idle,churn3,churn1,mixed").split(",")
OUT = os.path.expanduser("~/dev191")
os.makedirs(OUT, exist_ok=True)


def call(op, args=None, timeout=4.0):
    body = json.dumps({"id": 1, "op": op, "args": args or {}}).encode()
    head = ("POST /rpc HTTP/1.1\r\nHost: %s\r\nContent-Type: application/json\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n" % (IP, len(body))).encode()
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
        return json.loads(d.split(b"\r\n\r\n", 1)[1])
    except Exception:
        return None


def link():
    r = call("status")
    return (r or {}).get("link") or {}


def traffic(period, with_set, stop, counts):
    n = 0
    while not stop.is_set():
        r = call("settings.get")
        counts["req"] += 1
        counts["fail"] += 0 if r else 1
        n += 1
        if with_set and n % 3 == 0:
            r = call("settings.get")
            counts["req"] += 1
            counts["fail"] += 0 if r else 1
        stop.wait(period)


def run(arm):
    period = {"idle": None, "churn3": 3.0, "churn1": 1.0, "mixed": 3.0}[arm]
    before = link()
    p = subprocess.Popen(["ping", "-c", str(int(SECS * 2)), "-i", "0.5", IP],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    stop, counts = threading.Event(), {"req": 0, "fail": 0}
    th = None
    if period:
        th = threading.Thread(target=traffic, args=(period, arm == "mixed", stop, counts))
        th.start()
    out = p.communicate()[0]
    stop.set()
    if th:
        th.join()
    m = re.search(r"(\d+) packets transmitted, (\d+) received", out)
    sent, got = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    after = link()
    rec = {"arm": arm, "secs": SECS, "ping_sent": sent, "ping_lost": sent - got,
           "loss_pct": round(100.0 * (sent - got) / sent, 1) if sent else None,
           "req": counts["req"], "req_failed": counts["fail"],
           "gw_miss_delta": (after.get("gw_miss") or 0) - (before.get("gw_miss") or 0),
           "gw_ok_delta": (after.get("gw_ok") or 0) - (before.get("gw_ok") or 0),
           "poll_max_ms": after.get("poll_max_ms"), "slow_polls": after.get("slow_polls"),
           "cut_replies": after.get("cut_replies"), "rejoins": after.get("rejoins")}
    print(json.dumps(rec), flush=True)
    with open(os.path.join(OUT, "churn.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    time.sleep(60)          # let the network stack settle between arms (TIME_WAIT is ~2 min)


for a in ARMS:
    run(a)
