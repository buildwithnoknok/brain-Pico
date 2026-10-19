# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_picohub.py - DEV-123, tier S (host Python, no hardware).
#
# A fake noknok PicoHub power manager (type 0x07) on a fake I2C bus speaks the
# command map of module-I2C-picohub-MOD-0011 firmware/readme.md byte for byte.
# Proves:
#   - enumeration makes it c.picohub (version read), None on a brain without one
#   - status() parses every field (incl. a negative temperature, flag bytes)
#   - a bad CRC is retried once; twice -> PicoHubError; board gone -> PicoHubError
#   - source_caps() parses the PDO list and refuses a bad count
#   - steering commands send the exact bytes (rail ids, want, ms little-endian,
#     key 0x5A) and return the board's result code; bad arguments -> ValueError
#   - after a Pico reboot the PicoHub (which keeps its address) is restored and
#     its address is never handed to a newcomer; a module type WITHOUT a driver
#     keeps its address reserved the same way
#   - 0x07 stays out of the firmware report (no OTA until DEV-124)
#
# Run from the repo root:   python -I tools/test_picohub.py

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

board = types.ModuleType("board")
for n in range(29):
    setattr(board, "GP%d" % n, "GP%d" % n)
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


def u16(v):
    v &= 0xFFFF
    return [v & 0xFF, v >> 8]


class Device:
    """A noknok module: 0x7F hand-out, then commands at its address."""
    def __init__(self, uid_hex, mtype):
        self.uid = bytes.fromhex(uid_hex)
        self.type = mtype
        self.address = None
        self.reply = b""
        self.writes = []
        self.present = True

    def on_write(self, data):
        self.writes.append(bytes(data))
        if data[0] == 0xB1:
            self.reply = bytes([1, 0, 2, 0])


class FakeHub(Device):
    def __init__(self, uid_hex="7777777777777777"):
        super().__init__(uid_hex, 0x07)
        self.bad_crc = 0          # corrupt the next N CRC replies
        self.results = {}         # command byte -> result code
        self.caps_count = None    # override the PDO count byte

    def status_bytes(self):
        b = [1, 2] + u16(20000) + u16(3000) + u16(19950) + u16(1234) + u16(5010) \
            + u16(-52) + [0x01 | 0x02 | 0x10, 0x08, 0x01 | 0x20, 0x01 | 0x04, 1]
        return b + [crc8(b)]

    def caps_bytes(self):
        pdos = [(5000, 3000), (9000, 3000), (15000, 3000), (20000, 2250)]
        n = len(pdos) if self.caps_count is None else self.caps_count
        b = [n]
        for mv, ma in pdos:
            b += u16(mv) + u16(ma)
        b += [crc8(b)]
        return b + [0] * (30 - len(b))

    def _crc_maybe_bad(self, b):
        if self.bad_crc:
            self.bad_crc -= 1
            b = list(b)
            b[-1] ^= 0xFF if len(b) == 20 else 0
            if len(b) != 20:                    # caps: break the CRC byte inside
                n = b[0] if b[0] <= 7 else 0
                b[1 + 4 * n] ^= 0xFF
        return bytes(b)

    def on_write(self, data):
        super().on_write(data)
        c = data[0]
        if c == 0x10:
            self.reply = self._crc_maybe_bad(self.status_bytes())
        elif c == 0x11:
            self.reply = self._crc_maybe_bad(self.caps_bytes())
        elif c in (0x12, 0x20, 0x21, 0x22, 0x23):
            self.reply = bytes([self.results.get(c, 0)])


class FakeBus:
    OPEN = {}

    def __init__(self, scl, sda, frequency=100_000):
        key = (scl, sda)
        if key not in WIRED:
            raise ValueError("No pull up found on SDA or SCL")
        self.devices = WIRED[key]
        self.pending = None

    def try_lock(self): return True
    def unlock(self): pass
    def deinit(self): pass

    def _at(self, addr):
        for d in self.devices:
            if d.present and d.address == addr:
                return d
        return None

    def readfrom_into(self, addr, buf):
        if addr == 0x7F:
            for d in self.devices:
                if d.present and d.address is None:
                    self.pending = d
                    rec = d.uid + bytes([d.type])
                    buf[:10] = rec + bytes([crc8(rec)])
                    return
            raise OSError(19)
        d = self._at(addr)
        if d is None:
            raise OSError(19)
        r = d.reply or bytes([0])
        for i in range(len(buf)):
            buf[i] = r[i] if i < len(r) else 0

    def writeto(self, addr, data):
        data = bytes(data)
        if addr == 0x7F:
            if self.pending is None:
                raise OSError(19)
            self.pending.address = data[1]
            self.pending = None
            return
        d = self._at(addr)
        if d is None:
            raise OSError(19)
        d.on_write(data)

    def scan(self):
        return [d.address for d in self.devices if d.present and d.address]


WIRED = {}
busio = types.ModuleType("busio")
busio.I2C = FakeBus
sys.modules["busio"] = busio

import noknok   # noqa: E402
noknok.time.sleep = lambda s: None
os.environ.update({"NOKNOK_I2C_SDA": "GP10", "NOKNOK_I2C_SCL": "GP11"})
A = ("GP11", "GP10")

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-64s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails += 1


def fresh_brain(keep_store=True):
    noknok._pin_config = None
    noknok._store = None
    if not keep_store:
        mc.nvm[:] = bytearray(4096)


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


# ── no PicoHub ───────────────────────────────────────────────────────────────
print("A brain without a PicoHub")
fresh_brain(keep_store=False)
WIRED[A] = [Device("1111111111111111", 0x01)]
c = noknok.Conductor()
c.enumerate()
check("c.picohub is None, the buzzer still enumerates", c.picohub is None and len(c.buzzer) == 1)

# ── enumeration ──────────────────────────────────────────────────────────────
print("\nEnumeration")
fresh_brain(keep_store=False)
hub = FakeHub()
buzz = Device("1111111111111111", 0x01)
WIRED[A] = [hub, buzz]
c = noknok.Conductor()
n = c.enumerate()
p = c.picohub
check("type 0x07 becomes c.picohub", isinstance(p, noknok.NoknokPicoHub), repr(p))
check("counted, version read (0.2.0)", n == 2 and p.firmware_version == "0.2.0", (n, p and p.firmware_version))
check("in the registry by UID", c.by_uid("7777777777777777") is p)
state = noknok.store().get("state")
check("saved with type 7 (its address is remembered)", state["7777777777777777"]["type"] == 0x07)
check("NOT in the firmware report (no OTA until DEV-124)",
      all(r["uid"] != "7777777777777777" for r in c.firmware_report({"picohub": {"version": "9.9.9"}})))

# ── status ───────────────────────────────────────────────────────────────────
print("\nstatus()")
s = p.status()
check("contract 20 V / 3 A, PD contract", s["contract_v"] == 20.0 and s["contract_a"] == 3.0
      and s["pd_state"] == "contract", s["pd_state"])
check("vin 19.95 V, iin 1.234 A, 5 V rail 5.01 V",
      s["vin"] == 19.95 and s["iin"] == 1.234 and s["v5v"] == 5.01)
check("negative temperature -5.2 C", s["temp_c"] == -5.2, s["temp_c"])
check("rails: bus A, bus B, buck on; usb, mezz off",
      s["rails"] == {"bus_a": True, "bus_b": True, "usb": False, "mezz": False, "buck": True})
check("inputs: minimum set", s["inputs"]["min_set"] and not s["inputs"]["usb_device"])
check("faults list", s["faults"] == ["input_overcurrent", "pd_failed"], s["faults"])
check("warnings list", s["warnings"] == ["limited_power", "hot"], s["warnings"])
check("reset cause watchdog", s["reset_cause"] == "watchdog")

hub.bad_crc = 1
check("one bad CRC -> retried, still parsed", p.status()["vin"] == 19.95)
hub.bad_crc = 2
check("two bad CRCs -> PicoHubError", raises(p.status, noknok.PicoHubError))

# ── source caps ──────────────────────────────────────────────────────────────
print("\nsource_caps()")
caps = p.source_caps()
check("4 PDOs, 20 V at 2.25 A last", caps == [(5.0, 3.0), (9.0, 3.0), (15.0, 3.0), (20.0, 2.25)], caps)
hub.bad_crc = 1
check("bad CRC once -> retried", len(p.source_caps()) == 4)
hub.caps_count = 9
check("count > 7 -> PicoHubError", raises(p.source_caps, noknok.PicoHubError))
hub.caps_count = None

# ── steering ─────────────────────────────────────────────────────────────────
print("\nSteering commands: exact bytes, result codes")
hub.writes.clear()
r = p.set_rail("usb", "auto")
check("set_rail('usb','auto') -> 20 02 02, applied", hub.writes[-1] == bytes([0x20, 2, 2]) and r == 0)
hub.results[0x20] = 1
r = p.set_rail("usb", True)
check("set_rail('usb', True) -> 20 02 01, INTERLOCK returned",
      hub.writes[-1] == bytes([0x20, 2, 1]) and r == p.INTERLOCK and p.last_result == 1)
hub.results[0x20] = 0
p.set_rail("bus_b", False)
check("set_rail('bus_b', False) -> 20 01 00", hub.writes[-1] == bytes([0x20, 1, 0]))
p.set_rail("mezz", "on")
check("set_rail('mezz', 'on') -> 20 03 01", hub.writes[-1] == bytes([0x20, 3, 1]))
check("set_rail('bus_a', ...) refused (bus A only cycles)",
      raises(lambda: p.set_rail("bus_a", False), ValueError))
p.cycle_rail("bus_a", 300)
check("cycle_rail('bus_a', 300) -> 21 00 2C 01 5A", hub.writes[-1] == bytes([0x21, 0, 0x2C, 0x01, 0x5A]))
p.reset_pico(100)
check("reset_pico(100) -> 22 64 00 5A", hub.writes[-1] == bytes([0x22, 0x64, 0x00, 0x5A]))
hub.results[0x23] = 2
r = p.set_min_input_voltage(20)
check("set_min_input_voltage(20) -> 23 14, NO_POWER returned",
      hub.writes[-1] == bytes([0x23, 20]) and r == p.NO_POWER)
p.set_min_input_voltage(None)
check("set_min_input_voltage(None) -> 23 00", hub.writes[-1] == bytes([0x23, 0]))
check("set_min_input_voltage(12) refused", raises(lambda: p.set_min_input_voltage(12), ValueError))
p.clear_faults()
check("clear_faults() -> 12", hub.writes[-1] == bytes([0x12]))
check("result text table", p.RESULT_TEXT[p.INTERLOCK] == "held off by an interlock")

hub.present = False
check("board gone: status() -> PicoHubError", raises(p.status, noknok.PicoHubError))
check("board gone: set_rail() -> PicoHubError", raises(lambda: p.set_rail("usb", "auto"), noknok.PicoHubError))
hub.present = True
hub_addr = hub.address

# ── Pico reboot: the hub keeps its address ───────────────────────────────────
print("\nPico reboot (the PicoHub keeps power and its address)")
odd = Device("9999999999999999", 0x09)      # a module type with no driver
WIRED[A].append(odd)
c.deinit()
fresh_brain()
c = noknok.Conductor()
c.enumerate()                                 # first boot with the odd module
odd_addr = odd.address
check("driver-less type 0x09 got an address and is saved",
      odd_addr and noknok.store().get("state")["9999999999999999"]["address"] == odd_addr)
c.deinit()
# reboot: modules keep their addresses (powered); a NEW module waits at 0x7F
new = Device("5555555555555555", 0x01)
WIRED[A].append(new)
fresh_brain()
c = noknok.Conductor()
c.enumerate()
check("PicoHub restored at its address", c.picohub is not None and c.picohub.address == hub_addr)
taken = {hub.address, buzz.address, odd.address}
check("the newcomer got a free address (not the hub's, not 0x09's)",
      new.address not in taken and new.address is not None, (new.address, sorted(taken)))
c.deinit()

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
