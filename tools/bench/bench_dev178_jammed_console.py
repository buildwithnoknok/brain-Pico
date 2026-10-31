#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev178_jammed_console.py IP [MINUTES] - DEV-178 acceptance: the host holds the brain's
# console OPEN and does NOT read, while the app keeps the product busy. Every second: one
# settings.set with an unknown key (code.py logs "[rpc] settings.set ..." and noknok.py logs
# "rejected": ~150 console bytes, no flash write) and one hello. Before DEV-178 the console's
# buffer fills within a minute or two and the next print() hangs the whole brain: /rpc stops.
# With DEV-178 /rpc keeps answering for the whole hold. At the end the port is read again and
# the console must still work (Ctrl-C gives a prompt is NOT tried: the product keeps running).
# DEV178_BASELINE=1: the same traffic with the console not opened (the normal miss rate).
# Run on the bench Pi with nothing else on the console (reserve the rig: ~/BENCH_IN_USE).
import glob, json, socket, sys, time
import serial

IP = sys.argv[1]
MIN = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0


def rpc(op, args=None, timeout=4):
    b = json.dumps({"id": 1, "op": op, "args": args or {}}).encode()
    t0 = time.time()
    try:
        c = socket.create_connection((IP, 80), timeout=timeout)
        c.sendall(b"POST /rpc HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  b"Content-Length: %d\r\n\r\n" % len(b) + b)
        d = b""
        while True:
            x = c.recv(4096)
            if not x:
                break
            d += x
        c.close()
        json.loads(d.split(b"\r\n\r\n", 1)[1])
        return time.time() - t0
    except Exception:
        return None


import os
BASELINE = os.environ.get("DEV178_BASELINE") == "1"    # same traffic, console NOT opened
port = glob.glob("/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00")[0]
con = None
if not BASELINE:
    con = serial.Serial(port, 115200, timeout=0.2)  # opens with DTR on: the brain sees a host
    con.reset_input_buffer()
print("%s for %.0f min; app traffic 1/s"
      % ("BASELINE: console closed" if BASELINE else "console open, NOT reading", MIN), flush=True)
t_end = time.time() + MIN * 60
n = bad = run = longest_run = 0
worst = 0.0
first_bad = None
while time.time() < t_end:
    a = rpc("settings.set", {"values": {"dev178_no_such_key": n}})
    h = rpc("hello")
    n += 1
    for r in (a, h):
        run = run + 1 if r is None else 0
        longest_run = max(longest_run, run)
        if r is None:
            bad += 1
            if first_bad is None:
                first_bad = time.time() - (t_end - MIN * 60)
                print("  first failed call after %.0f s" % first_bad, flush=True)
        else:
            worst = max(worst, r)
    if n % 60 == 0:
        print("  %3d s: %d rounds, %d failed calls, slowest %.2f s"
              % (n, n, bad, worst), flush=True)
    time.sleep(1)
if con is not None:
    got = b""
    t0 = time.time()
    while time.time() - t0 < 5:
        got += con.read(65536)
    print("host reads again: %d console bytes waiting" % len(got))
print("after: hello %s" % ("ok" if rpc("hello") is not None else "FAILED"))
# A frozen brain fails every call from then on; a healthy one misses the odd call (the same
# ~1 % shows with the console closed: DEV178_BASELINE=1, bench 31 Oct). So: no run of 5 failed
# calls in a row, and under 2 % failed.
ok = longest_run < 5 and bad < 0.02 * 2 * n
print("%s: %d rounds, %d failed calls (longest run %d), slowest %.2f s"
      % ("PASS" if ok else "FAIL", n, bad, longest_run, worst))
