# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_stage1_bl.py - DEV-106 + DEV-147, tier S (host Python, no hardware).
#
# DEV-106: after Conductor.stage1_update() the remembered bootloader version
#   ("bl": module.bootloader, _bl_seen, the saved state) is the version read back
#   after the install, not the old one - so the layout gate judges the new stage-1.
# DEV-147: bootloader_version() asks a second time (after ~200 ms) when the module
#   did not appear at 0x7E after the first 0xB0; a second miss still raises, and
#   the module is always sent back to its app (BOOT).
#
# Run from the repo root:   python -I tools/test_stage1_bl.py

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))
for name in ("busio", "board", "microcontroller", "digitalio", "supervisor"):
    sys.modules.setdefault(name, types.ModuleType(name))


class FlashError(Exception):
    pass


class FakeFlasher:
    """Stands in for module_flasher.ModuleFlasher. `misses` = how many
    wait_for_bootloader() calls fail before the module shows up."""
    misses = 0
    log = []
    version = (1, 1, 2, 0, 2)

    def __init__(self, bus):
        pass
    def enter_bootloader(self, addr, settle=0.2):
        FakeFlasher.log.append("enter")
    def wait_for_bootloader(self, timeout=2.0):
        FakeFlasher.log.append("wait")
        if FakeFlasher.misses > 0:
            FakeFlasher.misses -= 1
            raise FlashError("bootloader did not appear at 0x7E")
        return True
    def get_version(self):
        return FakeFlasher.version
    def boot(self):
        FakeFlasher.log.append("boot")
    def flash_stage1(self, data, runtime_addr=None, progress=None):
        return (1, 1, 1, 0, 0), FakeFlasher.version
    def flash(self, image, runtime_addr=None, progress=None):
        FakeFlasher.log.append("app")
        return True


fake_mf = types.ModuleType("module_flasher")
fake_mf.ModuleFlasher = FakeFlasher
fake_mf.FlashError = FlashError
sys.modules["module_flasher"] = fake_mf

import noknok   # noqa: E402
noknok.time.sleep = lambda s: None          # no real waits in the retry


class Mod:
    def __init__(self, uid):
        self._uid_hex = uid
        self.address = 0x09


class Rig(noknok.Conductor):
    """A Conductor with no bus: one module, enumerate() keeps the SAME object
    (as a re-enumeration with restore does) and _save_state() is counted."""
    def __init__(self, bl=None):
        self._registry = {}
        self.saves = 0
        m = Mod("cdab67a8bcbdf011")
        if bl is not None:
            m.bootloader = bl
        self._registry[m._uid_hex] = m
        self._bl_seen = {m._uid_hex: bl} if bl is not None else {}
    def _entry_bus(self, entry):
        return None
    def enumerate(self, *a, **k):
        return list(self._registry.values())
    def by_uid(self, uid):
        return self._registry.get(uid)
    def _save_state(self, filename=None):
        self.saves += 1


fails = 0
def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (("  - " + detail) if detail and not cond else ""))
    if not cond:
        fails += 1

UID = "cdab67a8bcbdf011"
ENTRY = {"bus": "i2c", "address": 0x09, "uid": UID, "type": "buzzer"}

# ── DEV-106 ─────────────────────────────────────────────────────────────────
c = Rig(bl=(1, 1, 1, 0, 0))
FakeFlasher.log = []
r = c.stage1_update(ENTRY, b"s1", b"app")
m = c.by_uid(UID)
check("stage1_update returns the read-back version", r["after"] == (1, 1, 2, 0, 2), repr(r))
check("module.bootloader is the NEW version", m.bootloader == (1, 1, 2, 0, 2), repr(m.bootloader))
check("_bl_seen is the NEW version", c._bl_seen.get(UID) == (1, 1, 2, 0, 2), repr(c._bl_seen))
check("state saved after the install", c.saves >= 1, "saves=%d" % c.saves)
check("layout gate sees layout 2", c.layout_of(m.bootloader) == 2, repr(c.layout_of(m.bootloader)))
check("app restored", r["app_restored"] and "app" in FakeFlasher.log)

c2 = Rig(bl=(1, 1, 1, 0, 0))
c2.stage1_update(ENTRY, b"s1")              # no app image: still remembered
check("remembered without an app image too", c2.by_uid(UID).bootloader == (1, 1, 2, 0, 2))

# ── DEV-147 ─────────────────────────────────────────────────────────────────
c = Rig()
FakeFlasher.log, FakeFlasher.misses = [], 0
v = c.bootloader_version(ENTRY)
check("healthy module: one 0xB0, read OK", v == (1, 1, 2, 0, 2) and FakeFlasher.log.count("enter") == 1,
      repr(FakeFlasher.log))

c = Rig()
FakeFlasher.log, FakeFlasher.misses = [], 1
v = c.bootloader_version(ENTRY)
check("first wait missed: second 0xB0, read OK", v == (1, 1, 2, 0, 2)
      and FakeFlasher.log.count("enter") == 2, repr(FakeFlasher.log))
check("...and the version is remembered", c.by_uid(UID).bootloader == v and c._bl_seen.get(UID) == v)
check("...and the module is sent back to its app", FakeFlasher.log[-1] == "boot", repr(FakeFlasher.log))

c = Rig()
FakeFlasher.log, FakeFlasher.misses = [], 2
try:
    c.bootloader_version(ENTRY)
    raised = False
except FlashError:
    raised = True
check("two misses: still raises (caller logs it)", raised)
check("...exactly two 0xB0, no third", FakeFlasher.log.count("enter") == 2, repr(FakeFlasher.log))
check("...module sent back to its app anyway", FakeFlasher.log[-1] == "boot", repr(FakeFlasher.log))
check("...nothing remembered", not hasattr(c.by_uid(UID), "bootloader") and UID not in c._bl_seen)

print("\n%s - %d failure(s)" % ("ALL PASS" if not fails else "FAILED", fails))
sys.exit(1 if fails else 0)
