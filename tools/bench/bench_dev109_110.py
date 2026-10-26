#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev109_110.py [IP] - DEV-109 + DEV-110 on a running brain over WiFi (no USB contact).
#   DEV-109: settings.set {"turbo": true} (not declared by the product) -> rejected
#            "unknown setting", and settings.get does not list it.
#   DEV-110: POST /rpc with the headers first and the body DELAY s later (two TCP segments):
#            0.0 / 0.2 / 0.6 s must be answered normally (inside the 1 s grace);
#            1.5 s is outside the grace and must come back as "bad json" - not a hang.
# Runs on the bench host (stdlib only). Exit 0 = all PASS.
import json
import socket
import sys
import time
import urllib.request

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.194"
fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-62s %s %s" % (name, "PASS" if cond else "FAIL", "" if cond else extra))
    fails += 0 if cond else 1


def rpc(op, args=None):
    body = json.dumps({"id": 1, "op": op, "args": args or {}}).encode()
    r = urllib.request.Request("http://%s/rpc" % IP, data=body,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=20) as f:
        return json.loads(f.read().decode())


def split_post(delay):
    """Headers now, body `delay` s later, as two separate sends."""
    body = json.dumps({"id": 7, "op": "hello", "args": {}}).encode()
    head = ("POST /rpc HTTP/1.1\r\nHost: %s\r\nContent-Type: application/json\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n" % (IP, len(body))).encode()
    s = socket.create_connection((IP, 80), timeout=15)
    try:
        s.sendall(head)
        time.sleep(delay)
        s.sendall(body)
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        s.close()
    text = data.decode("utf-8", "replace")
    return json.loads(text.split("\r\n\r\n", 1)[1]) if "\r\n\r\n" in text else {}


print("DEV-109: undeclared settings key (brain %s)" % IP)
r = rpc("settings.set", {"scope": "product", "values": {"turbo": True}})
check("settings.set turbo -> rejected 'unknown setting'",
      (r.get("rejected") or {}).get("turbo") == "unknown setting" and "turbo" not in (r.get("changed") or {}),
      r)
g = rpc("settings.get", {"scope": "product"})
check("settings.get does not list turbo", "turbo" not in (g.get("values") or {}), g.get("values"))

print("\nDEV-110: body sent after the headers")
for delay in (0.0, 0.2, 0.6):
    try:
        r = split_post(delay)
    except Exception as e:
        r = {"error": repr(e)}
    check("body %.1f s after the headers -> answered (hello ok)" % delay,
          r.get("ok") is True and r.get("id") == 7, r)
    time.sleep(2)
try:
    r = split_post(1.5)
except Exception as e:
    r = {"error": repr(e)}
check("body 1.5 s after the headers (beyond the 1 s grace) -> 'bad json', no hang",
      r.get("error") == "bad json", r)
time.sleep(2)
h = rpc("hello")
check("brain still answers afterwards", h.get("ok") is True, h)
st = rpc("status")
print("  (status.last_bad_request: %s)" % st.get("last_bad_request"))

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
