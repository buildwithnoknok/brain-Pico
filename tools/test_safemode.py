# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_safemode.py - DEV-154, tier S (host Python, no hardware).
#
# safemode.py (run by CircuitPython in safe mode) restarts the brain at most SM_MAX times
# in a row; code.py reports each restart as a [SAFE] event and sets the count back to 0
# after a real power-on or SM_STABLE_S of the product running. Proves:
#   limit    - restart 1, restart 2, then STAY in safe mode (no reboot loop)
#   nvm      - count in nvm[6] (0xA0 | n), reason in nvm[7:23], one write; nothing else
#              in nvm touched (crash count, update time, Store)
#   robust   - a blank or foreign byte counts as 0; an error inside safemode.py never raises
#   report   - code.py logs ONE [SAFE] event with the reason, then clears the reason
#   clear    - the product's stable run (via the service hook) and a POWER_ON boot reset
#              the count, so a later fault gets its two restarts again
#
# Run from the repo root:   python -I tools/test_safemode.py

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SW = os.path.join(HERE, "..", "software")
sys.path.insert(0, SW)


class _Any:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Any()
    def __getattr__(self, n): return _Any()


class Reset(BaseException):
    """microcontroller.reset() never returns on the chip; here it raises."""


NVM = bytearray(b"\xff" * 4096)
writes = []


class Nvm:
    def __getitem__(self, k):
        return NVM[k]

    def __setitem__(self, k, v):
        writes.append(k)
        NVM[k] = v

    def __len__(self):
        return len(NVM)


def _reset():
    raise Reset()


class _Reason:
    name = "HARD_FAULT"

    def __str__(self):
        return "supervisor.SafeModeReason." + self.name


mc = types.ModuleType("microcontroller")
mc.nvm = Nvm()
mc.reset = _reset
sys.modules["microcontroller"] = mc
sv = types.ModuleType("supervisor")
sv.runtime = types.SimpleNamespace(safe_mode_reason=_Reason())
sv.reload = lambda: None
sys.modules["supervisor"] = sv

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-66s %s %s" % (name, "PASS" if cond else "FAIL", extra if not cond else ""))
    if not cond:
        fails += 1


SAFEMODE = open(os.path.join(SW, "safemode.py"), encoding="utf-8").read()


def run_safemode():
    """One safe-mode boot: True if safemode.py restarted the brain."""
    try:
        exec(compile(SAFEMODE, "safemode.py", "exec"), {"__name__": "__main__"})
    except Reset:
        return True
    return False


print("safemode.py: restart at most twice, then stay")
NVM[0:6] = bytes([0xC2, 1, 2, 3, 4, 0x5A])            # crash count, update time, marker
NVM[64:72] = b"NKS1STOR"                              # a Store record
writes.clear()
r1 = run_safemode()
check("fresh nvm (0xFF): restart 1", r1 and NVM[6] == 0xA1, hex(NVM[6]))
check("  ...reason HARD_FAULT stored in nvm[7:23]",
      bytes(NVM[7:23]).rstrip(b"\x00") == b"HARD_FAULT", bytes(NVM[7:23]))
check("  ...one nvm write, nothing else touched", len(writes) == 1 and
      NVM[0:6] == bytes([0xC2, 1, 2, 3, 4, 0x5A]) and NVM[64:72] == b"NKS1STOR", writes)
r2 = run_safemode()
check("second safe mode in a row: restart 2", r2 and NVM[6] == 0xA2, hex(NVM[6]))
writes.clear()
r3 = run_safemode()
check("third in a row: NO restart, stays in safe mode (no loop)", not r3 and NVM[6] == 0xA2)
check("  ...and writes nothing", writes == [], writes)

NVM[6] = 0x00                                          # a foreign value
check("foreign byte counts as 0 -> restart 1", run_safemode() and NVM[6] == 0xA1, hex(NVM[6]))
NVM[6] = 0xA0
_Reason.name = "BROWNOUT_LONG_REASON_NAME_XX"
check("long reason name cut to 16 bytes, restart", run_safemode() and
      bytes(NVM[7:23]) == b"BROWNOUT_LONG_RE")
_Reason.name = "HARD_FAULT"
saved = mc.nvm
mc.nvm = None                                          # nvm broken
try:
    restarted = run_safemode()
    ok = not restarted
except Exception:
    ok = False
mc.nvm = saved
check("an error inside safemode.py never raises (stays in safe mode)", ok)

# ── code.py side ──────────────────────────────────────────────────────────────
for name in ("busio", "board", "digitalio", "socketpool", "ssl", "adafruit_requests",
             "adafruit_connection_manager"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["wifi"] = types.SimpleNamespace(radio=_Any(), AuthMode=_Any())
m = types.ModuleType("adafruit_httpserver")
m.Server, m.Request, m.Response, m.POST = _Any, _Any, _Any, "POST"
sys.modules["adafruit_httpserver"] = m
src = open(os.path.join(SW, "code.py"), encoding="utf-8").read().rstrip()
assert src.endswith("main()"), "code.py no longer ends with main() - update this test"
ns = {"__name__": "code_under_test"}
exec(compile(src[: -len("main()")], "code.py", "exec"), ns)
events, logs, services = [], [], []
ns["event"] = events.append
ns["log"] = logs.append
ns["rpc"] = types.SimpleNamespace(service=lambda: services.append(1))

print("\ncode.py: report, then clear")
NVM[6:23] = bytes([0xA1]) + b"HARD_FAULT" + bytes(6)
check("constants match safemode.py (index, magic, max)",
      (ns["SM_NVM_INDEX"], ns["SM_MAGIC"], ns["SM_MAX"]) == (6, 0xA0, 2) and
      "SM_NVM_INDEX = 6" in SAFEMODE and "SM_MAGIC = 0xA0" in SAFEMODE and "SM_MAX = 2" in SAFEMODE)
ns["_sm_report"]()
check("one [SAFE] event with the reason and the count",
      len(events) == 1 and "[SAFE]" in events[0] and "HARD_FAULT" in events[0] and "1 of 2" in events[0],
      events)
check("  ...reason cleared, count kept", NVM[7:23] == bytes(16) and NVM[6] == 0xA1)
ns["_sm_report"]()
check("  ...no second event on the next boot", len(events) == 1, events)

t = [1000.0]
ns["time"] = types.SimpleNamespace(monotonic=lambda: t[0], sleep=lambda s: None)
ns["_sm_clear_at"] = t[0] + ns["SM_STABLE_S"]
ns["_service"]()
check("service hook: app channel pumped, count untouched before 10 min",
      services == [1] and NVM[6] == 0xA1)
t[0] += ns["SM_STABLE_S"]
writes.clear()
ns["_service"]()
check("after SM_STABLE_S of the product: count back to 0 (one write)",
      NVM[6] == 0xA0 and ns["_sm_clear_at"] is None and len(writes) == 1, (hex(NVM[6]), writes))
writes.clear()
ns["_service"]()
ns["_sm_clear"]()
check("  ...no further nvm writes once it is 0", writes == [], writes)
check("a later fault gets its restarts again", run_safemode() and NVM[6] == 0xA1)
check("POWER_ON boot clears the count (code.py main)",
      "ResetReason.POWER_ON" in src and "_sm_clear()" in src.split("def main():")[1].split("log_new_boot()")[0])
check("[WDT] event not repeated on the post-update reload",
      "and not _skip_ota_once" in src)
check("run_product arms the stable-run clear only when the count is not 0",
      "if _sm_count():" in src.split("def run_product(")[1].split("exec(")[0])
check("the drivers' service hook is code.py's _service (not rpc.service directly)",
      "nk.set_service_hook(_service)" in src and "set_service_hook(rpc.service)" not in src)

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
