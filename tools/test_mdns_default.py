# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_mdns_default.py - DEV-70, tier S (host Python, no hardware).
#
# mDNS (noknok-XXXX.local) is ON by default since code.py 0.36: a settings.toml
# without NOKNOK_MDNS, or with 1, advertises the name; only NOKNOK_MDNS = 0 switches
# it off. The repo settings.toml ships NOKNOK_MDNS = 1.
#
# Run from the repo root:   python -I tools/test_mdns_default.py
# code.py is loaded with its hardware/network imports stubbed and WITHOUT the
# final main() call, so nothing boots (same harness as test_setup_join.py).

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


def stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m


for name in ("busio", "board", "digitalio", "socketpool", "ssl", "adafruit_requests",
             "adafruit_connection_manager"):
    stub(name)
stub("microcontroller", nvm=bytearray(4096))
stub("supervisor", reload=lambda: None, runtime=_Any())
stub("wifi", radio=_Any(), AuthMode=_Any())
stub("adafruit_httpserver", Server=_Any, Request=_Any, Response=_Any, POST="POST")

src = open(os.path.join(SW, "code.py"), encoding="utf-8").read().rstrip()
assert src.endswith("main()"), "code.py no longer ends with main() - update this test"
ns = {"__name__": "code_under_test"}
exec(compile(src[: -len("main()")], "code.py", "exec"), ns)

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-60s %s %s" % (name, "PASS" if cond else "FAIL", extra if not cond else ""))
    if not cond:
        fails += 1


print("DEV-70: mDNS default")
for value, want in ((None, True), ("1", True), ("0", False), (" 0 ", False), ("yes", True)):
    os.environ.pop("NOKNOK_MDNS", None)
    if value is not None:
        os.environ["NOKNOK_MDNS"] = value
    got = ns["_mdns_wanted"]()
    check("NOKNOK_MDNS %-6s -> mDNS %s" % ("unset" if value is None else repr(value),
                                          "on" if want else "off"), got == want, got)
os.environ.pop("NOKNOK_MDNS", None)
toml = open(os.path.join(SW, "settings.toml"), encoding="utf-8").read()
check("repo settings.toml ships NOKNOK_MDNS = 1",
      any(l.split("#")[0].replace(" ", "") == "NOKNOK_MDNS=1" for l in toml.splitlines()))
check("both mDNS start paths use _mdns_wanted()",
      src.count("_mdns_wanted()") >= 2 and 'env("NOKNOK_MDNS", "0")' not in src)

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
