# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_rescue_paths.py - DEV-97, tier S (host Python, no hardware).
#
# Proves that no connected boot skips the parked-module rescue, and that a
# rescue that cannot help is reported loudly:
#   3. DEV-124: the PicoHub (carrier) is always resolved; the V003 stage-1 pass skips its X035.
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

src = open(os.path.join(SW, "noknok_main.py"), encoding="utf-8").read()
src = src.rstrip()
assert not src.endswith("main()"), "noknok_main.py must not call main() itself: the code.py stub does (DEV-283)"
ns = {"__name__": "code_under_test"}
exec(compile(src, "noknok_main.py", "exec"), ns)

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

# ── 3. DEV-124: the carrier (PicoHub) is always in the OTA pass ─────────────
print("\nDEV-124: the PicoHub's own firmware")
seen_mf = []
ns["_ota_check_due"] = lambda: True
ns["_resolve_module_firmware"] = lambda session, mf: (seen_mf.append(dict(mf)), ({}, None))[1]
acm.get_radio_socketpool = lambda r: object()
sys.modules["adafruit_requests"].Session = lambda pool, ctx: object()
ns["log"] = lambda msg: None
for label, mf in (("product with no module_firmware", None), ("product with a buzzer", MF)):
    seen_mf.clear()
    ns["check_and_flash_modules"](mf)
    check("%s: picohub resolved too" % label, bool(seen_mf) and "picohub" in seen_mf[0], seen_mf)
check("the product's own floors are kept", bool(seen_mf) and seen_mf[0].get("buzzer") == {"min": "3.3.1"})


# ── 4. DEV-128: stage-1 per chip family, matched by LAYOUT ──────────────────
print("\nDEV-128: stage-1 per layout")


class Mod:
    def __init__(self, uid, addr, bl):
        self._uid_hex, self.address = uid, addr
        if bl is not None:
            self.bootloader = bl


class S1Conductor:
    """A V003 buzzer (layout 2, stage-1 1.2.0) and a PicoHub X035 (layout 3, 1.0.0)."""
    _FW_GROUPS = (("buzzer", "buzzer"), ("_picohub_fw", "picohub"))

    def __init__(self, hub_bl=(1, 1, 0, 0, 3), buzzer_bl=(1, 1, 2, 0, 2)):
        self.mods = {"b": Mod("b", 0x10, buzzer_bl), "h": Mod("h", 0x08, hub_bl)}
        self.buzzer, self._picohub_fw = [self.mods["b"]], [self.mods["h"]]
        self.updates, self.bl_reads = [], []

    def by_uid(self, uid):
        return self.mods.get(uid)

    def bootloader_version(self, entry):
        self.bl_reads.append(entry)

    def stage1_update(self, entry, s1_image, app_image):
        self.updates.append((entry["type"], s1_image, app_image))
        return {"after": (1, 1, 0, 1), "app_restored": True}


S1 = {2: {"version": "1.2.0", "layout": 2, "url": "x", "size": 1, "crc32": "0"},
      3: {"version": "1.0.1", "layout": 3, "url": "x", "size": 1, "crc32": "0"}}
IMG = {"stage1_2": (b"v003", {"version": "1.2.0"}), "stage1_3": (b"x035", {"version": "1.0.1"}),
       "picohub": (b"hubapp", {"version": "0.3.1", "layout": 3}),
       "buzzer": (b"bzapp", {"version": "3.5.0", "layout": 2})}


def cache_read(key):
    if key not in IMG:
        raise ValueError("no cached image for %r" % key)
    return IMG[key]


ns["_cache_read"] = cache_read
# the app is opened as a file-backed image (DEV-205), not read whole: fake that pair
ns["_cache_meta"] = lambda key: IMG[key][1] if key in IMG else None
ns["_cache_paths"] = lambda key: (key, key + ".json")
_fetch = types.ModuleType("noknok_fetch")


def _open_cached(path, meta):
    if path not in IMG:
        raise ValueError("no cached image at %s" % path)
    return IMG[path][0]


_fetch.open_cached = _open_cached
sys.modules["noknok_fetch"] = _fetch
ns["_field_alerts"].clear(); evs.clear()
sc = S1Conductor()
ns["_stage1_pass"](sc, S1)
check("X035 1.0.0 -> 1.0.1 gets the layout-3 image + the cached picohub app",
      sc.updates == [("picohub", b"x035", b"hubapp")], sc.updates)
check("V003 buzzer at 1.2.0 untouched", all(u[0] != "buzzer" for u in sc.updates))
check("no alert on the happy path", not ns["_field_alerts"], ns["_field_alerts"])

sc = S1Conductor(buzzer_bl=(1, 1, 0, 0, 2))      # older V003 stage-1
ns["_stage1_pass"](sc, S1)
check("old V003 gets ONLY the layout-2 image, never the X035 one",
      ("buzzer", b"v003", b"bzapp") in sc.updates
      and all(not (t == "buzzer" and i == b"x035") for t, i, _ in sc.updates), sc.updates)

sc = S1Conductor()
ns["_stage1_pass"](sc, {2: S1[2]})               # old registry: no X035 entry
check("no stage-1 for layout 3 published: X035 skipped, no update",
      sc.updates == [], sc.updates)

sc = S1Conductor(hub_bl=(1, 1, 0, 0, None))      # layout not reported
ns["_stage1_pass"](sc, S1)
check("module without a reported layout: skipped", sc.updates == [], sc.updates)

sc = S1Conductor()
saved = dict(IMG); del IMG["stage1_3"]
ns["_stage1_pass"](sc, S1)
IMG.update(saved)
check("X035 stage-1 missing from the cache: skipped", sc.updates == [], sc.updates)

INDEXES = {
    "v": {"version": "1.2.0", "url": "u", "layout": 2, "size": 5, "crc32": "a"},
    "x": {"version": "1.0.1", "url": "u", "layout": 3, "size": 5, "crc32": "b"},
    "bad": {"version": "9.9.9", "url": "u", "layout": 3, "size": 5},       # no crc32
}
ns["_download_json"] = lambda session, url, timeout=None: INDEXES[url]
ns["INDEX_TIMEOUT_S"] = 1
r = ns["_resolve_stage1"](None, {"bootloader": {"stage1": {
    "CH32V003": {"index": "v"}, "CH32X035": {"index": "x"}}}})
check("registry 2.0.0: both families, keyed by layout", sorted(r) == [2, 3], r)
r = ns["_resolve_stage1"](None, {"bootloader": {"stage1": {"index": "v"}}})
check("registry 1.x shape: one entry", sorted(r) == [2], r)
r = ns["_resolve_stage1"](None, {"bootloader": {"stage1": {
    "CH32V003": {"index": "v"}, "CH32X035": {"index": "bad"}}}})
check("one family with an incomplete index: only that one is dropped", sorted(r) == [2], r)
check("no bootloader section: empty", ns["_resolve_stage1"](None, {}) == {})

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
