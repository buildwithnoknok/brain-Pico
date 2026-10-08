# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_layout_gate.py - DEV-65, tier S (host Python, no hardware).
#
# Proves the flash-layout gate inside Conductor.firmware_report() / update_all():
# a module whose bootloader layout differs from the image's layout is flagged
# with a plain reason and NEVER written to; an unknown layout or an index with no
# layout fails closed; an exact match still flashes.
#
# Run from the repo root:   python -I tools/test_layout_gate.py
# (the hardware modules are stubbed - busio/board are not needed)

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))
for name in ("busio", "board", "microcontroller", "digitalio", "supervisor"):
    sys.modules.setdefault(name, types.ModuleType(name))

import noknok   # noqa: E402


class FakeModule:
    """Just enough of a module for firmware_report()."""
    def __init__(self, uid, address, fw, bootloader=None, known=True):
        self._uid_hex = uid
        self.address = address
        self.firmware_version = fw
        self.protocol_version = 1
        self.kind = "buzzer"
        if known:                       # bootloader answer remembered already
            self.bootloader = bootloader


class Rig(noknok.Conductor):
    """A Conductor with no bus: modules injected, bootloader reads and flash
    calls recorded instead of touching hardware."""
    def __init__(self, bl_by_uid):
        self.buzzer, self.knob, self.ledbutton, self.display = [], [], [], []
        self.leds, self.leds16 = [], []
        self._registry = {}
        self.bl_by_uid = bl_by_uid      # what a real read would return per UID
        self.reads, self.flashed = [], []

    def add(self, m):
        self.buzzer.append(m)
        self._registry[m._uid_hex] = m

    def bootloader_version(self, entry):          # replaces the bus round trip
        self.reads.append(entry["uid"])
        v = self.bl_by_uid[entry["uid"]]
        self._registry[entry["uid"]].bootloader = v
        return v

    def update_module(self, entry, image, progress=None):
        self.flashed.append(entry["uid"])
        return True

    def enumerate_all(self, *a, **k):
        return 0


L1 = (1, 1, 0, 0, 0)        # stage-1 1.0.x: no layout byte -> layout_of() == None
L1B = (1, 1, 1, 0, 1)       # stage-1 reporting layout 1
L2 = (1, 1, 2, 0, 2)        # stage-1 1.2.0, layout 2
LEGACY = None               # monolithic bootloader, no 0xB1

IMG2 = {"buzzer": {"version": "3.5.0", "url": "x", "layout": 2}}
IMG2_NOLAYOUT = {"buzzer": {"version": "3.5.0", "url": "x"}}

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-62s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails += 1


def one(bl, manifest=IMG2, known=True, fw="3.3.1", **kw):
    c = Rig({"aa": bl})
    c.add(FakeModule("aa", 0x10, fw, bl, known=known))
    rep = c.firmware_report(manifest, **kw)
    return c, rep[0]


def run_update(bl, manifest=IMG2, known=True, **kw):
    c = Rig({"aa": bl})
    c.add(FakeModule("aa", 0x10, "3.3.1", bl, known=known))
    res = c.update_all(manifest, lambda e: b"img", logfn=lambda *a: None, **kw)
    return c, res


print("firmware_report()")
c, r = one(L2)
check("layout 2 on layout-2 image: update offered", r["needs_update"] and not r["blocked"])
check("  fields: installed 2 / image 2", (r["layout_installed"], r["layout_image"]) == (2, 2))

c, r = one(L1B)
check("layout 1 vs layout-2 image: blocked, plain reason",
      r["blocked"] and not r["needs_update"] and "layout 1" in r["blocked"] and "SWD" in r["blocked"], r["blocked"] or "")
check("  reason text starts with BLOCKED", r["reason"].startswith("BLOCKED"))

c, r = one(LEGACY)
check("legacy bootloader: blocked, says SWD", bool(r["blocked"]) and "SWD" in r["blocked"], r["blocked"] or "")

c, r = one(L2, fw="3.5.0")
check("up to date module is not touched or flagged", not r["needs_update"] and not r["blocked"])

c, r = one(L1B, known=False)
check("unknown layout, default report: no read, no block, 'None'",
      r["layout_installed"] is None and not r["blocked"] and not c.reads)

c, r = one(L1B, known=False, read_layout=True)
check("read_layout=True reads once and then flags the mismatch",
      c.reads == ["aa"] and r["layout_installed"] == 1 and bool(r["blocked"]))

c, r = one(L1, known=False, read_layout=True, strict_layout=True)
check("strict: stage-1 without a layout byte is blocked", bool(r["blocked"]), r["blocked"] or "")

c, r = one(L2, IMG2_NOLAYOUT, strict_layout=True)
check("strict: index with no layout is blocked", bool(r["blocked"]), r["blocked"] or "")

c, r = one(L2, IMG2_NOLAYOUT)
check("non-strict: index with no layout and known good module is not blocked", not r["blocked"])

print("update_all()")
c, res = run_update(L2)
check("exact match flashes", c.flashed == ["aa"] and res and res[0]["updated"])

c, res = run_update(L1B)
check("layout mismatch: NOTHING written", c.flashed == [])
check("  ...and reported with the reason", len(res) == 1 and not res[0]["updated"] and "layout 1" in res[0]["error"])

c, res = run_update(LEGACY)
check("legacy bootloader: nothing written", c.flashed == [])

c, res = run_update(L2, IMG2_NOLAYOUT)
check("manifest without layout: fails closed, nothing written", c.flashed == [] and bool(res and res[0]["error"]))

c, res = run_update(L1B, known=False)
check("unknown layout: read first, mismatch -> nothing written", c.reads == ["aa"] and c.flashed == [])

c, res = run_update(L2, known=False)
check("unknown layout: read first, match -> flashed", c.reads == ["aa"] and c.flashed == ["aa"])

c, res = run_update(L1B, check_layout=False)
check("check_layout=False (bench-only) bypasses the gate", c.flashed == ["aa"])

# Two modules of one type: only the bad one is refused (per module, not per type).
c = Rig({"aa": L2, "bb": L1B})
c.add(FakeModule("aa", 0x10, "3.3.1", L2))
c.add(FakeModule("bb", 0x11, "3.3.1", L1B))
res = c.update_all(IMG2, lambda e: b"img", logfn=lambda *a: None)
check("per module: good one flashed, mismatching one refused",
      c.flashed == ["aa"] and {x["uid"]: x["updated"] for x in res} == {"aa": True, "bb": False})

c = Rig({"aa": L2, "bb": L1B})
c.add(FakeModule("aa", 0x10, "3.3.1", L2))
c.add(FakeModule("bb", 0x11, "3.3.1", L1B))
res = c.update_all(IMG2, lambda e: b"img", logfn=lambda *a: None, exclude_uids={"bb"})
check("exclude_uids (product path) still works, no duplicate result", c.flashed == ["aa"] and len(res) == 1)

print("\n%s" % ("ALL PASS" if not fails else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
