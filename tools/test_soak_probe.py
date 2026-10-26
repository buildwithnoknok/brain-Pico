# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_soak_probe.py - DEV-165 / DEV-166, tier S (host Python, no hardware).
#
# Dry run of tools/soak/soak_probe.py against a fake I2C bus with a fake clock:
#   quiet    - every baselined module answers: no capture, no 20 s wait
#   missing  - a module stays silent: the probe captures the bus (raw scan, 0x7F,
#              0x7E, raw bytes at the module's address) and a 20 s later second
#              enumeration that still does not see it
#   late     - a module powers up 12 s late: missing the first time, found by the
#              second enumeration (separates "slow boot" from "stuck")
#   glitch   - a module answers its first GET_VERSION with [1,2,0,0]: flagged as an
#              odd version, raw bytes kept, correct again in the second pass
#   boot     - a module sitting in its bootloader at 0x7E: boot_ver captured
#   hold     - during an idle_hold a module that vanishes is counted, never captured
#   expect   - soak_run.expect_from() turns the baseline into what the probe wants
#   report   - soak_report.py prints the captures
#
# Run from the repo root:   python -I tools/test_soak_probe.py

import io
import json
import os
import sys
import types
import contextlib
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))
sys.path.insert(0, os.path.join(HERE, "soak"))

# ── fake CircuitPython world ─────────────────────────────────────────────────
board = types.ModuleType("board")
for n in range(29):
    setattr(board, "GP%d" % n, "GP%d" % n)
board.LED = "LED"
sys.modules["board"] = board
mc = types.ModuleType("microcontroller")
mc.nvm = bytearray(4096)
mc.nvm[0] = 0xC0


class _Reason:
    def __str__(self):
        return "ResetReason.POWER_ON"


mc.cpu = types.SimpleNamespace(reset_reason=_Reason())
sys.modules["microcontroller"] = mc
sup = types.ModuleType("supervisor")
sup.runtime = types.SimpleNamespace(run_reason="RunReason.STARTUP", safe_mode_reason="SafeModeReason.NONE")
sys.modules["supervisor"] = sup
sys.modules["digitalio"] = types.ModuleType("digitalio")

NOW = [100.0]            # fake clock: sleep() advances it, monotonic() reads it


def fake_sleep(s):
    NOW[0] += s


import time as _time   # noqa: E402
_time.sleep = fake_sleep
_time.monotonic = lambda: NOW[0]


import gc as _gc   # noqa: E402
_gc.mem_free = lambda: 250000          # CircuitPython only


def crc8(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class FakeModule:
    def __init__(self, uid_hex, mtype, fw=(2, 1, 0), up_at=0.0, glitch=0, silent=False, bootloader=False):
        self.uid = bytes.fromhex(uid_hex)
        self.type = mtype
        self.fw = fw
        self.up_at = up_at            # fake-clock time the module powers up
        self.glitch = glitch          # first N GET_VERSION replies are [1, 2, 0, 0]
        self.silent = silent          # never answers
        self.bootloader = bootloader  # answers at 0x7E only
        self.address = None
        self.last_cmd = None
        self.assigned = False


WIRED = []


class FakeBus:
    def __init__(self, scl, sda, frequency=100_000):
        self.modules = WIRED
        self.pending = None

    def try_lock(self):
        return True

    def unlock(self):
        pass

    def deinit(self):
        pass

    def _live(self, m):
        return not m.silent and NOW[0] >= m.up_at

    def readfrom_into(self, addr, buf):
        if addr == 0x7F:
            for m in self.modules:
                if self._live(m) and not m.bootloader and m.address is None:
                    self.pending = m
                    rec = m.uid + bytes([m.type])
                    buf[:10] = rec + bytes([crc8(rec)])
                    return
            raise OSError(19)
        if addr == 0x7E:
            for m in self.modules:
                if self._live(m) and m.bootloader:
                    if m.last_cmd == 0xB1:
                        buf[:5] = bytes([1, 1, 2, 0, 2])[:len(buf)]
                    else:
                        buf[:2] = bytes([0, 0])[:len(buf)]
                    return
            raise OSError(19)
        for m in self.modules:
            if self._live(m) and m.address == addr:
                if m.last_cmd == 0xB1:
                    if m.glitch > 0:
                        m.glitch -= 1
                        buf[:4] = bytes([1, 2, 0, 0])[:len(buf)]
                    else:
                        buf[:4] = bytes([1, *m.fw])[:len(buf)]
                else:
                    for i in range(len(buf)):
                        buf[i] = 0
                return
        raise OSError(19)

    def writeto(self, addr, data):
        data = bytes(data)
        if addr == 0x7F:
            if self.pending is None or data[0] != 0x1D:
                raise OSError(19)
            self.pending.address = data[1]
            self.pending = None
            return
        if addr == 0x7E:
            for m in self.modules:
                if self._live(m) and m.bootloader:
                    m.last_cmd = data[0] if data else None
                    return
            raise OSError(19)
        for m in self.modules:
            if self._live(m) and m.address == addr:
                m.last_cmd = data[0] if data else None
                return
        raise OSError(19)

    def scan(self):
        return [m.address for m in self.modules if m.address and self._live(m)]


busio = types.ModuleType("busio")
busio.I2C = FakeBus
sys.modules["busio"] = busio
mf = types.ModuleType("module_flasher")
sys.modules["module_flasher"] = mf

for k, v in (("NOKNOK_I2C_SDA", "GP10"), ("NOKNOK_I2C_SCL", "GP11")):
    os.environ[k] = v

import noknok  # noqa: E402

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-70s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails += 1


KNOB = "cdab9be274bcdc4a"
LBTN = "cdabc35528beb8bf"
HUB = "cdab6e273abc758f"
BASE = {"A": {KNOB: ["knob", 0x0a, "2.1.0"], LBTN: ["ledbutton", 0x0c, "2.4.1"],
              HUB: ["picohub", 0x08, "0.3.0"]}}
SRC = open(os.path.join(HERE, "soak", "soak_probe.py"), encoding="utf-8").read()


def run_probe(modules, expect, hold=0):
    """Run soak_probe.py exactly as the orchestrator does (prelude + source)."""
    WIRED[:] = modules
    NOW[0] = 100.0
    noknok._pin_config = None
    noknok._store = None
    mc.nvm[:] = bytearray(4096)
    mc.nvm[0] = 0xC0
    prelude = "HOLD_SEC = %d\nDO_USB = 0\nREPS = 2\nEXPECT = %r\n" % (hold, expect)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(prelude + SRC, "soak_probe_gen.py", "exec"), {"__name__": "__main__"})
    line = [l for l in buf.getvalue().splitlines() if l.startswith("@@NK@@")]
    assert len(line) == 1, buf.getvalue()[-500:]
    return json.loads(line[0][6:]), NOW[0] - 100.0


def mods(**kw):
    """The healthy set on bus A (knob + LED button; the PicoHub is added by tests that need it)."""
    return [FakeModule(KNOB, noknok.Conductor.TYPE_KNOB, (2, 1, 0), **kw.get("knob", {})),
            FakeModule(LBTN, noknok.Conductor.TYPE_LEDBUTTON, (2, 4, 1), **kw.get("lb", {}))]


EXP2 = {"A": {k: v for k, v in BASE["A"].items() if k != HUB}}     # no PicoHub in the fake

print("quiet: everything present")
rep, took = run_probe(mods(), EXP2)
A = rep["buses"]["A"]
check("both modules enumerated", A["n"] == 2 and not A["missing"], A["missing"])
check("no capture", "capture" not in A)
check("no 20 s wait", took < 20, "%.1f s of fake time" % took)
check("uptime reported", "uptime" in rep)

print("missing: a module stays silent")
rep, took = run_probe(mods(lb={"silent": True}), EXP2)
A = rep["buses"]["A"]
cap = A.get("capture")
check("capture present", cap is not None)
check("names the missing module", cap and cap["missing"] == ["ledbutton/%s/0x0c" % LBTN], cap and cap["missing"])
check("raw scan lists the knob's address", cap and cap["scan"] == ["0x08"], cap and cap["scan"])
check("nobody waits at 0x7F", cap and cap["staging"] is None)
check("raw bytes at the missing address: nack", cap and cap["raw"]["ledbutton/0x0c"]["ver4"] == "nack",
      cap and cap["raw"])
check("second enumeration after 20 s still without it",
      cap and cap["second"]["after_s"] == 20 and cap["second"]["n"] == 1, cap and cap["second"])
check("the capture cost the 20 s wait", took >= 20)
check("main result still intact (knob exercised)", A["ok"] > 0)

print("late: a module powers up 12 s late")
rep, took = run_probe(mods(lb={"up_at": 112.0}), EXP2)
cap = rep["buses"]["A"]["capture"]
check("missing the first time", cap["missing"] == ["ledbutton/%s/0x0c" % LBTN])
check("second enumeration finds it", cap["second"]["n"] == 2, cap["second"]["modules"])

print("glitch: first GET_VERSION answers [1,2,0,0]")
rep, took = run_probe(mods(lb={"glitch": 1}), EXP2)
A = rep["buses"]["A"]
cap = A.get("capture")
check("probe saw 2.0.0", any(m["fw"] == "2.0.0" for m in A["modules"]), A["modules"])
check("flagged as an odd version", cap and cap["wrong"] and "2.0.0" in cap["wrong"][0], cap and cap["wrong"])
check("raw version bytes kept (second read is clean)", cap and cap["raw"]["ledbutton/0x0c"]["ver4"] == "01020401",
      cap and cap["raw"])
check("second enumeration reads 2.4.1",
      cap and any(m["fw"] == "2.4.1" for m in cap["second"]["modules"]), cap and cap["second"]["modules"])

print("boot: a module sitting in its bootloader at 0x7E")
rep, took = run_probe(mods(lb={"bootloader": True}), EXP2)
cap = rep["buses"]["A"]["capture"]
check("stage-1 version captured", cap["boot_ver"] == "0101020002", cap["boot_ver"])
check("status bytes captured", cap["boot_status"] == "0000", cap["boot_status"])

print("hold: a module vanishing during idle_hold is counted, not captured")
rep, took = run_probe(mods(lb={"up_at": 130.0}), EXP2, hold=60)
check("hold reports missing loops", (rep["hold"] or {}).get("missing_loops", 0) >= 1, rep["hold"])
check("no 20 s capture inside the hold", took < 60 + 30, "%.0f s" % took)

print("expect: baseline -> probe expectation")
import soak_run  # noqa: E402
exp = soak_run.expect_from({"A": ["knob/%s/0x0a/2.1.0" % KNOB, "picohub/%s/0x08/0.3.0" % HUB],
                            "B": [], "usb": ["leds16/abc/2.2.1"]})
check("parsed", exp == {"A": {KNOB: ["knob", 10, "2.1.0"], HUB: ["picohub", 8, "0.3.0"]}}, exp)
check("no baseline -> nothing expected", soak_run.expect_from(None) == {})

print("report: captures are printed")
import subprocess  # noqa: E402
rep_miss, _ = run_probe(mods(lb={"silent": True}), EXP2)
rec = {"n": 7, "ts": "2026-10-26T12:00:00", "scenario": "cold_boot", "result": "FAIL",
       "reason": "A MISSING ledbutton", "boot_sec": 2.0, "report": rep_miss}
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "soak_t.jsonl")
    open(p, "w").write(json.dumps(rec) + "\n")
    out = subprocess.run([sys.executable, "-I", os.path.join(HERE, "soak", "soak_report.py"), p],
                         capture_output=True, text=True).stdout
check("captures section", "captures: the bus when a module was missing" in out)
check("shows the missing module and the scan", "ledbutton/%s/0x0c" % LBTN in out and "0x08" in out)

print()
print("%d check(s) failed" % fails if fails else "all checks passed")
sys.exit(1 if fails else 0)
