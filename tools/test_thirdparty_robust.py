# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_thirdparty_robust.py - DEV-248, tier S (host Python, no hardware).
#
# noknok_thirdparty must never raise into a product (ADR-011 decision 5). Proves the
# DEV-248 review fixes:
#   saved-*   - attach() with broken Store entries (type no driver has, key that does not
#               parse, entry that is not a dict, driver that raises in setup) logs and
#               skips them; good entries next to them still work.
#   xfer-*    - a transfer on a bus that was deinit'ed (ValueError / RuntimeError from
#               try_lock or the transfer) returns None and marks the chip lost; OSError as
#               before.
#   veml-keep - VEML7700 recovery after a power loss keeps the product's autorange /
#               start step (setup() without arguments reuses them).
# Run from the repo root:   python -I tools/test_thirdparty_robust.py

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))

# noknok_thirdparty imports noknok only inside try/except: a stub without a Store is enough.
nk = types.ModuleType("noknok")
STORE = {}


class _Store:
    def get(self, k):
        return STORE.get(k)

    def set(self, k, v):
        STORE[k] = v


nk.store = lambda: _Store()
nk._service = lambda: None
sys.modules["noknok"] = nk

import noknok_thirdparty as tp                       # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    print("%-12s %s  %s" % (name, "OK  " if ok else "FAIL", detail))
    fails += not ok


class Bus:
    """Fake busio.I2C. answers = {address: {reg: bytes}}; plain reads return reg None."""
    def __init__(self, answers, fail=None):
        self.answers, self.fail, self.locked = answers, fail, False

    def try_lock(self):
        if self.fail == "lock":
            raise ValueError("Object has been deinitialized and can no longer be used.")
        self.locked = True
        return True

    def unlock(self):
        self.locked = False

    def _chip(self, a):
        if self.fail == "xfer":
            raise RuntimeError("bus deinit'ed")
        if a not in self.answers:
            raise OSError(19)
        return self.answers[a]

    def writeto(self, a, buf):
        self._chip(a)

    def readfrom_into(self, a, buf):
        d = self._chip(a).get(None, b"\x00")
        buf[:len(d)] = d[:len(buf)]

    def writeto_then_readfrom(self, a, out, buf):
        d = self._chip(a).get(out[0], b"\x00" * len(buf))
        buf[:len(buf)] = (bytes(d) + b"\x00" * len(buf))[:len(buf)]


class Conductor:
    def __init__(self, bus):
        self.bus, self._registry, self._unknown = bus, {}, {}

    def _buses(self):
        return [("A", self.bus)]

    def _scan(self, bus):
        return sorted(bus.answers)


# 0x30 = Soldered PIR (no ID), 0x31 = second PIR, 0x48 = nothing our drivers claim.
bus = Bus({0x30: {None: b"\x00"}, 0x31: {None: b"\x00"}, 0x48: {}})
logs = []
STORE[tp.STORE_KEY] = {
    "A-30": {"type": "soldered_pir"},          # good
    "A-31": {"type": "no_such_driver"},        # removed / renamed driver, app typo
    "A48": {"type": "tmp102"},                 # key that does not parse
    "A-zz": {"type": "tmp102"},                # address that does not parse
    "A-49": "not a dict",                      # broken entry
}
c = Conductor(bus)
try:
    tp.attach(c, log=logs.append)
    raised = None
except Exception as e:
    raised = e
check("saved-noraise", raised is None, repr(raised))
check("saved-good", [m.TYPE for m in c.thirdparty] == ["soldered_pir"] and c.thirdparty[0].address == 0x30,
      c.thirdparty)
check("saved-logged", sum("skipped" in l for l in logs) == 4, [l for l in logs if "skipped" in l])
check("saved-unknown", sorted(u.address for u in c.unknown_devices) == [0x31, 0x48],
      c.unknown_devices)

# An explicit assign_map with a bad type (a product passing it by hand) must not raise either.
logs = []
try:
    tp.attach(c, log=logs.append, assign_map={("A", 0x30): "no_such_driver"})
    raised = None
except Exception as e:
    raised = e
check("named-bad", raised is None and any("saved naming" in l for l in logs), repr(raised))


# A driver whose setup() raises (bug) on the named path.
class Boom(tp.ThirdPartyModule):
    TYPE = "boom"

    def setup(self):
        raise RuntimeError("driver bug")


boom_mod = types.ModuleType("noknok_tp_boom")
boom_mod.DRIVER = Boom
sys.modules["noknok_tp_boom"] = boom_mod
_types = tp.TYPES
tp.TYPES = _types + ("boom",)
logs = []
try:
    tp.attach(c, log=logs.append, assign_map={("A", 0x30): "boom"})
    raised = None
except Exception as e:
    raised = e
tp.TYPES = _types
check("named-raise", raised is None and any("boom" in l for l in logs), repr(raised))

# Options of a present board whose saved entry is broken: defaults, no exception.
oled = tp.load("ssd1306")(bus, 0x3C, "A")
try:
    tp._apply_options(oled, "not a dict")
    tp._apply_options(oled, ["height", 64])
    tp._apply_options(oled, None)
    ok = True
except Exception as e:
    ok = repr(e)
check("opts-broken", ok is True, ok)

# _xfer / _probe_reg on a bus that is gone.
for name, fail in (("xfer-lock", "lock"), ("xfer-xfer", "xfer")):
    b = Bus({0x10: {0x00: b"\x00\x00"}}, fail=fail)
    m = tp.ThirdPartyModule(b, 0x10, "A")
    try:
        r = m._read_reg(0x00, 2)
        p = tp._probe_reg(b, 0x10, 0x07, 2)
        s = tp._probe_status(b, 0x10)
        ok, detail = r is None and p is None and s is None and m._lost and not b.locked, (r, p, s, m._lost)
    except Exception as e:
        ok, detail = False, repr(e)
    check(name, ok, detail)
b = Bus({})
m = tp.ThirdPartyModule(b, 0x10, "A")
check("xfer-oserr", m._read_reg(0x00, 2) is None and m._lost and not b.locked)

# VEML7700 keeps the product's choice through a recovery.
veml = __import__("noknok_tp_veml7700")
veml.time.sleep = lambda s: None
b = Bus({0x10: {0x00: b"\x00\x00", 0x07: b"\x81\xc4"}})
v = veml.VEML7700(b, 0x10, "A")
v.setup(step=3, autorange=False)
v._lost = True
v._recover()
check("veml-keep", v.autorange is False and v._step == 3 and v.start_step == 3,
      (v.autorange, v._step))
v2 = veml.VEML7700(b, 0x10, "A")
v2.setup()
check("veml-default", v2.autorange is True and v2._step == 1, (v2.autorange, v2._step))

print("ALL PASS" if not fails else "%d FAILED" % fails)
sys.exit(1 if fails else 0)
