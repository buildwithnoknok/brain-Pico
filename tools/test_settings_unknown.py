# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_settings_unknown.py - DEV-109, tier S (host Python, no hardware).
#
# settings.set from the app (Settings.apply_remote) only accepts keys the product declared
# with s.defaults(): an undeclared key ("turbo": true) is rejected as "unknown setting",
# not stored, not delivered to the product, and does not survive a reboot. Declared keys
# behave as before (type check, read-only info fields); the product's own s.set() is
# unchanged. Run from the repo root:   python -I tools/test_settings_unknown.py

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))
for name in ("busio", "board", "digitalio", "supervisor"):
    sys.modules.setdefault(name, types.ModuleType(name))
mc = types.ModuleType("microcontroller")
mc.nvm = bytearray(4096)
sys.modules["microcontroller"] = mc

import noknok   # noqa: E402

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-62s %s %s" % (name, "PASS" if cond else "FAIL", extra if not cond else ""))
    if not cond:
        fails += 1


def boot():
    """New VM: Store and settings objects re-read from nvm."""
    noknok._store = None
    for attr in ("_settings", "_settings_by_scope"):
        if hasattr(noknok, attr):
            setattr(noknok, attr, None if not isinstance(getattr(noknok, attr), dict) else {})
    s = noknok.Settings(noknok.SETTINGS_KEY, "lamp-test")
    s.defaults({"on": True, "brightness": 120, "color": "#FF0000"})
    return s


print("DEV-109: settings.set rejects undeclared keys")
s = boot()
changed, rejected = s.apply_remote({"turbo": True})
check("undeclared key -> rejected 'unknown setting'", rejected == {"turbo": "unknown setting"}, rejected)
check("  ...nothing changed", changed == {}, changed)
check("  ...not in the values", "turbo" not in s.snapshot()["values"], s.snapshot()["values"])
check("  ...not delivered to the product", "turbo" not in s._pending, s._pending)
changed, rejected = s.apply_remote({"brightness": 200, "turbo": 1, "color": "#00FF00"})
check("mixed request: declared keys applied, the unknown one rejected",
      changed == {"brightness": 200, "color": "#00FF00"} and rejected == {"turbo": "unknown setting"},
      (changed, rejected))
changed, rejected = s.apply_remote({"brightness": "max"})
check("declared key, wrong type -> still 'expected number'",
      rejected.get("brightness") == "expected number", rejected)
s.info("clock", lambda: "12:00")
changed, rejected = s.apply_remote({"clock": "13:00"})
check("read-only info field -> still 'read-only'", rejected == {"clock": "read-only"}, rejected)
changed, rejected = s.apply_remote({7: True})
check("non-string key -> rejected, no crash", 7 in rejected and not changed, rejected)
check("product's own s.set() of an undeclared key still works (device side)",
      s.set("product_internal", 3) is True and s.get("product_internal") == 3)

s.apply_remote({"turbo": True})
saved = s.flush(force=True)
s2 = boot()
check("written to the Store and read back after a reboot: brightness 200, no 'turbo'",
      saved and s2.get("brightness") == 200 and s2.get("color") == "#00FF00"
      and "turbo" not in s2.snapshot()["values"], (saved, s2.snapshot()["values"]))

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
