# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_usb_stage1.py - DEV-298, tier S (host Python, no hardware).
#
# The brain installs a newer CH32V203 stage-1 bootloader (USB bootloader 2.0.0,
# flash layout 4) in its daily update pass. A USB module answers 0xB1 only in
# its bootloader and the Pico host only sees it there after a brain hard reset
# (DEV-45), so everything runs as "bootloader visits" around hard resets:
#   day pass  _usb_stage1_plan() records a visit + sends 0xB0, hard reset
#   boot 2    usb_fw_pass(): 0xB1 read; "read" visit -> BOOT; stage-1 visit ->
#             ERASE/WRITE/VERIFY_STAGE1/BOOT (stage-0 installs it), hard reset
#   boot 3    module back in its bootloader without an app: version checked,
#             app pushed from the cache, hard reset
#   boot 4    app back, verified, record cleared
#
# Part 1 drives the REAL noknok_usb.UsbModuleFlasher against a fake CDC device
# (wire bytes, 0xB1 with/without/late reply, errors 4/8).
# Part 2 drives the REAL Conductor + the REAL noknok_main plan/cache code
# against a simulated V203 module (app/bootloader, stage-0 install, power cuts).
#
# Run from the repo root:   python -I tools/test_usb_stage1.py

import json
import os
import sys
import types
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SW = os.path.join(HERE, "..", "software")
sys.path.insert(0, SW)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-70s %s %s" % (name, "PASS" if cond else "FAIL", "" if cond else extra))
    if not cond:
        fails += 1


def stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class _Any:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Any()
    def __getattr__(self, n): return _Any()


# =============================================================================
# Part 1 - noknok_usb.UsbModuleFlasher on the wire
# =============================================================================
class BlDev:
    """A noknok USB bootloader (PID 4E42) at the CDC level."""

    def __init__(self, serial, version=(1, 2, 0, 0, 4), empty_first=0, verify_err=0):
        self.idVendor, self.idProduct = 0x1209, 0x4E42
        self.serial_number = serial
        self.version = version          # None = bootloader 1.x (ignores 0xB1)
        self.empty_first = empty_first  # first N 0xB1 go unanswered
        self.verify_err = verify_err    # error code VERIFY_STAGE1 answers with
        self.writes, self.q = [], b""

    def set_configuration(self):
        pass

    def write(self, ep, data, timeout=None):
        assert ep == 0x02
        d = bytes(data)
        self.writes.append(d)
        c = d[0]
        if c == 0xB1:
            if self.version is None:
                return
            if self.empty_first:
                self.empty_first -= 1
                return
            self.q += bytes(self.version)
        elif c in (0x01, 0x02):
            self.q += bytes((0, 0))
        elif c == 0x04:
            self.q += bytes((2, 0))             # app VERIFY ok -> READY
        elif c == 0x06:
            self.q += bytes((3, self.verify_err)) if self.verify_err else bytes((2, 0))

    def read(self, ep, buf, timeout=None):
        assert ep == 0x83
        if not self.q:
            raise OSError("timeout")
        n = min(len(buf), len(self.q))
        buf[:n] = self.q[:n]
        self.q = self.q[n:]
        return n


BUS = []
usb = types.ModuleType("usb")
usb_core = types.ModuleType("usb.core")
usb_core.find = lambda find_all=False, **k: list(BUS)
usb.core = usb_core
stub("usb_host", Port=lambda dp, dm: object())
sys.modules.update({"usb": usb, "usb.core": usb_core})
board = stub("board")
for n in range(29):
    setattr(board, "GP%d" % n, "GP%d" % n)

import noknok_usb as nu  # noqa: E402
nu.time.sleep = lambda s: None
nu.DEFAULT_DP, nu.DEFAULT_DM = "GP0", "GP1"

print("Part 1: UsbModuleFlasher (wire)")
S = "cdabc2f333bcc25b39e339e3"
f = nu.UsbModuleFlasher()

d = BlDev(S)
BUS[:] = [d]
check("get_version -> (1,2,0,0,4), one 0xB1", f.get_version(S) == (1, 2, 0, 0, 4)
      and d.writes == [b"\xB1"])
d = BlDev(S, empty_first=1)
BUS[:] = [d]
check("first 0xB1 unanswered -> retried, still read", f.get_version(S) == (1, 2, 0, 0, 4)
      and d.writes.count(b"\xB1") == 2)
d = BlDev(S, version=None)
BUS[:] = [d]
check("bootloader 1.x (no answer) -> None after 3 tries", f.get_version(S) is None
      and d.writes.count(b"\xB1") == 3)
BUS[:] = []
try:
    f.get_version(S)
    raised = False
except nu.UsbFlashError:
    raised = True
check("no bootloader on the bus -> UsbFlashError (not 'is 1.x')", raised)

img = bytes(range(256)) * 21 + bytes(196)          # 5572 B like the test 2.0.1
crc = zlib.crc32(img) & 0xFFFFFFFF
d = BlDev(S)
BUS[:] = [d]
f.flash_stage1(img, serial=S)
w = d.writes
check("flash_stage1: ERASE first", w[0] == b"\x01")
chunks = w[1:-2]
check("  ...WRITE x175 (32-byte chunks, 0x02 n data)", len(chunks) == 175
      and all(c[0] == 0x02 and c[1] == len(c) - 2 for c in chunks)
      and b"".join(c[2:] for c in chunks) == img)
check("  ...0x06 VERIFY_STAGE1 + crc32 LE (not 0x04)", w[-2] == b"\x06" + crc.to_bytes(4, "little"))
check("  ...then BOOT 0x05", w[-1] == b"\x05")
for code, word in ((8, "wrong image"), (4, "too big")):
    d = BlDev(S, verify_err=code)
    BUS[:] = [d]
    try:
        f.flash_stage1(img, serial=S)
        msg = ""
    except nu.UsbFlashError as e:
        msg = str(e)
    check("VERIFY_STAGE1 error %d -> raises '%s', no BOOT" % (code, word),
          word in msg and d.writes[-1][0] == 0x06, msg)
d = BlDev(S)
d.q = bytes((1, 2, 0, 0, 4))                        # a late 0xB1 answer still queued
BUS[:] = [d]
f.flash(b"\xAA" * 64, serial=S, confirm_app=False)
check("app flash drains a late 0xB1 reply before ERASE (status not misread)",
      d.writes[0] == b"\x01" and d.writes[-2][0] == 0x04)
d = BlDev(S)
BUS[:] = [d]
f.boot_only(S)
check("boot_only -> just BOOT", d.writes == [b"\x05"])

# =============================================================================
# Part 2 - Conductor + noknok_main with a simulated V203 module
# =============================================================================
class HardReset(Exception):
    pass


def _hard_reset():
    raise HardReset()


nvm = bytearray(4096)
for name in ("busio", "digitalio", "socketpool", "ssl"):
    stub(name)
stub("microcontroller", nvm=nvm, reset=_hard_reset,
     cpu=types.SimpleNamespace(uid=bytes(range(8))))
stub("supervisor", reload=lambda: None, runtime=_Any())
stub("wifi", radio=_Any())
stub("adafruit_requests", Session=_Any)
stub("adafruit_connection_manager", get_radio_socketpool=lambda r: _Any(),
     get_radio_ssl_context=lambda r: _Any())
stub("adafruit_httpserver", Server=_Any, Request=_Any, Response=_Any, POST="POST")


def s1_image(ver, chip=b"V203"):
    return b"NKS1" + chip + bytes(ver) + bytes(100)


class Module:
    """A V203 LEDs module: app or bootloader, frozen stage-0, flash state."""

    def __init__(self, serial, bl=(1, 2, 0, 0, 4), app="2.2.2", mtype=0x06):
        self.serial, self.bl, self.app, self.mtype = serial, bl, app, mtype
        self.mode = "app"
        self.armed = None               # staged stage-1 version (VERIFY_STAGE1 ok)


MODS = {}


class SimFlasher:
    """Stands in for noknok_usb.UsbModuleFlasher; mutates the Module objects
    the way the real bootloader does (DEV-237 spec)."""
    log = []
    cut = None                          # "write" | "after_verify": power lost there

    def __init__(self, *a, **k):
        pass

    def request_bootloader(self, serial):
        m = MODS.get(serial)
        SimFlasher.log.append(("B0", serial))
        if m is None or m.mode != "app":
            return False
        m.mode = "bl"
        return True

    def _bl(self, serial):
        m = MODS.get(serial)
        if m is None or m.mode != "bl":
            raise nu.UsbFlashError("bootloader %s not on the bus" % serial)
        return m

    def get_version(self, serial=None, tries=3):
        m = self._bl(serial)
        SimFlasher.log.append(("B1", serial))
        return m.bl                      # None for 1.x

    def flash_stage1(self, data, serial=None, progress=None):
        m = self._bl(serial)
        SimFlasher.log.append(("S1", serial))
        if m.bl is None:
            raise nu.UsbFlashError("bootloader 1.x has no VERIFY_STAGE1")
        m.app = None                     # ERASE: the app region is the staging area
        if SimFlasher.cut == "write":
            SimFlasher.cut = None
            raise PowerCut()
        if data[4:8] != b"V203":
            raise nu.UsbFlashError("VERIFY_STAGE1: bootloader error wrong image (code 8)")
        m.armed = tuple(data[8:13])
        if SimFlasher.cut == "after_verify":
            SimFlasher.cut = None
            raise PowerCut()
        self._stage0(m)                  # BOOT: reset, stage-0 copies, stays in BL
        return True

    @staticmethod
    def _stage0(m):
        if m.armed:
            m.bl, m.armed = m.armed, None
        m.mode = "app" if m.app else "bl"

    def flash(self, data, serial=None, progress=None, confirm_app=True):
        m = self._bl(serial)
        SimFlasher.log.append(("APP", serial))
        m.app = data.decode()
        m.mode = "app"
        return True

    def boot_only(self, serial=None):
        m = self._bl(serial)
        SimFlasher.log.append(("BOOT", serial))
        if m.app:
            m.mode = "app"
        return True


class PowerCut(BaseException):        # the brain dies too: nothing catches it
    pass


def power_cycle():
    for m in MODS.values():
        SimFlasher._stage0(m)            # stage-0 runs at every power-on


stub("noknok_usb", UsbModuleFlasher=SimFlasher, last_bootloaders=[])
import noknok  # noqa: E402

src = open(os.path.join(SW, "noknok_main.py"), encoding="utf-8").read()
ns = {"__name__": "code_under_test"}
exec(compile(src, "noknok_main.py", "exec"), ns)
LOG = []
EVENTS = []
ns["log"] = lambda msg: LOG.append(msg)
ns["event"] = lambda msg: EVENTS.append(msg)
nk = ns["nk"]
nk.pins = lambda g: ("GP0", "GP1") if g == "usb" else None

# The on-device cache, as the online pass leaves it.
CACHE = {}


def cache_put(slot, data, **meta):
    CACHE[slot] = (data, dict(meta))


def _cache_meta(slot):
    return dict(CACHE[slot][1]) if slot in CACHE else None


def _cache_read(slot):
    if slot not in CACHE:
        raise ValueError("no cached image for %r" % slot)
    data, meta = CACHE[slot]
    return data, dict(meta)


ns["_cache_meta"], ns["_cache_read"] = _cache_meta, _cache_read


class Leds16:
    MODULE_TYPE = 0x06

    def __init__(self, serial, version):
        self._uid_hex, self.firmware_version = serial, version


class Leds8(Leds16):
    MODULE_TYPE = 0x04


def conductor():
    c = noknok.Conductor.__new__(noknok.Conductor)
    c.leds = [Leds8(s, m.app) for s, m in MODS.items() if m.mode == "app" and m.mtype == 4]
    c.leds16 = [Leds16(s, m.app) for s, m in MODS.items() if m.mode == "app" and m.mtype == 6]
    c.usb_parked = sorted(s for s, m in MODS.items() if m.mode == "bl")
    c._usb_fl = None
    return c


def reset_brain():
    ns["_hard_reset_why"] = None
    ns["_ota_went_online"] = False
    ns["_field_alerts"][:] = []


def boot():
    """One brain boot: get_conductor()'s USB pass (cache as image source).
    Returns (actions, hard_reset_requested)."""
    reset_brain()
    c = conductor()
    out = []
    orig = ns["event"]
    ns["event"] = lambda msg: (EVENTS.append(msg), out.append(msg))
    try:
        ns["_usb_fw"](c)
    finally:
        ns["event"] = orig
    acts = [m.split("action=")[1].split()[0] for m in out if "action=" in m]
    return acts, ns["_hard_reset_why"] is not None


def day_pass(published="2.0.1", report=None, layout=4, chip="CH32V203"):
    """The online pass's USB stage-1 step: plan visits for the published stage-1."""
    reset_brain()
    c = conductor()
    s1 = {layout: {"version": published, "url": "u", "layout": layout, "size": 1,
                   "crc32": "0", "chip": chip}} if published else {}
    ns["_usb_stage1_plan"](c, s1, report or [])
    return ns["_hard_reset_why"] is not None


def rec(serial=S):
    return (noknok.store().get("usb") or {}).get(serial) or {}


def fresh(*mods):
    MODS.clear()
    for m in mods:
        MODS[m.serial] = m
    noknok.store().delete("usb")
    SimFlasher.log, SimFlasher.cut = [], None
    c = conductor()
    c._remember_usb_types([(m.serial, "x", (Leds16 if m.mtype == 6 else Leds8)(m.serial, m.app))
                           for m in mods])
    CACHE.clear()
    cache_put("usb_leds_16x", b"2.2.2", version="2.2.2", size=5, crc32="0")
    cache_put("usb_leds", b"1.8.2", version="1.8.2", size=5, crc32="0")
    cache_put("stage1_4", s1_image((1, 2, 0, 1, 4)), version="2.0.1", layout=4,
              size=113, crc32="0")


print("\nPart 2: the happy path, 2.0.0 -> 2.0.1 on a LEDs 16x")
fresh(Module(S))
check("day 1: bootloader never read -> a 'read' visit (0xB0), hard reset",
      day_pass() and SimFlasher.log == [("B0", S)] and rec().get("s1") == "read")
acts, rst = boot()
check("boot 2: in its bootloader -> 0xB1 read, BOOT back, hard reset",
      acts == ["read"] and rst and rec().get("bl") == [1, 2, 0, 0, 4], (acts, rec()))
check("  ...visit cleared, 1 read visit counted, nothing written",
      "s1" not in rec() and "pending" not in rec() and rec().get("bl_visits") == 1
      and ("S1", S) not in SimFlasher.log and MODS[S].app == "2.2.2", rec())
acts, rst = boot()
check("boot 3: app runs, nothing pending -> quiet, no reset", acts == [] and not rst)
SimFlasher.log = []
check("day 1 (same pass result): 2.0.0 < 2.0.1 -> stage-1 visit scheduled",
      day_pass() and rec().get("s1") == "2.0.1" and SimFlasher.log == [("B0", S)], rec())
acts, rst = boot()
check("boot 2: stage-1 installed -> action stage1, hard reset",
      acts == ["stage1"] and rst and MODS[S].bl == (1, 2, 0, 1, 4) and MODS[S].mode == "bl",
      (acts, vars(MODS[S])))
check("  ...record: s1_done 2.0.1, 1 attempt", rec().get("s1_done") == "2.0.1"
      and rec().get("s1_n") == 1, rec())
acts, rst = boot()
check("boot 3: back in its bootloader -> version OK, app pushed, hard reset",
      acts == ["stage1_ok", "flashed"] and rst and MODS[S].app == "2.2.2", (acts, rec()))
check("  ...bl in the record is now 2.0.1", rec().get("bl") == [1, 2, 0, 1, 4], rec())
acts, rst = boot()
check("boot 4: app back -> verified, no reset", acts == ["verified"] and not rst, acts)
check("  ...cycle keys cleared; bl / s1_ver / s1_n / bl_visits kept",
      set(rec()) == {"type", "bl", "bl_app", "bl_visits", "s1_ver", "s1_n"}, rec())
resets = 0
SimFlasher.log = []
check("next day: current -> no visit, no 0xB0, no reset",
      not day_pass() and SimFlasher.log == [])
acts, rst = boot()
check("  ...and the boot is quiet", acts == [] and not rst)

print("\nPart 2: already current at the first read")
fresh(Module(S, bl=(1, 2, 0, 1, 4)))
day_pass()
boot()
SimFlasher.log = []
check("2.0.1 read once -> later days: no visit at all",
      not day_pass() and not day_pass() and SimFlasher.log == [] and rec().get("bl_visits") == 1)

print("\nPart 2: bootloader 1.x (LEDs 8x / 16x not yet on layout 4)")
fresh(Module(S, bl=None))
day_pass()
acts, rst = boot()
check("read visit: no 0xB1 answer -> bl None, BOOT back", acts == ["read"] and rec().get("bl") is None
      and MODS[S].mode == "app" and MODS[S].app == "2.2.2", (acts, rec()))
SimFlasher.log = []
check("  ...never offered a stage-1 again (no visit, no 0xB0)",
      not day_pass() and not day_pass() and SimFlasher.log == [])
S8 = "aaaa000000000000000000a8"
fresh(Module(S8, bl=None, app="1.8.2", mtype=4))
day_pass()
boot()
check("LEDs 8x on 1.x: same - read once, then left alone",
      rec(S8).get("bl") is None and not day_pass() and MODS[S8].app == "1.8.2")

print("\nPart 2: re-flashed elsewhere (stale bl, e.g. the one-time layout-4 full image)")
fresh(Module(S, bl=(1, 2, 0, 0, 4), app="2.2.2"))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl": None, "bl_app": "2.2.1",
                               "bl_visits": 2}})
SimFlasher.log = []
check("bl null read under app 2.2.1, module now runs 2.2.2 -> fresh read visit (cap reset)",
      day_pass() and rec().get("s1") == "read" and rec().get("bl_visits") == 1
      and rec().get("bl_app") == "2.2.2", rec())
boot()
check("  ...re-read: layout 4, 2.0.0", rec().get("bl") == [1, 2, 0, 0, 4], rec())
check("  ...and the stage-1 visit follows", day_pass() and rec().get("s1") == "2.0.1", rec())
fresh(Module(S, bl=(1, 2, 0, 0, 4)))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl": None}})
check("record from before bl_app existed -> treated as stale, read again",
      day_pass() and rec().get("s1") == "read", rec())
fresh(Module(S, bl=None))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl": None, "bl_app": "2.2.2"}})
SimFlasher.log = []
check("bl null under the SAME app version -> left alone", not day_pass() and SimFlasher.log == [])
fresh(Module(S, bl=None, app="2.2.1"))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl": None, "bl_app": "2.2.1"}})
conductor().update_module({"type": "usb_leds_16x", "bus": "usb", "uid": S}, b"x")
boot()
boot()
SimFlasher.log = []
check("app updated BY THIS BRAIN 2.2.1 -> 2.2.2: bl_app follows, no extra read visit",
      rec().get("bl_app") == "2.2.2" and not day_pass() and SimFlasher.log == [], rec())

print("\nPart 2: piggyback on an app update")
fresh(Module(S))
rep = [{"uid": S, "bus": "usb", "needs_update": True, "type": "usb_leds_16x"}]
SimFlasher.log = []
check("bl unknown but an app update is due -> no extra 'read' visit",
      not day_pass(report=rep) and SimFlasher.log == [] and "s1" not in rec())
c = conductor()
c.update_module({"type": "usb_leds_16x", "bus": "usb", "uid": S}, b"x")
acts, rst = boot()
check("the app-update visit reads 0xB1 for free, then flashes the app",
      acts == ["flashed"] and rec().get("bl") == [1, 2, 0, 0, 4] and ("B1", S) in SimFlasher.log,
      (acts, rec()))
boot()
check("  ...next day the stage-1 visit is planned directly",
      day_pass() and rec().get("s1") == "2.0.1", rec())

print("\nPart 2: stage-1 and app update due the same day")
fresh(Module(S, app="2.2.1"))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl": [1, 2, 0, 0, 4], "bl_app": "2.2.1"}})
day_pass(report=[{"uid": S, "bus": "usb", "needs_update": True}])
c = conductor()
SimFlasher.log = []
r = c.update_module({"type": "usb_leds_16x", "bus": "usb", "uid": S}, b"x")
check("update_module() while a stage-1 visit is pending -> 'pending', no 2nd 0xB0",
      r == "pending" and SimFlasher.log == [] and rec().get("app") is True, rec())
a1, _ = boot()
a2, _ = boot()
a3, _ = boot()
check("visit: stage1 -> stage1_ok + app (current 2.2.2) -> verified",
      a1 == ["stage1"] and a2 == ["stage1_ok", "flashed"] and a3 == ["verified"]
      and MODS[S].app == "2.2.2" and MODS[S].bl == (1, 2, 0, 1, 4), (a1, a2, a3))

print("\nPart 2: power cut during the stage-1 WRITE stream")
fresh(Module(S, bl=(1, 2, 0, 0, 4)))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl": [1, 2, 0, 0, 4], "bl_app": "2.2.2"}})
day_pass()
SimFlasher.cut = "write"
try:
    boot()
    cut = False
except PowerCut:
    cut = True
power_cycle()
check("cut mid-WRITE: old stage-1 kept, app gone, module in its bootloader",
      cut and MODS[S].bl == (1, 2, 0, 0, 4) and MODS[S].app is None and MODS[S].mode == "bl")
check("  ...the attempt was counted before the ERASE (Store survives the cut)",
      rec().get("s1_n") == 1 and rec().get("s1") == "2.0.1", rec())
a1, _ = boot()
a2, _ = boot()
a3, _ = boot()
check("next boots: retried (attempt 2) -> stage1_ok + app -> verified",
      a1 == ["stage1"] and a2 == ["stage1_ok", "flashed"] and a3 == ["verified"]
      and rec().get("s1_n") == 2 and MODS[S].bl == (1, 2, 0, 1, 4), (a1, a2, a3, rec()))

print("\nPart 2: power cut after VERIFY_STAGE1, before BOOT")
fresh(Module(S))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl": [1, 2, 0, 0, 4], "bl_app": "2.2.2"}})
day_pass()
SimFlasher.cut = "after_verify"
try:
    boot()
except PowerCut:
    pass
power_cycle()
check("stage-0 installed the armed stage-1 at power-on", MODS[S].bl == (1, 2, 0, 1, 4)
      and MODS[S].mode == "bl")
a1, rst = boot()
a2, _ = boot()
check("next boot: already 2.0.1 -> stage1_ok, app pushed (no 2nd install) -> verified",
      a1 == ["stage1_ok", "flashed"] and rst and a2 == ["verified"]
      and rec().get("s1_n") == 1, (a1, a2, rec()))

print("\nPart 2: wrong image (not a V203 stage-1) -> error 8")
fresh(Module(S))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl": [1, 2, 0, 0, 4], "bl_app": "2.2.2"}})
cache_put("stage1_4", s1_image((1, 2, 0, 1, 4), chip=b"V003"), version="2.0.1", layout=4,
          size=113, crc32="0")
day_pass()
a1, rst = boot()
check("refused -> stage1_failed + app pushed in the SAME boot (ERASE cleared it)",
      a1 == ["stage1_failed", "flashed"] and rst and MODS[S].app == "2.2.2"
      and MODS[S].bl == (1, 2, 0, 0, 4), (a1, rec()))
check("  ...customer alert raised", any("stage1_failed" in a for a in ns["_field_alerts"])
      or ns["_field_alerts"], ns["_field_alerts"])
a2, _ = boot()
check("  ...verified, module runs its app", a2 == ["verified"] and MODS[S].mode == "app")
day_pass()
boot()
boot()
boot()
SimFlasher.log = []
check("second failure on the next day -> 2 attempts for 2.0.1: no more visits (no loop)",
      rec().get("s1_n") == 2 and not day_pass() and SimFlasher.log == [], rec())
check("  ...app still running", MODS[S].mode == "app" and MODS[S].app == "2.2.2")
SimFlasher.log = []
cache_put("stage1_4", s1_image((1, 2, 0, 2, 4)), version="2.0.2", layout=4, size=113, crc32="0")
check("a NEWER published stage-1 (2.0.2) gets fresh attempts",
      day_pass(published="2.0.2") and rec().get("s1") == "2.0.2", rec())

print("\nPart 2: cache does not hold the published stage-1 / app")
fresh(Module(S))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl": [1, 2, 0, 0, 4], "bl_app": "2.2.2"}})
CACHE.pop("stage1_4")
SimFlasher.log = []
check("stage-1 not cached (fetch failed) -> no visit", not day_pass() and SimFlasher.log == [])
fresh(Module(S))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl": [1, 2, 0, 0, 4], "bl_app": "2.2.2"}})
CACHE.pop("usb_leds_16x")
check("app not cached -> no visit (it could not be restored), alert",
      not day_pass() and SimFlasher.log == [] and ns["_field_alerts"])
fresh(Module(S))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl": [1, 2, 0, 0, 4], "bl_app": "2.2.2"}})
day_pass()
cache_put("stage1_4", s1_image((1, 2, 0, 2, 4)), version="2.0.2", layout=4, size=113, crc32="0")
a1, _ = boot()
check("cache moved on between plan and visit -> stage1_failed, app pushed, not guessed",
      a1 == ["stage1_failed", "flashed"] and MODS[S].bl == (1, 2, 0, 0, 4), a1)

print("\nPart 2: what is never touched")
fresh(Module(S))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl": [1, 2, 0, 0, 4], "bl_app": "2.2.2"}})
SimFlasher.log = []
check("registry without a USB stage-1 (old registry / only I2C chips) -> nothing",
      not day_pass(published=None) and SimFlasher.log == [])
check("published stage-1 for layout 2 (CH32V003) -> USB module not visited",
      not day_pass(layout=2, chip="CH32V003") and SimFlasher.log == [])
check("a layout-4 stage-1 for a chip not in USB_STAGE1_CHIPS -> ignored",
      not day_pass(chip="CH32X035") and SimFlasher.log == [])
fresh(Module(S, bl=(1, 2, 0, 0, 3)))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl": [1, 2, 0, 0, 3], "bl_app": "2.2.2"}})
check("module reporting another layout -> not offered the layout-4 image",
      not day_pass() and SimFlasher.log == [])

print("\nPart 2: read-visit caps")
fresh(Module(S))
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "bl_visits": 2, "bl_app": "2.2.2"}})
SimFlasher.log = []
check("2 read visits without an answer already -> no 3rd", not day_pass() and SimFlasher.log == [])
fresh(Module(S))
day_pass()
MODS[S].mode = "app"                 # lost power before the visit: back as the app
a1, rst = boot()
check("asked, but came back as the app -> asked again (DEV-133 cap applies)",
      a1 == ["asked"] and rst and rec().get("asks") == 2, (a1, rec()))
MODS[S].mode = "app"
a2, rst = boot()
check("  ...third time -> gave_up, visit cleared, no reset loop",
      a2 == ["gave_up"] and not rst and "s1" not in rec() and "pending" not in rec(), (a2, rec()))

print("\nnoknok_main helpers")
CACHE.clear()
cache_put("stage1_4", b"abc", version="2.0.1", layout=4, size=3, crc32="0")
try:
    ns["_cached_usb_stage1"](4, "2.0.1")
    ok1 = True
except ValueError:
    ok1 = False
try:
    ns["_cached_usb_stage1"](4, "2.0.0")
    ok2 = False
except ValueError:
    ok2 = True
check("_cached_usb_stage1: exact version + layout only", ok1 and ok2)


class FakeSession:
    def __init__(self, docs):
        self.docs = docs


DOCS = {
    "i003": {"format": 1, "version": "1.3.0", "url": "u3", "layout": 2, "size": 3, "crc32": "a"},
    "i203": {"format": 1, "version": "2.0.1", "url": "u4", "layout": 4, "size": 5572,
             "crc32": "dc791dc1", "test_only": True},
}
ns["_download_json"] = lambda session, url, timeout=None: DOCS[url]
reg = {"bootloader": {"stage1": {"CH32V003": {"index": "i003"},
                                 "CH32V203": {"index": "i203"}}}}
out = ns["_resolve_stage1"](None, reg)
check("_resolve_stage1 keeps the chip per layout (registry 2.1.0 shape)",
      out[2]["chip"] == "CH32V003" and out[4]["chip"] == "CH32V203"
      and out[4]["version"] == "2.0.1", out)

print("\nLEDs app index without a `layout` field")
c = noknok.Conductor.__new__(noknok.Conductor)
r = {"type": "usb_leds_16x", "bus": "usb", "uid": S, "address": None,
     "needs_update": True, "layout_image": None, "layout_installed": None, "blocked": None}
c._check_layout(r, True, True)
check("USB entry with no layout is not blocked by the layout gate", r["needs_update"] is True
      and not r.get("blocked"), r)
gate = ns["_bootloader_gate"](c, [r], None)
check("_bootloader_gate refuses nothing for USB", list(gate) == [], gate)

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
