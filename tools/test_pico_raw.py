#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_pico_raw.py - DEV-173 host test (no board): pico.py's enter_raw() must get a board that is
# stuck in the RAW REPL back to the friendly prompt, and reset() must send Ctrl-B first.
# Run: python -I tools/test_pico_raw.py
import os, sys, types
sys.modules["serial"] = types.ModuleType("serial")        # pico.py imports pyserial at load
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pico


class FakeSer:
    """A board that is in raw REPL: silent to Ctrl-C, leaves raw REPL on Ctrl-B."""
    def __init__(self, raw):
        self.raw, self.out, self.sent = raw, b"", b""
    def reset_input_buffer(self): self.out = b""
    def flush(self): pass
    def write(self, b):
        self.sent += b
        for ch in b:
            if ch == 2 and self.raw:                       # Ctrl-B
                self.raw, self.out = False, self.out + b"\r\nCircuitPython\r\n>>> "
            elif ch == 2:
                self.out += b"\r\n>>> "
            elif ch == 1 and not self.raw:                 # Ctrl-A
                self.raw, self.out = True, self.out + b"raw REPL; CTRL-B to exit\r\n>"
    def read(self, n):
        c, self.out = self.out[:n], self.out[n:]
        return c


def mk(raw):
    p = pico.Pico.__new__(pico.Pico)
    p.ser = FakeSer(raw)
    return p


# 1. normal friendly-REPL board: enter_raw works
p = mk(False); p.enter_raw(); assert p.ser.raw, "friendly -> raw failed"
# 2. board left in raw REPL by a killed run: the leading Ctrl-B gets it out, enter_raw works
p = mk(True); p.enter_raw(); assert p.ser.raw, "raw -> raw failed"
# 3. reset() starts with Ctrl-B
p = mk(True); pico.time.sleep = lambda s: None; p.reset()
assert p.ser.sent.startswith(b"\x02\x03\x03"), p.ser.sent
print("test_pico_raw: 3/3 PASS")
