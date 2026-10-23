# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_foreign_device.py - DEV-119, tier S (host Python, no hardware).
#
# A fake two-bus world with noknok modules AND non-noknok I2C devices (a Qwiic /
# Stemma QT board at a fixed address). Proves:
#   skip     - enumerate() never hands out an address where a foreign device
#              answers, on either bus (one address space), and logs it once.
#   nak      - a device that ACKs the scan but NAKs reads is skipped as well.
#   phantom  - a remembered module is gone and a foreign board sits on its old
#              address: no noknok object is created; the state entry is kept.
#   busy     - a real module that misses the first GET_VERSION is still restored.
#   regress  - a noknok-only bus enumerates and restores exactly as before.
#
# Run from the repo root:   python -I tools/test_foreign_device.py

import io
import os
import sys
import types
import contextlib

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

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
for name in ("digitalio", "supervisor"):
    sys.modules[name] = types.ModuleType(name)


def crc8(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class NoknokDev:
    def __init__(self, uid_hex, mtype):
        self.uid = bytes.fromhex(uid_hex)
        self.type = mtype
        self.address = None          # None = waiting at 0x7F
        self.last_cmd = None
        self.miss_b1 = 0             # GET_VERSION replies to drop (module busy)
        self.armed = False           # a 0xB1 was just written (not a presence ping)

    def reply(self, buf):
        if self.last_cmd == 0xB1:
            if self.miss_b1 and self.armed:
                self.miss_b1 -= 1
                self.armed = False
                raise OSError(19)
            self.armed = False
            buf[:4] = bytes([1, 2, 3, 0])[:len(buf)]
        else:
            for i in range(len(buf)):
                buf[i] = 0


class ForeignDev:
    """A non-noknok chip at a fixed address: answers every read with its
    register contents (0x00 / 0x3C...), never the noknok protocol byte."""
    def __init__(self, address, nak_reads=False):
        self.address = address
        self.nak_reads = nak_reads
        self.writes = 0
        self.cmds = []

    def reply(self, buf):
        if self.nak_reads:
            raise OSError(19)
        for i in range(len(buf)):
            buf[i] = 0x3C if i == 0 else 0


PERIPHERAL = {("GP11", "GP10"): 1, ("GP21", "GP20"): 0}
WIRED = {}
OPEN = {}


class FakeBus:
    def __init__(self, scl, sda, frequency=100_000):
        key = (scl, sda)
        if key not in PERIPHERAL:
            raise ValueError("No pull up found on SDA or SCL; check your wiring")
        per = PERIPHERAL[key]
        if per in OPEN:
            raise ValueError("I2C peripheral in use")
        OPEN[per] = self
        self.per = per
        self.devs = WIRED.setdefault(key, [])
        self.pending = None

    def try_lock(self):
        return True

    def unlock(self):
        pass

    def deinit(self):
        OPEN.pop(self.per, None)

    def _at(self, addr):
        for d in self.devs:
            if d.address == addr:
                return d
        return None

    def readfrom_into(self, addr, buf):
        if addr == 0x7F:
            for d in self.devs:
                if isinstance(d, NoknokDev) and d.address is None:
                    self.pending = d
                    rec = d.uid + bytes([d.type])
                    buf[:10] = rec + bytes([crc8(rec)])
                    return
            raise OSError(19)
        d = self._at(addr)
        if d is None:
            raise OSError(19)
        d.reply(buf)

    def writeto(self, addr, data):
        data = bytes(data)
        if addr == 0x7F:
            if self.pending is None or data[0] != 0x1D:
                raise OSError(19)
            self.pending.address = data[1]
            self.pending = None
            return
        d = self._at(addr)
        if d is None:
            raise OSError(19)
        if isinstance(d, ForeignDev):
            d.writes += 1
            d.cmds.append(data[0] if data else None)
        else:
            d.last_cmd = data[0] if data else None
            d.armed = d.last_cmd == 0xB1

    def scan(self):
        return sorted(d.address for d in self.devs if d.address)


busio = types.ModuleType("busio")
busio.I2C = FakeBus
sys.modules["busio"] = busio

import noknok   # noqa: E402
noknok.time.sleep = lambda s: None

MOD0011 = {"NOKNOK_I2C_SDA": "GP10", "NOKNOK_I2C_SCL": "GP11",
           "NOKNOK_I2C_B_SDA": "GP20", "NOKNOK_I2C_B_SCL": "GP21"}
os.environ.update(MOD0011)
A, B = ("GP11", "GP10"), ("GP21", "GP20")
BUZ, KNOB, LEDB = "cdab67a8bcbdf011", "cdabaaa8bcbd3312", "cdab315428be26be"

fails = 0
def check(name, cond, extra=""):
    global fails
    print("  %-66s %s %s" % (name, "PASS" if cond else "FAIL", extra if not cond else ""))
    if not cond:
        fails += 1


def boot(keep_store=True):
    """New VM: buses released, Store re-read; modules keep their addresses
    (a brain reload, not a power cycle) unless power_cycle() was called."""
    for b in list(OPEN.values()):
        b.deinit()
    noknok._pin_config = None
    noknok._store = None
    if not keep_store:
        mc.nvm[:] = bytearray(4096)
    c = noknok.Conductor()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        c.enumerate()
    return c, out.getvalue()


def power_cycle():
    for devs in WIRED.values():
        for d in devs:
            if isinstance(d, NoknokDev):
                d.address = None


def addrs(c):
    return {m._uid_hex: m.address for m in c._registry.values() if m is not None}


# ── skip ──────────────────────────────────────────────────────────────────────
print("skip: addresses where a foreign device answers are never handed out")
WIRED.clear()
oled = ForeignDev(0x08)                        # like an SSD1306, at the first free address
WIRED[A] = [oled, NoknokDev(BUZ, 0x01), NoknokDev(KNOB, 0x02)]
c, log = boot(keep_store=False)
got = addrs(c)
check("two modules found", len(got) == 2, repr(got))
check("...neither got 0x08 (foreign device there)", 0x08 not in got.values(), repr(got))
check("...the log names the skipped address", "device at 0x08 (bus A)" in log, log)
check("...the foreign device never got a write", oled.writes == 0, "writes=%d" % oled.writes)

WIRED.clear()
WIRED[A] = [NoknokDev(BUZ, 0x01)]
WIRED[B] = [ForeignDev(0x08)]                  # on the OTHER bus: same address space
c, log = boot(keep_store=False)
check("foreign device on bus B blocks 0x08 for a bus-A module too",
      addrs(c).get(BUZ) not in (None, 0x08), repr(addrs(c)))

print("nak: ACKs the scan, NAKs reads")
WIRED.clear()
WIRED[A] = [ForeignDev(0x08, nak_reads=True), NoknokDev(BUZ, 0x01)]
c, log = boot(keep_store=False)
check("address still skipped", addrs(c).get(BUZ) not in (None, 0x08), repr(addrs(c)))

# ── phantom ───────────────────────────────────────────────────────────────────
print("phantom: a foreign board on a remembered module's old address")
WIRED.clear()
buz = NoknokDev(BUZ, 0x01)
WIRED[A] = [buz, NoknokDev(KNOB, 0x02)]
c, log = boot(keep_store=False)
old = addrs(c)[BUZ]
check("buzzer enumerated and saved", BUZ in c._state_map(), repr(c._state_map()))
WIRED[A].remove(buz)                           # buzzer unplugged ...
foreign = ForeignDev(old)                      # ... a Qwiic board at its old address
WIRED[A].append(foreign)
c, log = boot()
check("no buzzer object created", not c.buzzer and BUZ not in addrs(c), repr(addrs(c)))
check("...the log says why", "answers but not as a noknok module" in log, log)
check("...the knob still restores at its address", KNOB in addrs(c))
check("...the buzzer's state entry is kept (rescue needs UID -> type)",
      isinstance(c._state_map().get(BUZ), dict), repr(c._state_map().get(BUZ)))
check("...only GET_VERSION probes reached the foreign chip (max 3)",
      set(foreign.cmds) <= {0xB1} and len(foreign.cmds) <= 3, repr(foreign.cmds))
new = NoknokDev(LEDB, 0x03)                    # a newcomer must not get that address
WIRED[A].append(new)
c, log = boot()
check("a newcomer does not get the foreign device's address", addrs(c).get(LEDB) not in (None, old),
      repr(addrs(c)))

# ── busy ──────────────────────────────────────────────────────────────────────
print("busy: a real module that misses one GET_VERSION")
WIRED.clear()
buz = NoknokDev(BUZ, 0x01)
WIRED[A] = [buz]
c, log = boot(keep_store=False)
a0 = addrs(c)[BUZ]
buz.miss_b1 = 1
c, log = boot()
check("still restored at its address", addrs(c).get(BUZ) == a0, repr(addrs(c)))
check("...firmware version read", c.buzzer and c.buzzer[0].firmware_version == "2.3.0",
      repr(c.buzzer and c.buzzer[0].firmware_version))

# ── regress ───────────────────────────────────────────────────────────────────
print("regress: noknok-only buses behave as before")
WIRED.clear()
WIRED[A] = [NoknokDev(BUZ, 0x01), NoknokDev(KNOB, 0x02)]
WIRED[B] = [NoknokDev(LEDB, 0x03)]
c, log = boot(keep_store=False)
first = addrs(c)
check("three modules, unique addresses from 0x08",
      len(first) == 3 and sorted(first.values()) == [0x08, 0x09, 0x0A], repr(first))
check("...no 'skipped' line", "address skipped" not in log, log)
c, log = boot()
check("reload: all three restored at the same addresses", addrs(c) == first, repr(addrs(c)))
check("...no 'skipped' line", "address skipped" not in log, log)
power_cycle()
c, log = boot()
check("power cycle: same stable addresses", addrs(c) == first, repr(addrs(c)))

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
