#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""DEV-286 / DEV-293 two-client stall test with a timeline (run on a bench Pi).

Same load as Bob's b286m.sh: a poller (1/s x40) + a burst of 30 back-to-back
`mem` calls + ping 5/s to the brain. Adds a CONTROL ping to the router over the
same WiFi, so a lossy evening on the Pi's own link is not blamed on the brain,
and one line per request (start, latency, ok) so the onset of a stall is visible.

  python3 two_client_stall.py <brain-ip> <label> [poller-op] [poller-args-json] [gw-ip]
Prints a summary line; writes <label>.tl.csv (t, kind, ms, ok) next to it.
Env: KA=1 = each client keeps ONE HTTP/1.1 connection open (like the app),
     BURST=n = burst size (default 30), POLLER=0 = burst only (single client),
     PINGS=n = pings per target (default 300, 5/s).
"""
import json, os, socket, subprocess, sys, threading, time

KA = os.environ.get("KA") == "1"
BURST = int(os.environ.get("BURST", "30"))
POLLER = os.environ.get("POLLER", "1") != "0"
PINGS = int(os.environ.get("PINGS", "300"))
IP, LABEL = sys.argv[1], sys.argv[2]
OP = sys.argv[3] if len(sys.argv) > 3 else "status"
ARGS = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
GW = sys.argv[5] if len(sys.argv) > 5 else "192.168.1.1"
CRLF = b"\r\n"
T0 = time.time()
rows, lock = [], threading.Lock()


def log(kind, ms, ok):
    with lock:
        rows.append((round(time.time() - T0, 2), kind, ms, int(ok)))


def request(rid, op, args, close):
    body = json.dumps({"id": rid, "op": op, "args": args}).encode()
    head = [b"POST /rpc HTTP/1.1", b"Host: " + IP.encode(),
            b"Content-Type: application/json", b"Content-Length: %d" % len(body)]
    if close:
        head.append(b"Connection: close")
    return CRLF.join(head) + CRLF + CRLF + body


class Keep:
    """One kept-alive connection (KA=1); reopened when the brain closes it."""
    def __init__(self):
        self.s = None
        self.buf = b""

    def call(self, req, timeout):
        if self.s is None:
            self.s = socket.create_connection((IP, 80), timeout=timeout)
            self.buf = b""
        self.s.settimeout(timeout)
        self.s.sendall(req)
        while CRLF + CRLF not in self.buf:
            c = self.s.recv(4096)
            if not c:
                raise OSError("closed")
            self.buf += c
        hdr, _, rest = self.buf.partition(CRLF + CRLF)
        n = 0
        for line in hdr.split(CRLF):
            k, _, v = line.partition(b":")
            if k.strip().lower() == b"content-length":
                n = int(v)
        while len(rest) < n:
            c = self.s.recv(4096)
            if not c:
                raise OSError("closed")
            rest += c
        self.buf = rest[n:]
        if b"connection: close" in hdr.lower():
            self.s.close()
            self.s = None
        return rest[:n]


def rpc(op, args, timeout, rid, keep=None):
    """One POST /rpc; on a fresh connection (like curl) unless keep. Returns (ok, ms)."""
    t = time.time()
    try:
        if keep is not None:
            js = keep.call(request(rid, op, args, False), timeout)
        else:
            # Like curl / urllib / the app's http client: read Content-Length bytes,
            # then close our end (do not wait for the brain to close first).
            one = Keep()
            try:
                js = one.call(request(rid, op, args, True), timeout)
            finally:
                if one.s is not None:
                    one.s.close()
        return bool(json.loads(js).get("ok")), int((time.time() - t) * 1000)
    except Exception:
        if keep is not None:
            try:
                keep.s.close()
            except Exception:
                pass
            keep.s = None
        return False, int((time.time() - t) * 1000)


def pinger(host, kind, n):
    for _ in range(n):
        t = time.time()
        r = subprocess.run(["ping", "-c", "1", "-W", "1", host], capture_output=True)
        log(kind, int((time.time() - t) * 1000), r.returncode == 0)
        time.sleep(max(0, 0.2 - (time.time() - t)))


res = {}


def poller():
    ok = 0
    k = Keep() if KA else None
    for i in range(40):
        t = time.time()
        good, ms = rpc(OP, ARGS, 5, i, k)
        ok += good
        log("poll", ms, good)
        time.sleep(max(0, 1 - (time.time() - t)))
    res["poll"] = ok


def burst():
    ok = 0
    t = time.time()
    k = Keep() if KA else None
    for i in range(BURST):
        good, ms = rpc("mem", {}, 8, 100 + i, k)
        ok += good
        log("burst", ms, good)
    res["burst"] = (ok, round(time.time() - t))


th = [threading.Thread(target=pinger, args=(IP, "ping", PINGS)),
      threading.Thread(target=pinger, args=(GW, "gw", PINGS))]
for x in th:
    x.start()
time.sleep(5)
if POLLER:
    p = threading.Thread(target=poller)
    p.start()
    th.append(p)
    time.sleep(3)
else:
    res["poll"] = 0
burst()
for x in th:
    x.join()


def loss(kind):
    r = [x for x in rows if x[1] == kind]
    return 100.0 * sum(1 for x in r if not x[3]) / max(1, len(r))


with open(LABEL + ".tl.csv", "w") as f:
    for r in rows:
        f.write("%s,%s,%s,%s\n" % r)
print("%s ka=%d poller(%s %s) %d/40 burst %d/%d in %ds ping-loss %.1f%% gw-loss %.1f%%" % (
    LABEL, KA, OP if POLLER else "-", json.dumps(ARGS), res["poll"], res["burst"][0], BURST,
    res["burst"][1], loss("ping"), loss("gw")))
