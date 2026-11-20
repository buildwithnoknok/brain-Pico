# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_capabilities.py - DEV-268, tier S (host Python, no hardware).
#
# code.py reports what the brain can drive and refuses a product that needs more:
#   - _capabilities(): "i2c" always; "i2c_b" when bus B pins are set; "usb_host" only when the
#     USB pins are set AND the build has the usb_host module (an ESP32-S3 build has not)
#   - hello carries `capabilities`
#   - provision with `requires` the brain lacks -> accepted false, reason brain_lacks,
#     missing[]; nothing handed to the join loop / no switch parked. Without `requires`
#     (older app) or with what the brain has -> unchanged behaviour
#
# Run from the repo root:   python -I tools/test_capabilities.py
# code.py is loaded with its hardware/network imports stubbed and WITHOUT main().

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


class Radio:
    ap_active = True
    connected = False


for name in ("busio", "board", "digitalio", "socketpool", "ssl", "adafruit_requests"):
    stub(name)
stub("microcontroller", nvm=bytearray(4096),
     cpu=types.SimpleNamespace(uid=bytes([0x1F, 0x87, 0x95, 0x1B])))
stub("supervisor", reload=lambda: None, runtime=_Any())
stub("wifi", radio=Radio())
stub("adafruit_connection_manager")
stub("adafruit_httpserver", Server=_Any, Request=_Any, Response=_Any, POST="POST")

src = open(os.path.join(SW, "code.py"), encoding="utf-8").read().rstrip()
assert src.endswith("main()"), "code.py no longer ends with main() - update this test"
ns = {"__name__": "code_under_test"}
exec(compile(src[: -len("main()")], "code.py", "exec"), ns)
ns["log"] = lambda msg: None
ns["_scan_cache"] = None                    # no pre-AP scan: the network check is skipped

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-66s %s %s" % (name, "PASS" if cond else "FAIL", extra if not cond else ""))
    if not cond:
        fails += 1


PICOHUB = {"i2c_a": ("GP10", "GP11"), "i2c_b": ("GP20", "GP21"), "usb": ("GP0", "GP1"),
           "source": "settings", "problems": []}
pins = dict(PICOHUB)
ns["nk"].pin_config = lambda: pins


def with_usb_host_module(present):
    if present:
        stub("usb_host")
    else:
        sys.modules["usb_host"] = None       # import usb_host -> ImportError


print("DEV-268: _capabilities()")
with_usb_host_module(True)
check("PicoHub pins + usb_host build -> i2c, i2c_b, usb_host",
      ns["_capabilities"]() == ["i2c", "i2c_b", "usb_host"], ns["_capabilities"]())
with_usb_host_module(False)
check("USB pins set but no usb_host in the build (ESP32-S3) -> no usb_host",
      ns["_capabilities"]() == ["i2c", "i2c_b"], ns["_capabilities"]())
with_usb_host_module(True)
pins = dict(PICOHUB, usb=None, i2c_b=None)
check("no USB pins, no bus B -> i2c only", ns["_capabilities"]() == ["i2c"], ns["_capabilities"]())


def boom():
    raise OSError("settings.toml unreadable")


ns["nk"].pin_config = boom
check("pin_config() raises -> i2c only, no exception", ns["_capabilities"]() == ["i2c"])
ns["nk"].pin_config = lambda: pins

print("\nDEV-268: hello")
pins = dict(PICOHUB)
ns["load_wifi_credentials"] = lambda: None
h = ns["_h_hello"]({}, {})
check("hello carries capabilities", h.get("capabilities") == ["i2c", "i2c_b", "usb_host"],
      h.get("capabilities"))

print("\nDEV-268: provision checks requires")
ns["pending"].clear()
pins = dict(PICOHUB, usb=None)
r = ns["_do_provision"]("Home", "pw", "https://x/p.py", None, "multicolor-lamp-16x-v1",
                        requires=["usb_host"])
check("setup AP, needs usb_host, brain has none -> brain_lacks, missing [usb_host]",
      r.get("accepted") is False and r.get("reason") == "brain_lacks"
      and r.get("missing") == ["usb_host"], r)
check("... credentials NOT handed to the join loop", not ns["pending"].get("ready"), ns["pending"])
r = ns["_do_provision"]("Home", "pw", "https://x/p.py", None, "button-light-v1", requires=[])
check("setup AP, requires [] -> accepted (setup)", r.get("accepted") is True and r["mode"] == "setup", r)
ns["pending"].clear()
r = ns["_do_provision"]("Home", "pw", "https://x/p.py", None, "button-light-v1")
check("older app (no requires) -> accepted, no check", r.get("accepted") is True, r)
ns["pending"].clear()
pins = dict(PICOHUB)
r = ns["_do_provision"]("Home", "pw", "https://x/p.py", None, "x-v1", requires=["usb_host", "ble"])
check("needs usb_host + ble, brain has usb_host -> missing [ble] only",
      r.get("reason") == "brain_lacks" and r.get("missing") == ["ble"], r)

Radio.ap_active = False
saved = []
ns["load_wifi_credentials"] = lambda: {"ssid": "Home"}
ns["nk"].store = lambda: types.SimpleNamespace(set=lambda k, v: saved.append(k) or True)
pins = dict(PICOHUB, usb=None)
r = ns["_do_provision"](None, None, "https://x/p.py", None, "multicolor-lamp-16x-v1",
                        requires=["usb_host"])
check("home WiFi product switch, brain lacks usb_host -> refused, no switch parked",
      r.get("reason") == "brain_lacks" and not saved, (r, saved))

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
