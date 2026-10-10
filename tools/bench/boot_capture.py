#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# boot_capture.py [SECONDS] - record a brain's boot from the very first second.
#
# Runs on the bench host (the Pi). Waits until the Pico's serial port DISAPPEARS (someone
# unplugs the brain), then until it comes back, opens it at once and prints everything for
# SECONDS (default 60). It sends NOTHING, so it never interrupts code.py - unlike pico.py,
# which stops the program with Ctrl-C and misses the start of the boot.
#
# Typical use: start it in the background, ask for an unplug/replug, read the log:
#   nohup python3 -u tools/bench/boot_capture.py 60 > ~/boot.log 2>&1 &
# Only ONE program may read the port at a time (stop pico.py runs / other listeners first).
import glob, sys, time
import serial

N = float(sys.argv[1]) if len(sys.argv) > 1 else 60
PORT = "/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00"   # by-id: ttyACM numbering is not stable

print("waiting for unplug...", flush=True)
while glob.glob(PORT):
    time.sleep(0.1)
print("unplugged %s, waiting for replug..." % time.strftime("%T"), flush=True)
while not glob.glob(PORT):
    time.sleep(0.05)
t0 = time.time()
s = None
while s is None and time.time() - t0 < 5:
    try:
        s = serial.Serial(glob.glob(PORT)[0], 115200, timeout=0.3)
    except Exception:
        time.sleep(0.05)
print("replugged %s, port open after %.2f s" % (time.strftime("%T"), time.time() - t0), flush=True)
got = 0
while time.time() - t0 < N:
    try:
        d = s.read(4096)
    except Exception:                 # the brain resets itself (factory reset, reload): reopen
        s = None
        while s is None and time.time() - t0 < N:
            try:
                s = serial.Serial(glob.glob(PORT)[0], 115200, timeout=0.3)
            except Exception:
                time.sleep(0.2)
        continue
    if d:
        got += len(d)
        sys.stdout.write(d.decode("utf-8", "replace"))
        sys.stdout.flush()
print("\n[captured %d bytes in %.0f s]" % (got, N), flush=True)
