# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_bus_clear.py - RDM-13 WP1, tier S (host Python, no hardware).
#
# noknok._bus_clear() against a fake bus (fake digitalio). Proves:
#   free     - SDA high: returns 0, SCL is never touched.
#   stuck    - a chip holds SDA low for n clock pulses: returns n, ends with a STOP
#              (SDA rises while SCL is high).
#   hopeless - SDA never released: returns -1 after 9 pulses.
#   off      - bus switched off (SDA and SCL read low): returns -2, SCL is never
#              driven (bench 17 Nov 2026: a PicoHub bus rail that is off reads 0/0;
#              the first patch clocked it and logged "still stuck").
#   busy     - SDA or SCL pin already in use (settings.toml typo: two buses share a
#              pin): returns 0, never raises (DEV-245 review), both pins released.
#
# Bench evidence: exploration repo rdm-13-third-party/bench/logs/2026-11-17-wp1/.
# Run from the repo root:   python -I tools/test_bus_clear.py

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))

board = types.ModuleType("board")
for n in range(29):
    setattr(board, "GP%d" % n, "GP%d" % n)
board.LED = "LED"
sys.modules["board"] = board
mc = types.ModuleType("microcontroller")
mc.nvm = bytearray(4096)
sys.modules["microcontroller"] = mc
for _name in ("supervisor", "busio"):
    sys.modules[_name] = types.ModuleType(_name)


class Bus:
    """Two open-drain lines. powered=False: no pull-up, both lines read 0.
    hold = SCL pulses until the stuck chip lets SDA go (None = never)."""
    def __init__(self, powered=True, hold=0):
        self.powered, self.hold = powered, hold
        self.drive = {"SDA": None, "SCL": None}    # None = input, else the driven level
        self.events = []

    def level(self, line):
        if not self.powered or self.drive[line] is False:
            return False
        if line == "SDA" and (self.hold is None or self.hold > 0):
            return False
        return True

    def set(self, line, value):
        before = self.level("SCL")
        self.drive[line] = bool(value)
        self.events.append((line, bool(value)))
        if line == "SCL" and not before and self.level("SCL") and self.hold:
            self.hold -= 1                          # one full pulse clocked out


BUS = None
PINS = {"GP10": "SDA", "GP11": "SCL"}
BUSY = set()                                        # pins another object holds


def make_digitalio():
    dio = types.ModuleType("digitalio")

    class Pull:
        UP, DOWN = "up", "down"

    class DriveMode:
        PUSH_PULL, OPEN_DRAIN = "pp", "od"

    class DigitalInOut:
        def __init__(self, pin):
            if pin in BUSY:
                raise ValueError("%s in use" % pin)
            self.line = PINS[pin]
        def switch_to_input(self, pull=None):
            BUS.drive[self.line] = None
        def switch_to_output(self, value=False, drive_mode=None):
            assert drive_mode == DriveMode.OPEN_DRAIN, "bus lines must be open-drain"
            BUS.set(self.line, value)
        @property
        def value(self):
            return BUS.level(self.line)
        @value.setter
        def value(self, v):
            BUS.set(self.line, v)
        def deinit(self):
            BUS.drive[self.line] = None

    dio.Pull, dio.DriveMode, dio.DigitalInOut = Pull, DriveMode, DigitalInOut
    return dio


sys.modules["digitalio"] = make_digitalio()
import noknok                                       # noqa: E402

fails = 0


def check(name, bus, expect, scl_driven):
    global BUS, fails
    BUS = bus
    got = noknok._bus_clear("GP10", "GP11")
    drove = any(line == "SCL" for line, _ in bus.events)
    ok = got == expect and drove == scl_driven
    if ok and expect > 0:                           # STOP: last SDA edge is a rise with SCL high
        sda = [e for e in bus.events if e[0] == "SDA"]
        ok = sda[-2:] == [("SDA", False), ("SDA", True)] and bus.level("SCL")
    print("%-9s %s  (got %s, expected %s)" % (name, "OK  " if ok else "FAIL", got, expect))
    fails += not ok


check("free", Bus(), 0, False)
check("stuck", Bus(hold=1), 1, True)
check("stuck-3", Bus(hold=3), 3, True)
check("hopeless", Bus(hold=None), -1, True)
check("off", Bus(powered=False), -2, False)


def check_busy(name, pin):
    global BUS, fails
    BUS = Bus(hold=1)
    BUSY.clear(); BUSY.add(pin)
    try:
        got = noknok._bus_clear("GP10", "GP11")
        ok = got == 0 and BUS.drive == {"SDA": None, "SCL": None}
    except Exception as e:
        got, ok = repr(e), False
    BUSY.clear()
    print("%-9s %s  (got %s, expected 0, no exception)" % (name, "OK  " if ok else "FAIL", got))
    fails += not ok


check_busy("busy-scl", "GP11")
check_busy("busy-sda", "GP10")
print("ALL PASS" if not fails else "%d FAILED" % fails)
sys.exit(1 if fails else 0)
