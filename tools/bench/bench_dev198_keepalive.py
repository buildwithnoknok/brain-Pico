#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev198_keepalive.py IP [SECONDS] [ARMS] - DEV-198: does HTTP keep-alive (noknok_rpc
# 0.7.0) remove the brain's ping loss under a fast request rate? No phone needed.
#
# Same method as bench_dev191_churn.py: runs on the bench host (the Pi) against a RUNNING
# product; per arm, SECONDS (default 150) long, a background `ping -i 0.5` measures the
# brain's reachability while the arm's traffic runs; status.link is read before and after
# (requests / reused come from noknok_rpc 0.7.0; older brains report none). One JSON line per
# arm, also appended to ~/dev198/keepalive.jsonl. 60 s settle between arms.
#
# ARMS (comma list, default idle,new1,ka1,ka3):
#   idle  - no traffic (control: must be ~0 % loss)
#   new1  - settings.get every 1 s, a NEW connection each, "Connection: close"
#           (= the app 1.11.3 rate against a brain that closes after every reply;
#           DEV-191 churn1 = 71.7 %, DEV-198 verdict with the real app = ~44 %)
#   ka1   - settings.get every 1 s on ONE kept connection (HTTP/1.1 keep-alive, like the
#           app's single http.Client once the brain keeps the connection)
#   ka3   - settings.get every 0.33 s on one kept connection (= the old app's 300 ms pace,
#           three times the 1.11.3 rate)
# A kept connection the brain closed is reopened at once and counted in "reconnects".
import http.client, json, os, re, socket, subprocess, sys, threading, time

IP = sys.argv[1]
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 150
ARMS = (sys.argv[3] if len(sys.argv) > 3 else "idle,new1,ka1,ka3").split(",")
OUT = os.path.expanduser("~/dev198")
os.makedirs(OUT, exist_ok=True)
PERIOD = {"idle": None, "new1": 1.0, "ka1": 1.0, "ka3": 1.0 / 3}


def call_new(op, args=None, timeout=4.0):
    """One request on a new connection that is closed after the reply."""
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


class Kept:
    """One HTTP/1.1 connection reused for every request (http.client keeps it open as
    long as the server does not say Connection: close)."""
    def __init__(self):
        self.conn = None
        self.reconnects = 0
        self.closed_by_server = 0

    def call(self, op, args=None, timeout=4.0):
        body = json.dumps({"id": 1, "op": op, "args": args or {}})
        for attempt in (1, 2):
            if self.conn is None:
                self.conn = http.client.HTTPConnection(IP, 80, timeout=timeout)
                self.reconnects += 1
            try:
                self.conn.request("POST", "/rpc", body, {"Content-Type": "application/json"})
                r = self.conn.getresponse()
                data = r.read()
                if (r.getheader("Connection") or "").lower() == "close":
                    self.closed_by_server += 1
                    self.conn.close()
                    self.conn = None
                return json.loads(data)
            except Exception:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
                if attempt == 2:
                    return None
        return None


def link():
    r = call_new("status")
    return (r or {}).get("link") or {}


def traffic(arm, stop, counts):
    period = PERIOD[arm]
    kept = Kept() if arm.startswith("ka") else None
    while not stop.is_set():
        t = time.monotonic()
        r = kept.call("settings.get") if kept else call_new("settings.get")
        counts["req"] += 1
        counts["fail"] += 0 if r else 1
        stop.wait(max(0.0, period - (time.monotonic() - t)))
    if kept:
        counts["reconnects"] = kept.reconnects
        counts["closed_by_server"] = kept.closed_by_server
        if kept.conn:
            kept.conn.close()


def delta(a, b, k):
    if a.get(k) is None or b.get(k) is None:
        return None
    return b[k] - a[k]


def run(arm):
    before = link()
    p = subprocess.Popen(["ping", "-c", str(int(SECS * 2)), "-i", "0.5", IP],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    stop, counts = threading.Event(), {"req": 0, "fail": 0}
    th = None
    if PERIOD[arm]:
        th = threading.Thread(target=traffic, args=(arm, stop, counts))
        th.start()
    out = p.communicate()[0]
    stop.set()
    if th:
        th.join()
    m = re.search(r"(\d+) packets transmitted, (\d+) received", out)
    sent, got = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    time.sleep(1)
    after = link()
    rec = {"arm": arm, "secs": SECS, "t": time.strftime("%H:%M:%S"),
           "ping_sent": sent, "ping_lost": sent - got,
           "loss_pct": round(100.0 * (sent - got) / sent, 1) if sent else None,
           "req": counts["req"], "req_failed": counts["fail"],
           "reconnects": counts.get("reconnects"),
           "closed_by_server": counts.get("closed_by_server"),
           "brain_requests": delta(before, after, "requests"),
           "brain_reused": delta(before, after, "reused"),
           "ka_evicted": delta(before, after, "ka_evicted"),
           "gw_miss_delta": delta(before, after, "gw_miss"),
           "poll_max_ms": after.get("poll_max_ms"), "slow_polls": after.get("slow_polls"),
           "cut_replies": after.get("cut_replies"), "rejoins": after.get("rejoins"),
           "status_after": bool(after)}
    print(json.dumps(rec), flush=True)
    with open(os.path.join(OUT, "keepalive.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    time.sleep(60)          # let the network stack settle between arms


for a in ARMS:
    run(a)
