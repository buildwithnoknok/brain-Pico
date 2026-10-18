# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_rescue_paths.py - DEV-97, tier S (host Python, no hardware).
#
# Proves that no connected boot skips the parked-module rescue, and that a
# rescue that cannot help is reported loudly:
#   1. check_and_flash_modules() runs the cache rescue on every early return:
#      no module_firmware / HTTPS session failed / update not due / nothing
#      resolved (GitHub unreachable).
#   2. _rescue_parked() with a failed rescue writes a [RESCUE] event (the
#      history the app reads via the status op), logs "MODULE STILL PARKED"
#      and queues the buzzer/LED alert; a successful one queues no alert.
#
# Run from the repo root:   python -I tools/test_rescue_paths.py
# code.py is loaded with its hardware/network imports stubbed and WITHOUT the
# final main() call, so nothing boots.

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SW = os.path.join(HERE, "..", "software")
sys.path.insert(0, SW)


def stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class _Any:
    """Accepts any call / attribute: enough for decorators and constructors."""
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Any()
    def __getattr__(self, n): return _Any()


for name in ("busio", "board", "digitalio", "socketpool", "ssl", "adafruit_requests"):
    stub(name)
stub("microcontroller", nvm=bytearray(4096))
stub("supervisor", reload=lambda: None, runtime=_Any())
stub("wifi", radio=_Any())
acm = stub("adafruit_connection_manager",
           get_radio_socketpool=lambda r: object(),
           get_radio_ssl_context=lambda r: object())
stub("adafruit_httpserver", Server=_Any, Request=_Any, Response=_Any, POST="POST")

src = open(os.path.join(SW, "code.py"), encoding="utf-8").read()
src = src.rstrip()
assert src.endswith("main()"), "code.py no longer ends with main() - update this test"
src = src[: -len("main()")]
ns = {"__name__": "code_under_test"}
exec(compile(src, "code.py", "exec"), ns)

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-62s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails += 1


# ── 1. every early return runs the cache rescue ─────────────────────────────
calls = []
ns["get_conductor"] = lambda rescue_get_image=None: calls.append(rescue_get_image)
ns["log"] = lambda msg: None
real_cached = ns["_cached_image"]


def run(label, module_firmware, due=True, https_ok=True, resolved=None):
    calls.clear()
    ns["_ota_check_due"] = lambda: due
    ns["_resolve_module_firmware"] = lambda session, mf: (resolved or {}, None)
    if https_ok:
        acm.get_radio_socketpool = lambda r: object()
    else:
        def boom(r):
            raise OSError("no radio")
        acm.get_radio_socketpool = boom
    ns["check_and_flash_modules"](module_firmware)
    ok = len(calls) == 1 and calls[0] is real_cached
    check(label + " -> cache rescue ran", ok, "(get_conductor calls: %d)" % len(calls))


print("DEV-97: no WiFi boot skips the rescue")
MF = {"buzzer": {"min": "3.3.1"}}
run("no module_firmware", {})
run("no module_firmware (None)", None)
run("HTTPS session setup failed", MF, https_ok=False)
run("update check not due", MF, due=False)
run("due, nothing resolved (GitHub unreachable)", MF, resolved={})

# ── 2. a rescue that cannot help is reported loudly ─────────────────────────
print("\nDEV-97: a failed rescue is loud")
logs, evs = [], []
ns["log"] = lambda msg: logs.append(str(msg))
ns["event"] = lambda msg: evs.append(str(msg))


class FakeConductor:
    def __init__(self, res):
        self.res = res

    def rescue_parked_module(self, get_image, logfn=print):
        return self.res


ns["_field_alerts"].clear()
ns["_rescue_parked"](FakeConductor({"uid": "ab12", "type": "buzzer", "reason": "interrupted update",
                                    "action": "failed", "detail": "no cached image for 'buzzer'"}),
                     real_cached)
check("[RESCUE] event written (status op history)",
      any(e.startswith("[RESCUE]") and "action=failed" in e for e in evs), evs[-1:] and evs[-1])
check("'MODULE STILL PARKED' log line",
      any("MODULE STILL PARKED" in l and "buzzer" in l for l in logs))
check("buzzer/LED alert queued", len(ns["_field_alerts"]) == 1, ns["_field_alerts"])

ns["_field_alerts"].clear(); logs.clear(); evs.clear()
ns["_rescue_parked"](FakeConductor({"uid": "ab12", "type": "buzzer", "reason": "x",
                                    "action": "reflashed", "detail": "2048 bytes"}), real_cached)
check("successful rescue: event, no alert, no STILL PARKED",
      len(evs) == 1 and not ns["_field_alerts"] and not any("STILL PARKED" in l for l in logs))

ns["_field_alerts"].clear(); evs.clear()
ns["_rescue_parked"](FakeConductor(None), real_cached)
check("nothing parked: silent", not evs and not ns["_field_alerts"])

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
