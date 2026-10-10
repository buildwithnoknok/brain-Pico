#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# serial_listen.py [SECONDS] [--reboot | --ctrlc] - listen to a running brain.
#
# Runs on the bench host. Default: listen only for SECONDS (default 20), sending nothing, and
# reopen the port if the brain resets itself. Prints the byte count at the end.
#   --reboot  first send Ctrl-C + Ctrl-D (soft reboot), then record code.py's start.
#   --ctrlc   listen, then send ONE Ctrl-C and listen 4 s more: tells "busy but alive" (REPL
#             banner comes back) from "console dead" (0 bytes before and after, cf. DEV-107).
# Only ONE program may read the port at a time.
import glob, sys, time
import serial

PORT = "/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00"
args = [a for a in sys.argv[1:] if not a.startswith("--")]
N = float(args[0]) if args else 20
s = None

def port():
    global s
    while s is None:
        try:
            s = serial.Serial(glob.glob(PORT)[0], 115200, timeout=0.3)
        except Exception:
            time.sleep(0.2)
    return s

def listen(sec):
    global s
    end, got = time.time() + sec, 0
    while time.time() < end:
        try:
            d = port().read(4096)
        except Exception:
            s = None
            continue
        if d:
            got += len(d)
            sys.stdout.write(d.decode("utf-8", "replace"))
            sys.stdout.flush()
    return got

if "--reboot" in sys.argv:
    port().write(b"\x03")
    time.sleep(0.3)
    port().write(b"\x04")
print("\n[received %d bytes in %.0f s]" % (listen(N), N))
if "--ctrlc" in sys.argv:
    port().write(b"\x03")
    print("[sent Ctrl-C]")
    print("\n[received %d bytes after Ctrl-C]" % listen(4))
