#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# listen.py - passively read the Pico's serial console for N seconds (default 10), sending nothing.
import glob, sys, time, serial
p = glob.glob("/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00")[0]
s = serial.Serial(p, 115200, timeout=0.5)
end = time.time() + (float(sys.argv[1]) if len(sys.argv) > 1 else 10)
buf = b""
while time.time() < end:
    buf += s.read(512)
print("bytes:", len(buf))
print(buf[-1500:].decode("utf-8", "replace"))
