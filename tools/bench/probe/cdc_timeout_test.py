#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# cdc_timeout_test.py [HOLD_S] - DEV-172 workaround B check, run on the bench Pi (stops the product!).
# Simulates a jammed console: the host keeps the port OPEN (DTR on) but stops reading for HOLD_S s
# (default 15). On the brain, the test first writes 1 KB blocks via usb_cdc.console with
# write_timeout = 0.2 s until a write comes back short, then print()s the same amount.
# PASS for option B: the usb_cdc write gives up within ~0.2 s while the host is not reading,
# while print() blocks until the host reads again (= the hang path, issue #10814).
# Afterwards the brain is at the REPL: restart the product with `pico.py reset` (Ctrl-D).
import glob, re, sys, time
import serial

HOLD = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
DEVICE_CODE = b"""
import time, usb_cdc
c = usb_cdc.console
c.write_timeout = 0.2
time.sleep(1.5)                       # the host stops reading now
blob = b"x" * 1023 + b"\n"
n = 0; blocked = None; t0 = time.monotonic()
for i in range(2048):                 # up to 2 MB, stops at the first short write
    t1 = time.monotonic(); w = c.write(blob) or 0; dt = time.monotonic() - t1
    n += w
    if w < len(blob):
        blocked = round(dt, 3); break
cdc = (n, round(time.monotonic() - t0, 2), blocked)
t0 = time.monotonic()
for i in range(max(1, n // 1024)):
    print("y" * 1023)
pr = round(time.monotonic() - t0, 2)
print("RESULT cdc_bytes=%d cdc_total_s=%s cdc_short_write_s=%s print_s=%s" % (cdc[0], cdc[1], cdc[2], pr))
"""

p = glob.glob("/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00")[0]
s = serial.Serial(p, 115200, timeout=0.5)            # pyserial sets DTR -> tud_cdc_connected()


def read_until(token, limit):
    buf = b""; t = time.time()
    while time.time() - t < limit:
        buf += s.read(4096)
        if token in buf:
            return buf
    raise SystemExit("timeout waiting for %r, got %r" % (token, buf[-200:]))


s.write(b"\x03"); time.sleep(0.3); s.write(b"\x03")
read_until(b">>>", 10)
s.write(b"\x01")                                     # raw REPL
read_until(b"raw REPL; CTRL-B to exit\r\n>", 5)
s.write(DEVICE_CODE); s.write(b"\x04")
read_until(b"OK", 5)
print("device code started; host stops reading for %.0f s (port stays open)" % HOLD)
time.sleep(HOLD)                                     # not reading: the console jams
print("host reads again")
out = read_until(b"RESULT", 120)
out += s.read(4096)
m = re.search(rb"RESULT [^\r\n]*", out)
print(m.group(0).decode())
s.write(b"\x02")                                     # leave raw REPL
r = dict(kv.split("=") for kv in m.group(0).decode().split()[1:])
ok = r["cdc_short_write_s"] != "None" and float(r["cdc_short_write_s"]) < 1.0 and float(r["print_s"]) > HOLD / 3
print("PASS: usb_cdc write timed out, print() blocked while the host was not reading" if ok else
      "FAIL/INCONCLUSIVE: see numbers above")
