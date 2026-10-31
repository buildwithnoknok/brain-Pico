#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# cdc_timeout_test.py STEP [HOLD_S] - DEV-178 workaround-B check, run on the bench Pi (stops the product!).
# Question: does a usb_cdc.console write with write_timeout give up while the host keeps the console
# port OPEN but does not read, where print() blocks (CircuitPython issue #10814 / DEV-172 path)?
#   STEP 1  harness check: raw-REPL exec of one print. Must show "HELLO-1". If not, nothing below counts.
#   STEP 2  the test with the host reading all the time (HOLD 0): RESULT must arrive, no short write.
#   STEP 3  the real case: host stops reading for HOLD_S s (default 15), port stays open.
#           PASS = usb_cdc write came back short within <1 s; print_s >= ~HOLD (print blocked).
# Every received chunk is logged with its time, so a failure shows WHERE it stopped.
# Run a continuous usbmon next to it: sudo cat /sys/kernel/debug/usb/usbmon/1u > usbmon.txt &
# Afterwards the brain sits at the REPL: restart the product with `pico.py reset` (Ctrl-D).
import glob, re, sys, time
import serial

STEP = int(sys.argv[1]) if len(sys.argv) > 1 else 1
HOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
if STEP == 2:
    HOLD = 0.0

CODE_1 = b'print("HELLO-" + str(1))\n'
CODE_23 = b"""print("START")
import time, usb_cdc
c = usb_cdc.console
print("console", c is not None)
c.write_timeout = 0.2
time.sleep(1.5)
blob = b"x" * 1023 + b"\\n"
n = 0
short = None
t0 = time.monotonic()
for i in range(2048):
    t1 = time.monotonic()
    w = c.write(blob) or 0
    dt = time.monotonic() - t1
    n += w
    if w < len(blob):
        short = round(dt, 3)
        break
cdc_total = round(time.monotonic() - t0, 2)
t0 = time.monotonic()
for i in range(max(1, n // 1024)):
    print("y" * 1023)
pr = round(time.monotonic() - t0, 2)
print("RESULT cdc_bytes=%d cdc_total_s=%s cdc_short_write_s=%s print_s=%s" % (n, cdc_total, short, pr))
"""

T0 = time.time()
p = glob.glob("/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00")[0]
s = serial.Serial(p, 115200, timeout=0.2)            # pyserial sets DTR -> tud_cdc_connected()
got = bytearray()


def log(msg):
    print("%7.2f %s" % (time.time() - T0, msg), flush=True)


def pump(limit, token=None, quiet=False, since=None):
    """Read for up to `limit` s (or until `token` appears in got[since:]; since defaults to now).
    Logs every chunk size. Returns True if the token was seen."""
    t = time.time(); start = len(got) if since is None else since
    if token and token in got[start:]:
        return True
    while time.time() - t < limit:
        d = s.read(8192)
        if d:
            got.extend(d)
            if not quiet:
                log("rx %5d B (total %d)  %r" % (len(d), len(got), bytes(d[-40:])))
            if token and token in got[start:]:
                return True
    return False


log("port open %s; leaving any raw REPL (Ctrl-B), then Ctrl-C x2" % p)
s.write(b"\x02"); time.sleep(0.2)
s.write(b"\x03"); time.sleep(0.3); s.write(b"\x03")
if not pump(10, b">>>", quiet=True):
    sys.exit("FAIL harness: no >>> after Ctrl-C (tail %r)" % bytes(got[-120:]))
log("at >>>; entering raw REPL")
s.write(b"\x01")
if not pump(5, b"raw REPL; CTRL-B to exit\r\n>", quiet=True):
    sys.exit("FAIL harness: no raw REPL prompt (tail %r)" % bytes(got[-120:]))
code = CODE_1 if STEP == 1 else CODE_23
s.write(code); s.write(b"\x04")
mark = len(got)
if not pump(5, b"OK", quiet=True, since=mark):
    sys.exit("FAIL harness: raw REPL did not answer OK (tail %r)" % bytes(got[-120:]))
mark = got.find(b"OK", mark)                         # everything after this is the code's output
log("code accepted (OK), step %d, hold %.0f s" % (STEP, HOLD))
if STEP == 1:
    ok = pump(5, b"HELLO-1", quiet=True, since=mark)
    s.write(b"\x02")
    log("PASS harness: HELLO-1 received" if ok else "FAIL harness: no HELLO-1 (tail %r)" % bytes(got[-120:]))
    sys.exit(0 if ok else 1)
if not pump(5, b"START", since=mark):
    log("WARNING: no START marker within 5 s")
if HOLD:
    log("host STOPS reading for %.0f s (port stays open)" % HOLD)
    time.sleep(HOLD)
    log("host reads again")
ok = pump(180, b"RESULT", quiet=True, since=mark)
pump(1, quiet=True)
s.write(b"\x02")
m = re.search(rb"RESULT [^\r\n]*", got)
log("received %d B in total; START %s; console line %r" % (
    len(got), b"START" in got, re.search(rb"console [A-Za-z]+", got).group(0) if re.search(rb"console [A-Za-z]+", got) else None))
if not m:
    err = re.search(rb"Traceback[^\x04]*", got)
    sys.exit("FAIL: no RESULT. %s tail %r" % ("Traceback: %r;" % err.group(0)[-300:] if err else "", bytes(got[-160:])))
log(m.group(0).decode())
r = dict(kv.split("=") for kv in m.group(0).decode().split()[1:])
if STEP == 2:
    log("PASS step 2 (harness + device code run end to end)" if r["cdc_short_write_s"] == "None" else
        "NOTE step 2: a write came back short although the host was reading")
else:
    ok = r["cdc_short_write_s"] != "None" and float(r["cdc_short_write_s"]) < 1.0 and float(r["print_s"]) > HOLD / 3
    log("PASS: usb_cdc write gave up while the host was not reading; print() blocked" if ok else
        "FAIL/INCONCLUSIVE: see the numbers")
