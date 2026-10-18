# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_two_bus.py - DEV-74, tier S (host Python, no hardware).
#
# A fake two-bus world (noknok PicoHub MOD-0011: bus A = GP10/GP11, bus B =
# GP20/GP21): fake modules sit on a bus, answer the 0x7F hand-out with their
# UID + type + CRC, take the address the Conductor assigns, and reply to
# GET_VERSION. Proves:
#   pins   - settings.toml is the ONLY pin source: no keys + no copy = no bus
#            and a clear problem; a typo or half pair = that group off; a full
#            set is kept as last-known-good and used when the file has no pin
#            keys at all; a file with some keys is authoritative.
#   buses  - modules on A and B enumerate into ONE registry, with unique
#            addresses across both buses, the driver bound to its own bus, and
#            "i2c_bus" in the saved state and in firmware_report().
#   moves  - a module moved from bus B to bus A keeps its address.
#   single - without bus B keys the brain is single-bus, exactly as before.
#   faults - bus B configured but unable to start: reported, bus A still works.
#   rescue - a module parked at 0x7E on bus B is found and reflashed on bus B.
#   deinit - Conductor.deinit() releases both buses.
#
# Run from the repo root:   python -I tools/test_two_bus.py

import os
import sys
import types

try:
    sys.stdout.reconfigure(encoding="utf-8")   # the library prints arrows; Windows consoles are cp1252
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))

# ── fake CircuitPython modules ───────────────────────────────────────────────
board = types.ModuleType("board")
for n in range(29):
    setattr(board, "GP%d" % n, "GP%d" % n)      # a pin is just its name here
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


class FakeModule:
    def __init__(self, uid_hex, mtype):
        self.uid = bytes.fromhex(uid_hex)
        self.uid_hex = uid_hex
        self.type = mtype
        self.address = None          # None = waiting at 0x7F for an address
        self.last_cmd = None

    def power_cycle(self):
        self.address = None


# Which pin pairs have a working bus (pull-ups), and its I2C peripheral:
# GP10/11 = I2C1, GP20/21 = I2C0, GP8/9 = I2C0 (the old default).
PERIPHERAL = {("GP11", "GP10"): 1, ("GP21", "GP20"): 0, ("GP9", "GP8"): 0}
WIRED = {}          # (scl, sda) -> list of FakeModule plugged into that bus
PARKED = {}         # (scl, sda) -> uid_hex of a module parked at 0x7E
OPEN = {}           # peripheral -> FakeBus currently holding it


class FakeBus:
    def __init__(self, scl, sda, frequency=100_000):
        key = (scl, sda)
        if key not in PERIPHERAL:
            raise ValueError("No pull up found on SDA or SCL; check your wiring")
        per = PERIPHERAL[key]
        if per in OPEN:
            raise ValueError("I2C peripheral in use")
        OPEN[per] = self
        self.key, self.per = key, per
        self.modules = WIRED.setdefault(key, [])
        self.pending = None          # module that answered 0x7F last
        self.dead = False

    def try_lock(self):
        return True

    def unlock(self):
        pass

    def deinit(self):
        self.dead = True
        OPEN.pop(self.per, None)

    def _at(self, addr):
        for m in self.modules:
            if m.address == addr:
                return m
        return None

    def readfrom_into(self, addr, buf):
        assert not self.dead, "read on a released bus"
        if addr == 0x7F:
            for m in self.modules:
                if m.address is None:
                    self.pending = m
                    rec = m.uid + bytes([m.type])
                    buf[:10] = rec + bytes([crc8(rec)])
                    return
            raise OSError(19)
        m = self._at(addr)
        if m is None:
            raise OSError(19)
        if m.last_cmd == 0xB1:
            buf[:4] = bytes([1, 3, 2, 1])[:len(buf)]
        else:
            for i in range(len(buf)):
                buf[i] = 0

    def writeto(self, addr, data):
        assert not self.dead, "write on a released bus"
        data = bytes(data)
        if addr == 0x7F:
            if self.pending is None or data[0] != 0x1D:
                raise OSError(19)
            self.pending.address = data[1]
            self.pending = None
            return
        m = self._at(addr)
        if m is None:
            raise OSError(19)
        m.last_cmd = data[0] if data else None

    def scan(self):
        return [m.address for m in self.modules if m.address]


busio = types.ModuleType("busio")
busio.I2C = FakeBus
sys.modules["busio"] = busio


# module_flasher fake for the rescue: parked state comes from PARKED
class FakeFlasher:
    flashed_on = []

    def __init__(self, bus):
        self.bus = bus

    def _read_status(self):
        return (3, 7) if self.bus.key in PARKED else None    # ST_ERROR, err 7

    def get_version(self):
        return (1, 1, 2, 0, 2)

    def get_uid(self):
        return PARKED[self.bus.key]

    def flash(self, image, runtime_addr=None, progress=None):
        FakeFlasher.flashed_on.append(self.bus.key)
        del PARKED[self.bus.key]


mf = types.ModuleType("module_flasher")
mf.ModuleFlasher = FakeFlasher
mf.ST_ERROR = 3
sys.modules["module_flasher"] = mf

import noknok   # noqa: E402
noknok.time.sleep = lambda s: None          # run the 3 s quiet window instantly

PIN_KEYS = ("NOKNOK_I2C_SDA", "NOKNOK_I2C_SCL", "NOKNOK_I2C_B_SDA", "NOKNOK_I2C_B_SCL",
            "NOKNOK_USB_DP", "NOKNOK_USB_DM")
MOD0011 = {"NOKNOK_I2C_SDA": "GP10", "NOKNOK_I2C_SCL": "GP11",
           "NOKNOK_I2C_B_SDA": "GP20", "NOKNOK_I2C_B_SCL": "GP21",
           "NOKNOK_USB_DP": "GP0", "NOKNOK_USB_DM": "GP1"}
A, B = ("GP11", "GP10"), ("GP21", "GP20")

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-64s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails += 1


def settings(values):
    """settings.toml as os.getenv sees it on the host."""
    for k in PIN_KEYS:
        os.environ.pop(k, None)
    os.environ.update(values)
    noknok._pin_config = None


def fresh_brain(keep_store=False):
    """New boot: new VM (pin cache, Store object), buses released."""
    for b in list(OPEN.values()):
        b.deinit()
    noknok._pin_config = None
    noknok._store = None
    if not keep_store:
        mc.nvm[:] = bytearray(4096)


def unplug_all():
    WIRED.clear()
    PARKED.clear()


# ── pins ─────────────────────────────────────────────────────────────────────
print("Pins come only from settings.toml")
unplug_all()
fresh_brain()
settings({})
cfg = noknok.pin_config()
check("no keys, no copy -> no group, source 'none'",
      cfg["source"] == "none" and not any(cfg[g] for g in ("i2c_a", "i2c_b", "usb")))
check("  ...and a problem says to install settings.toml",
      any("Install settings.toml" in p for p in cfg["problems"]), cfg["problems"])
c = noknok.Conductor()
check("  ...Conductor starts with no bus, enumerate() = 0",
      c.i2c is None and c.i2c_b is None and c.enumerate() == 0)

fresh_brain()
settings(dict(MOD0011, NOKNOK_I2C_B_SDA="GP99"))
cfg = noknok.pin_config()
check("typo 'GP99' -> bus B off, reported", cfg["i2c_b"] is None and
      any("GP99" in p for p in cfg["problems"]), cfg["problems"])
check("  ...bus A + USB still configured", cfg["i2c_a"] == ("GP10", "GP11") and cfg["usb"] == ("GP0", "GP1"))
check("  ...a set with problems is NOT kept as last-known-good",
      noknok.store().get(noknok.PINS_STORE_KEY) is None)

fresh_brain()
settings({"NOKNOK_I2C_SDA": "GP10"})
cfg = noknok.pin_config()
check("half a pair (SDA only) -> bus A off, reported",
      cfg["i2c_a"] is None and any("must both be set" in p for p in cfg["problems"]))

fresh_brain()
settings(MOD0011)
cfg = noknok.pin_config()
check("full MOD-0011 set -> A, B, USB, no problems",
      cfg["i2c_a"] == ("GP10", "GP11") and cfg["i2c_b"] == ("GP20", "GP21")
      and cfg["usb"] == ("GP0", "GP1") and not cfg["problems"])
check("  ...kept in the Store as last-known-good",
      noknok.store().get(noknok.PINS_STORE_KEY) == {"i2c_a": ["GP10", "GP11"],
                                                    "i2c_b": ["GP20", "GP21"], "usb": ["GP0", "GP1"]})

fresh_brain(keep_store=True)
settings({})
cfg = noknok.pin_config()
check("settings.toml lost -> last-known-good pins used",
      cfg["source"] == "last-known-good" and cfg["i2c_a"] == ("GP10", "GP11")
      and cfg["i2c_b"] == ("GP20", "GP21"))
check("  ...and reported ('Restore settings.toml')",
      any("Restore settings.toml" in p for p in cfg["problems"]), cfg["problems"])

fresh_brain(keep_store=True)
settings({"NOKNOK_I2C_SDA": "GP10", "NOKNOK_I2C_SCL": "GP11"})
cfg = noknok.pin_config()
check("file with some keys is authoritative: no B/USB keys -> B, USB off, no problem",
      cfg["source"] == "settings" and cfg["i2c_b"] is None and cfg["usb"] is None
      and not cfg["problems"])

fresh_brain()
settings({"NOKNOK_USB_DP": "GP0", "NOKNOK_USB_DM": "GP1"})
cfg = noknok.pin_config()
check("USB only, no I2C keys -> reported (I2C modules are off)",
      any("I2C modules are off" in p for p in cfg["problems"]), cfg["problems"])

# ── two buses ────────────────────────────────────────────────────────────────
print("\nTwo buses, one registry, one address space")
unplug_all()
fresh_brain()
settings(MOD0011)
buzz = FakeModule("1111111111111111", 0x01)
knob = FakeModule("2222222222222222", 0x02)
led1 = FakeModule("3333333333333333", 0x03)
led2 = FakeModule("4444444444444444", 0x03)
WIRED[A] = [buzz, led1]
WIRED[B] = [knob, led2]
c = noknok.Conductor()
n = c.enumerate()
check("4 modules found across A and B", n == 4, n)
addrs = [m.address for m in (buzz, knob, led1, led2)]
check("addresses unique across both buses", len(set(addrs)) == 4 and None not in addrs, addrs)
check("knob is on bus B and its driver talks to bus B",
      c.knob[0].i2c_bus == "B" and c.knob[0].i2c is c.i2c_b)
check("buzzer is on bus A and its driver talks to bus A",
      c.buzzer[0].i2c_bus == "A" and c.buzzer[0].i2c is c.i2c)
check("by_uid() works across buses", c.by_uid("2222222222222222") is c.knob[0])
check("GET_VERSION read on the module's own bus", c.knob[0].firmware_version == "3.2.1",
      c.knob[0].firmware_version)
state = noknok.store().get("state")
check("saved state records the bus per module",
      state["2222222222222222"]["i2c_bus"] == "B" and state["1111111111111111"]["i2c_bus"] == "A")
rep = c.firmware_report({"knob": {"version": "9.9.9", "url": "x", "layout": 2}})
k = [r for r in rep if r["type"] == "knob"]
b = [r for r in rep if r["type"] == "buzzer"]
check("firmware_report(): knob entry on bus B, buzzer entry on bus A",
      k and k[0]["i2c_bus"] == "B" and b and b[0]["i2c_bus"] == "A")
check("_entry_bus() routes the knob's update to bus B", c._entry_bus(k[0]) is c.i2c_b)
knob_addr = knob.address
c.deinit()
check("deinit() releases both buses", c.i2c is None and c.i2c_b is None and not OPEN)

print("\nA module moved from bus B to bus A keeps its address")
for m in (buzz, knob, led1, led2):
    m.power_cycle()
WIRED[B].remove(knob)
WIRED[A].append(knob)
fresh_brain(keep_store=True)
settings(MOD0011)
c = noknok.Conductor()
c.enumerate()
check("knob found on bus A", c.knob and c.knob[0].i2c_bus == "A")
check("  ...at the same address as before", knob.address == knob_addr, "%s vs %s" % (knob.address, knob_addr))
check("  ...state updated to bus A", noknok.store().get("state")["2222222222222222"]["i2c_bus"] == "A")
check("still 4 unique addresses",
      len({m.address for m in (buzz, knob, led1, led2)}) == 4)
c.deinit()

print("\nSingle bus (no bus B keys) behaves as before")
for m in (buzz, knob, led1, led2):
    m.power_cycle()
fresh_brain()
settings({"NOKNOK_I2C_SDA": "GP10", "NOKNOK_I2C_SCL": "GP11"})
c = noknok.Conductor()
n = c.enumerate()
check("only bus A modules (3), bus B not touched", n == 3 and c.i2c_b is None, n)
check("no problems, no bus problems", not noknok.pin_config()["problems"] and not c.bus_problems)
c.deinit()

print("\nBus B configured but cannot start")
for m in (buzz, knob, led1, led2):
    m.power_cycle()
fresh_brain()
settings({"NOKNOK_I2C_SDA": "GP8", "NOKNOK_I2C_SCL": "GP9",          # I2C0
          "NOKNOK_I2C_B_SDA": "GP20", "NOKNOK_I2C_B_SCL": "GP21"})   # also I2C0
WIRED[("GP9", "GP8")] = [FakeModule("5555555555555555", 0x01)]
c = noknok.Conductor()
check("bus A up, bus B off", c.i2c is not None and c.i2c_b is None)
check("  ...reported with the peripheral hint",
      any("bus B" in p and "same I2C peripheral" in p for p in c.bus_problems), c.bus_problems)
check("  ...bus A still enumerates", c.enumerate() == 1)
c.deinit()

print("\nRescue finds a module parked on bus B")
unplug_all()
fresh_brain()
settings(MOD0011)
WIRED[A] = []
WIRED[B] = []
noknok.store().set("state", {"6666666666666666": {"address": 0x09, "type": 0x02, "i2c_bus": "B"}})
PARKED[B] = "6666666666666666"
FakeFlasher.flashed_on = []
c = noknok.Conductor()
res = c.rescue_parked_module(lambda entry: b"\x00" * 64, logfn=lambda m: None)
check("rescue result on bus B, reflashed",
      res and res.get("i2c_bus") == "B" and res.get("action") == "reflashed", res)
check("  ...flashed through bus B", FakeFlasher.flashed_on == [B], FakeFlasher.flashed_on)
check("nothing parked -> None", c.rescue_parked_module(lambda e: b"x", logfn=lambda m: None) is None)
c.deinit()

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
