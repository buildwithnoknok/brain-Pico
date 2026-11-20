# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_thirdparty_ops.py - DEV-249, tier S (host Python, no hardware).
#
# code.py's /rpc side of third-party boards, with the REAL noknok_thirdparty library and the
# REAL noknok Store (nvm stub), on fake I2C buses:
#   import   - code.py does not import noknok_thirdparty; ops answer "no conductor" /
#              "no library" when the product has none
#   status   - status.modules = noknok rows + cached third-party rows; events are dropped
#              (never rows) when the reply would pass rpc.MAX_REPLY
#   assign   - bad bus / address / type, noknok module address, nothing answering; a good
#              naming makes the unknown board a soldered_pir row and is saved in the Store
#   reboot   - a fresh attach() (= next boot) applies the saved naming + OLED height
#   set      - OLED height 32 -> 64 (row + Store), errors not found / not supported / bad value
#   forget   - drops the naming, the board is unknown again at once
#   check    - pull-up budget + warnings, pass false below 1000 ohm with a fix suggestion
#   nofat    - no op opens a file for writing
#
# Run from the repo root:   python -I tools/test_thirdparty_ops.py

import builtins
import json
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
    ap_active = False
    connected = False


for name in ("busio", "board", "digitalio", "socketpool", "ssl", "adafruit_requests"):
    stub(name)
stub("microcontroller", nvm=bytearray(8192),
     cpu=types.SimpleNamespace(uid=bytes([0x1F, 0x87, 0x95, 0x1B])))
stub("supervisor", reload=lambda: None, runtime=_Any())
stub("wifi", radio=Radio())
stub("adafruit_connection_manager")
stub("adafruit_httpserver", Server=_Any, Request=_Any, Response=_Any, POST="POST")

import gc                                  # noqa: E402  CircuitPython extras
gc.mem_free = lambda: 100000
gc.mem_alloc = lambda: 50000

src = open(os.path.join(SW, "code.py"), encoding="utf-8").read().rstrip()
assert src.endswith("main()"), "code.py no longer ends with main() - update this test"
ns = {"__name__": "code_under_test"}
exec(compile(src[: -len("main()")], "code.py", "exec"), ns)
ns["log"] = lambda msg: None
ns["register_ops"]()
rpc, nk = ns["rpc"], ns["nk"]
D = rpc.dispatcher()

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-66s %s %s" % (name, "PASS" if cond else "FAIL", "" if cond else extra))
    if not cond:
        fails += 1


def call(op, **args):
    return D.dispatch({"id": 1, "op": op, "args": args})


class Bus:
    """Fake busio.I2C. answers = {address: {reg: bytes}}; reg None = plain read."""
    def __init__(self, answers):
        self.answers = answers

    def try_lock(self):
        return True

    def unlock(self):
        pass

    def _chip(self, a):
        if a not in self.answers:
            raise OSError(19)
        return self.answers[a]

    def writeto(self, a, buf):
        self._chip(a)

    def readfrom_into(self, a, buf):
        d = self._chip(a).get(None, b"\x00")
        buf[:len(d)] = d[:len(buf)]

    def writeto_then_readfrom(self, a, out, buf):
        d = self._chip(a).get(out[0], b"\x00" * len(buf))
        buf[:len(buf)] = (bytes(d) + b"\x00" * len(buf))[:len(buf)]


class Mod:                                # a noknok module in the Conductor registry
    def __init__(self, address, bus):
        self.address, self.i2c_bus, self.firmware_version = address, bus, "3.6.0"
        self._uid_hex = "aa%02x" % address


class Conductor:
    def __init__(self, buses):
        self.buses = buses
        self._registry = {"x": Mod(0x09, "A")}
        self._unknown = {}

    def _buses(self):
        return list(self.buses.items())

    def _scan(self, bus):
        return sorted(bus.answers)


# Bus A: noknok module 0x09, Soldered PIR 0x30 (no ID -> unknown), OLED 0x3C (family).
bus_a = Bus({0x09: {}, 0x30: {None: b"\x00"}, 0x3C: {None: b"\x00"}})
bus_b = Bus({})
c = Conductor({"A": bus_a, "B": bus_b})

# ── import rule ────────────────────────────────────────────────────────────────
print("DEV-249: import rule")
check("code.py source never imports noknok_thirdparty",
      "import noknok_thirdparty" not in src and "__import__(\"noknok_thirdparty\"" not in src)
check("loading code.py did not load the library", "noknok_thirdparty" not in sys.modules)
nk._last_conductor = None
r = call("thirdparty.check")
check("no Conductor -> error no conductor", r.get("error") == "no conductor", r)
nk._last_conductor = c
r = call("thirdparty.assign", bus="A", address=0x30, type="soldered_pir")
check("product without the library -> error no library", r.get("error") == "no library", r)
check("... and the op did not import it", "noknok_thirdparty" not in sys.modules)
st = ns["_h_status"]({}, {})
check("status without the library: noknok rows only",
      [m["type"] for m in st["modules"]] == ["mod"], st["modules"])

# ── the product imports the library and attaches ──────────────────────────────
import noknok_thirdparty as tp            # noqa: E402  (what product.py does)
tp.attach(c, log=lambda m: None)

print("\nDEV-249: status rows")
st = ns["_h_status"]({}, {})
rows = [m for m in st["modules"] if m.get("thirdparty")]
check("status lists noknok module + 2 third-party rows", len(st["modules"]) == 3, st["modules"])
oled = [r for r in rows if r["type"] == "ssd1306"]
check("OLED row: family, height 32, uid tp-ssd1306-A-3c",
      oled and oled[0]["confidence"] == "family" and oled[0]["height"] == 32
      and oled[0]["uid"] == "tp-ssd1306-A-3c" and oled[0]["address"] == 0x3C, rows)
unk = [r for r in rows if r["type"] == "unknown"]
check("unknown at 0x30 with candidates [soldered_pir]",
      unk and unk[0]["candidates"] == ["soldered_pir"] and unk[0]["confidence"] is None, rows)

# status size cap: many long events + rows -> events dropped, rows kept, under MAX_REPLY
saved_events = ns["events"]
ns["events"] = lambda: ["[EVT] %03d %s" % (i, "x" * 280) for i in range(30)]
st = ns["_h_status"]({}, {})
n = len(json.dumps(st))
check("big events + rows: reply under rpc.MAX_REPLY (%d bytes)" % n, n <= rpc.MAX_REPLY - 100)
check("... rows kept, events dropped, newest kept",
      len(st["modules"]) == 3 and 0 < len(st["events"]) < 10
      and st["events"][-1].startswith("[EVT] 029")
      and st["events_since"] == 30 - len(st["events"]), (len(st["events"]), st["events_since"]))
st = ns["_h_status"]({"since": 5}, {})
check("with since: page starts at since, shorter at its end",
      st["events_since"] == 5 and st["events"][0].startswith("[EVT] 005")
      and len(json.dumps(st)) <= rpc.MAX_REPLY - 100, (st["events_since"], len(st["events"])))
ns["events"] = saved_events

# ── no FAT writes from here on ─────────────────────────────────────────────────
real_open = builtins.open
writes = []


def guarded_open(path, mode="r", *a, **k):
    if any(ch in mode for ch in "wax+"):
        writes.append(path)
    return real_open(path, mode, *a, **k)


builtins.open = guarded_open

print("\nDEV-249: thirdparty.assign")
for args, err in ((dict(bus="C", address=0x30, type="soldered_pir"), "bad bus"),
                  (dict(bus="A", address="0x30", type="soldered_pir"), "bad address"),
                  (dict(bus="A", address=True, type="soldered_pir"), "bad address"),
                  (dict(bus="A", address=0x78, type="soldered_pir"), "bad address"),
                  (dict(bus="A", address=0x30, type="bme280"), "unknown type"),
                  (dict(bus="A", address=0x09, type="soldered_pir"), "noknok module"),
                  (dict(bus="A", address=0x31, type="soldered_pir"), "no answer")):
    r = call("thirdparty.assign", **args)
    check("%s -> %s" % (args, err), r.get("ok") is False and r.get("error") == err, r)
r = call("thirdparty.assign", bus="A", address=0x30, type="soldered_pir")
check("name 0x30 soldered_pir -> ok, row confidence user, saved",
      r.get("ok") and r["saved"] and r["row"]["type"] == "soldered_pir"
      and r["row"]["confidence"] == "user" and r["row"]["uid"] == "tp-soldered_pir-A-30", r)
check("Store holds the naming", tp.saved().get("A-30") == {"type": "soldered_pir"}, tp.saved())
st = ns["_h_status"]({}, {})
check("status: no unknown row left, soldered_pir row present",
      [m["type"] for m in st["modules"] if m.get("thirdparty")].count("unknown") == 0
      and any(m["type"] == "soldered_pir" for m in st["modules"]), st["modules"])

print("\nDEV-249: thirdparty.set")
r = call("thirdparty.set", bus="A", address=0x3C, height=64)
check("OLED height 64 -> ok, row height 64, saved",
      r.get("ok") and r["row"]["height"] == 64 and r["saved"], r)
check("Store holds the height", tp.saved().get("A-3c") == {"height": 64}, tp.saved())
for args, err in ((dict(bus="A", address=0x3D, height=64), "not found"),
                  (dict(bus="A", address=0x30, height=64), "not supported"),
                  (dict(bus="A", address=0x3C, width=96), "not supported"),
                  (dict(bus="A", address=0x3C, height=48), "bad value")):
    r = call("thirdparty.set", **args)
    check("%s -> %s" % (args, err), r.get("error") == err, r)

print("\nDEV-249: reboot (fresh attach reads the Store)")
c2 = Conductor({"A": bus_a, "B": bus_b})
tp.attach(c2, log=lambda m: None)
types2 = {r["type"]: r for r in c2.thirdparty_rows}
check("after reboot: soldered_pir named, OLED height 64",
      "soldered_pir" in types2 and types2.get("ssd1306", {}).get("height") == 64, c2.thirdparty_rows)
store_raw = nk.store().get(tp.STORE_KEY)
check("Store backend is nvm (not the FAT)", nk.store().backend in ("nvm", "fram"), nk.store().backend)

print("\nDEV-249: thirdparty.forget")
r = call("thirdparty.forget", bus="A", address=0x30)
check("forget 0x30 -> forgotten true", r.get("ok") and r["forgotten"] is True, r)
st = ns["_h_status"]({}, {})
check("... board is unknown again at once",
      any(m["type"] == "unknown" and m["address"] == 0x30 for m in st["modules"]), st["modules"])
check("... OLED height kept (other entry untouched)",
      any(m.get("height") == 64 for m in st["modules"]), st["modules"])
r = call("thirdparty.forget", bus="A", address=0x30)
check("forget again -> ok, forgotten false", r.get("ok") and r["forgotten"] is False, r)

print("\nDEV-249: thirdparty.check")
r = call("thirdparty.check")
check("one OLED on A -> pass true", r.get("ok") and r["pass"] is True, r)
check("... buses A and B with total_ohms and boards lists",
      set(r["buses"]) == {"A", "B"} and r["buses"]["A"]["boards"][0][1] == 5000
      and r["buses"]["A"]["unknown"] == 1, r["buses"])
check("... warning about the unknown board", any("unknown" in w for w in r["warnings"]), r["warnings"])
# Overload bus A: a second OLED (5 k) + a TMP102 (2.2 k, cuttable jumper):
# 3.3k || 5k || 5k || 2.2k = ~864 ohm. Moving the TMP102 to the empty bus B fixes both.
bus_a.answers[0x3D] = {None: b"\x00"}
bus_a.answers[0x48] = {}
c.thirdparty.append(tp.load("ssd1306")(bus_a, 0x3D, "A"))
c.thirdparty.append(tp.load("tmp102")(bus_a, 0x48, "A"))
r = call("thirdparty.check")
check("overloaded bus A -> pass false", r.get("ok") and r["pass"] is False, r)
check("... below-limit warning + 'move TMP102 to bus B' + cut-jumper hint",
      any("too strong" in w for w in r["warnings"])
      and any("move" in w and "0x48" in w and "bus B" in w for w in r["warnings"])
      and any("jumper" in w.lower() for w in r["warnings"]), r["warnings"])

builtins.open = real_open
check("no file opened for writing by any op (no FAT write)", not writes, writes)

print("\nDEV-249: factory reset wipes the namings")
check("Conductor._RESET_KEYS holds the library's Store key",
      tp.STORE_KEY in nk.Conductor._RESET_KEYS, nk.Conductor._RESET_KEYS)

print("\nDEV-249: ops listed in hello")
ops = rpc.dispatcher().ops()
check("thirdparty.assign/set/forget/check registered",
      all(o in ops for o in ("thirdparty.assign", "thirdparty.set", "thirdparty.forget",
                             "thirdparty.check")), ops)

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
