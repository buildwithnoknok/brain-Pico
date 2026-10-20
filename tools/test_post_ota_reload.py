# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_post_ota_reload.py - DEV-132, tier S (host Python, no hardware).
#
# After an online OTA pass the heap is too fragmented for a large product
# (MemoryError, strike 1/3). code.py 0.26 reloads once for a clean heap:
#   - no reload when the pass did not go online (not due / skipped)
#   - reload + nvm marker when it did (also when nothing resolved)
#   - the reloaded run takes + clears the marker and skips the pass entirely
#     (no HTTPS session), so a check that stays due can never loop
#
# Run from the repo root:   python -I tools/test_post_ota_reload.py

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SW = os.path.join(HERE, "..", "software")
sys.path.insert(0, SW)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class _Any:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Any()
    def __getattr__(self, n): return _Any()


class Reloaded(Exception):
    pass


def _reload():
    raise Reloaded()


sessions = []


class Session:
    def __init__(self, *a, **k):
        sessions.append(1)


nvm = bytearray(4096)
for name in ("busio", "board", "digitalio", "socketpool", "ssl"):
    stub(name)
stub("adafruit_requests", Session=Session)
stub("microcontroller", nvm=nvm)
stub("supervisor", reload=_reload, runtime=_Any())
stub("wifi", radio=_Any())
stub("adafruit_connection_manager", get_radio_socketpool=lambda r: _Any(),
     get_radio_ssl_context=lambda r: _Any())
stub("adafruit_httpserver", Server=_Any, Request=_Any, Response=_Any, POST="POST")

src = open(os.path.join(SW, "code.py"), encoding="utf-8").read().rstrip()
assert src.endswith("main()"), "code.py no longer ends with main() - update this test"
ns = {"__name__": "code_under_test"}
exec(compile(src[: -len("main()")], "code.py", "exec"), ns)
ns["log"] = lambda msg: None
rescues = []
ns["_rescue_from_cache"] = lambda why: rescues.append(why)

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-64s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails += 1


def new_run():
    """What main() does at the start of every run."""
    ns["_ota_went_online"] = False
    ns["_skip_ota_once"] = ns["_take_post_ota_marker"]()


def reloads():
    try:
        ns["_reload_for_clean_heap"]()
        return False
    except Reloaded:
        return True


I = ns["POST_OTA_NVM_INDEX"]
check("marker byte is outside code.py's [0:5] and the Store's [64:]", 5 <= I < 64, I)

print("not due: no reload")
new_run()
ns["_ota_check_due"] = lambda: False
sessions.clear(); rescues.clear()
ns["check_and_flash_modules"]({"buzzer": {"min": "1.0.0"}})
check("quiet boot -> no network flag", ns["_ota_went_online"] is False)
check("  ...no reload, marker untouched", not reloads() and nvm[I] == 0)

print("\ndue, registry unreachable (stays due tomorrow)")
new_run()
ns["_ota_check_due"] = lambda: True
ns["_resolve_module_firmware"] = lambda s, mf: ({}, None)
sessions.clear(); rescues.clear()
ns["check_and_flash_modules"]({"buzzer": {"min": "1.0.0"}})
check("went online", ns["_ota_went_online"] is True)
check("  ...reload, marker set", reloads() and nvm[I] == ns["POST_OTA_MAGIC"])

print("\nthe reloaded run")
new_run()
check("marker taken and cleared", ns["_skip_ota_once"] is True and nvm[I] == 0)
sessions.clear(); rescues.clear()
ns["check_and_flash_modules"]({"buzzer": {"min": "1.0.0"}})
check("pass skipped: no HTTPS session, cache rescue only",
      not sessions and rescues and "post-OTA reload" in rescues[-1], rescues)
check("  ...no second reload (no loop although still due)", not reloads())

print("\nthe run after that")
new_run()
check("no marker -> the pass runs again when due", ns["_skip_ota_once"] is False)

print("\nmarker cannot be written")
new_run()
ns["_ota_went_online"] = True
ns["microcontroller"].nvm = None            # nvm[...] = x raises TypeError
check("no marker -> no reload (could loop otherwise)", not reloads())
ns["microcontroller"].nvm = nvm

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
