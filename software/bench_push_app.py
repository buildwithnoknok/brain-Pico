# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_push_app.py — push an application to a running module through the
# Conductor (update_module) and read its version back. Written for buzzer
# v3.4.0 (hardening C): the bench buzzer runs the LEGACY bootloader, which
# flashes apps fine, so this proves the app-side boot block runs — the stage-1
# interaction itself is proven on the LED Button (regress_all.sh step 3).
# Manual tool, not part of the regression; adjust the expected version below.
from noknok import Conductor
c = Conductor(); c.enumerate()
if not c.buzzer:
    raise SystemExit("FAIL: no buzzer")
m = c.buzzer[0]
print("before:", c._read_version(m.address) if hasattr(c, "_read_version") else "?")
entry = {"type": "buzzer", "bus": "i2c", "address": m.address, "uid": getattr(m, "_uid_hex", None)}
with open("buzzer_firmware.bin", "rb") as f:
    img = f.read()
print("pushing %d B via update_module() ..." % len(img))
c.update_module(entry, img)
import time; time.sleep(1.0)
c.enumerate()
m = c.buzzer[0]
# read GET_VERSION directly
c.i2c.try_lock()
try:
    c.i2c.writeto(m.address, bytes([0xB1])); b = bytearray(4); c.i2c.readfrom_into(m.address, b)
finally:
    c.i2c.unlock()
print("after : proto=%d v%d.%d.%d at 0x%02X" % (b[0], b[1], b[2], b[3], m.address))
if tuple(b[1:]) != (3, 4, 0):
    raise SystemExit("FAIL: expected v3.4.0")
print("ALL PASS - buzzer v3.4.0 (IWDG + health handshake) runs and enumerates")
