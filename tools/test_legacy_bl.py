# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_legacy_bl.py - DEV-155, tier S (host Python, no hardware).
#
# A module with the LEGACY monolithic bootloader (e.g. a Knob with app 2.1.0,
# linked at 0x1000) does not stay silent on the bootloader's GET_VERSION (0xB1):
# it ACKs every byte and answers any read with its status [state, last_error]
# padded with 0x00, so a fresh one reads (0, 0, 0, 0, 0). The brain used to take
# that for "stage-1 without a layout byte" (layout None). Checked here:
#   - module_flasher.get_version() returns None for that reply (real class, fake bus),
#     and the real stage-1 tuple for a stage-1 reply;
#   - Conductor.layout_of() says 0 (legacy) for it, also for a list read back
#     from the Store, and keeps None for a real pre-1.2.0 stage-1;
#   - bootloader_version() / the remembered "bl" become None for it, even when an
#     old module_flasher still hands back the zero tuple.
#
# Run from the repo root:   python -I tools/test_legacy_bl.py

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))
for name in ("busio", "board", "microcontroller", "digitalio", "supervisor"):
    sys.modules.setdefault(name, types.ModuleType(name))

import module_flasher as real_mf   # noqa: E402  (the real file, before the fake below)

fails = 0
def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (("  - " + detail) if detail and not cond else ""))
    if not cond:
        fails += 1


# ── 1. module_flasher: the reply classifier and get_version() ───────────────
class FakeBus:
    """An I2C bus with one device at 0x7E that answers every read with `reply`
    (padded with zeros, like the CH32V003 bootloaders' TXE handler)."""
    def __init__(self, reply):
        self.reply = bytes(reply)
        self.writes = []
    def try_lock(self):
        return True
    def unlock(self):
        pass
    def writeto(self, addr, data):
        self.writes.append((addr, bytes(data)))
    def readfrom_into(self, addr, buf):
        for i in range(len(buf)):
            buf[i] = self.reply[i] if i < len(self.reply) else 0

S1 = (1, 1, 2, 0, 2)            # stage-1 1.2.0, layout 2 (LED Button / Buzzer / Display on the bench)
check("is_stage1_reply: legacy zero reply is not stage-1", not real_mf.is_stage1_reply((0, 0, 0, 0, 0)))
check("is_stage1_reply: stage-1 1.2.0", real_mf.is_stage1_reply(S1))
check("is_stage1_reply: stage-1 1.0.0 (no layout byte) still stage-1", real_mf.is_stage1_reply((1, 1, 0, 0, 0)))
check("is_stage1_reply: legacy in ERROR state (3, err) is not stage-1", not real_mf.is_stage1_reply((3, 5, 0, 0, 0)))
check("is_stage1_reply: None", not real_mf.is_stage1_reply(None))

legacy = real_mf.ModuleFlasher(FakeBus([0, 0]))          # legacy: status [IDLE, 0] + zero pad
check("get_version on a legacy bootloader -> None", legacy.get_version() is None)
check("...the 0xB1 was still sent", legacy.i2c.writes == [(0x7E, bytes([0xB1]))], repr(legacy.i2c.writes))
stage1 = real_mf.ModuleFlasher(FakeBus(S1))
check("get_version on a stage-1 -> the 5-tuple", stage1.get_version() == S1, repr(stage1.get_version()))


# ── 2. Conductor: layout_of, bootloader_version, remembered "bl" ────────────
class FakeFlasher:
    """An OLD module_flasher that still returns the raw zero tuple - the brain
    must not depend on module_flasher alone."""
    version = (0, 0, 0, 0, 0)
    def __init__(self, bus):
        pass
    def enter_bootloader(self, addr, settle=0.2):
        pass
    def wait_for_bootloader(self, timeout=2.0):
        return True
    def get_version(self):
        return FakeFlasher.version
    def boot(self):
        pass

fake_mf = types.ModuleType("module_flasher")
fake_mf.ModuleFlasher = FakeFlasher
sys.modules["module_flasher"] = fake_mf

import noknok   # noqa: E402
noknok.time.sleep = lambda s: None
C = noknok.Conductor

check("layout_of((0,0,0,0,0)) == 0 (legacy, needs SWD)", C.layout_of((0, 0, 0, 0, 0)) == 0,
      repr(C.layout_of((0, 0, 0, 0, 0))))
check("layout_of([0,0,0,0,0]) from the Store == 0", C.layout_of([0, 0, 0, 0, 0]) == 0)
check("layout_of(None) == 0 (unchanged)", C.layout_of(None) == 0)
check("layout_of(stage-1 1.1.0, no layout byte) is None (unchanged)", C.layout_of((1, 1, 1, 0, 0)) is None)
check("layout_of(stage-1 1.2.0 layout 2) == 2 (unchanged)", C.layout_of(S1) == 2)
check("_bl_norm keeps a real stage-1 as a tuple", C._bl_norm([1, 1, 2, 0, 2]) == S1)
check("_bl_norm drops the zero reply", C._bl_norm([0, 0, 0, 0, 0]) is None)


class Mod:
    def __init__(self, uid):
        self._uid_hex = uid
        self.address = 0x0A

class Rig(noknok.Conductor):
    def __init__(self):
        m = Mod(UID)
        self._registry = {UID: m}
        self._bl_seen = {}
        self.saves = 0
    def _entry_bus(self, entry):
        return None
    def enumerate(self, *a, **k):
        return list(self._registry.values())
    def by_uid(self, uid):
        return self._registry.get(uid)
    def _save_state(self, filename=None):
        self.saves += 1

UID = "cdab9be274bcdc4a"        # the DEV-155 Knob
ENTRY = {"bus": "i2c", "address": 0x0A, "uid": UID, "type": "knob"}

c = Rig()
v = c.bootloader_version(ENTRY)
m = c.by_uid(UID)
check("bootloader_version on the legacy Knob -> None", v is None, repr(v))
check("...module.bootloader remembered as None (legacy)", hasattr(m, "bootloader") and m.bootloader is None,
      repr(getattr(m, "bootloader", "missing")))
check("..._bl_seen remembered as None", UID in c._bl_seen and c._bl_seen[UID] is None, repr(c._bl_seen))
check("...state saved", c.saves >= 1)
check("...layout gate now sees 0 (legacy), not None", c.layout_of(m.bootloader) == 0)

FakeFlasher.version = S1
c = Rig()
v = c.bootloader_version(ENTRY)
check("bootloader_version on a stage-1 module unchanged", v == S1 and c.by_uid(UID).bootloader == S1, repr(v))

print("\n%s - %d failure(s)" % ("ALL PASS" if not fails else "FAILED", fails))
sys.exit(1 if fails else 0)
