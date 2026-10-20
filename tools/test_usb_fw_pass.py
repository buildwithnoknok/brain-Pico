# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_usb_fw_pass.py - DEV-133 / DEV-134, tier S (host Python, no hardware).
#
# The Pico PIO-USB host does not see a USB module re-enumerate within one host
# session (DEV-45). So a USB update goes around a hard reset of the brain:
#   boot N    update_module() records "pending" in the Store, sends 0xB0, returns
#             "pending"; code.py hard-resets before the product
#   boot N+1  discover() sees the module at PID 4E42; usb_fw_pass() flashes it
#             from the cache; hard reset again
#   boot N+2  usb_fw_pass() sees the app, verifies the version, clears the record
# Proves (noknok.py 1.18):
#   - a quiet boot does nothing and writes nothing
#   - serial -> type is remembered for every app seen (the rescue's source)
#   - the three boots above, including the Store record at each step
#   - a module back as the app before it was flashed is asked again (max 2)
#   - flash caps (max 2), a failing flash, an unknown serial in its bootloader
#   - update_all() reports a pending USB entry as pending, not OK / FAIL
# and the code.py 0.27 decisions:
#   - a hard reset is requested only on a brain with USB host pins
#   - _usb_fw() turns flashed/asked into a hard reset, failures into an alert
#   - an X035 (picohub) rescue on a USB brain requests a hard reset (DEV-134)
#   - _hard_reset_if_needed() sets the DEV-132 marker only after an online pass
#
# Run from the repo root:   python -I tools/test_usb_fw_pass.py

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


class HardReset(Exception):
    pass


def _hard_reset():
    raise HardReset()


nvm = bytearray(4096)
board = stub("board")
for n in range(29):
    setattr(board, "GP%d" % n, "GP%d" % n)
for name in ("busio", "digitalio", "socketpool", "ssl"):
    stub(name)
stub("microcontroller", nvm=nvm, reset=_hard_reset)
stub("supervisor", reload=lambda: None, runtime=_Any())
stub("wifi", radio=_Any())
stub("adafruit_requests", Session=_Any)
stub("adafruit_connection_manager", get_radio_socketpool=lambda r: _Any(),
     get_radio_ssl_context=lambda r: _Any())
stub("adafruit_httpserver", Server=_Any, Request=_Any, Response=_Any, POST="POST")


# -- a fake noknok_usb: records what the Conductor asks of the USB host --------
class FakeFlasher:
    apps = set()            # serials currently running as the app
    requested = []          # request_bootloader() calls
    flashed = []            # flash() calls (serial, len)
    fail_flash = False

    def __init__(self, *a, **k):
        pass

    def request_bootloader(self, serial):
        FakeFlasher.requested.append(serial)
        return serial in FakeFlasher.apps

    def flash(self, data, serial=None, progress=None, confirm_app=True):
        if FakeFlasher.fail_flash:
            raise RuntimeError("ERASE: no reply from bootloader")
        FakeFlasher.flashed.append((serial, len(data)))
        return True


stub("noknok_usb", UsbModuleFlasher=FakeFlasher, last_bootloaders=[])

import noknok  # noqa: E402

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-66s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails += 1


class Leds16:
    MODULE_TYPE = 0x06

    def __init__(self, serial, version):
        self._uid_hex = serial
        self.firmware_version = version


S = "cdabc2f333bcc25b39e339e3"


def conductor(parked=(), leds16=()):
    """A Conductor as enumerate_usb() leaves it (no I2C needed for this pass)."""
    c = noknok.Conductor.__new__(noknok.Conductor)
    c.leds, c.leds16, c.usb_parked = [], list(leds16), list(parked)
    c._usb_fl = None
    return c


def rec(serial=S):
    return (noknok.store().get("usb") or {}).get(serial)


def reset_fakes():
    FakeFlasher.requested, FakeFlasher.flashed = [], []
    FakeFlasher.apps, FakeFlasher.fail_flash = set(), False


def image_src(version="2.2.1"):
    calls = []

    def get_image(entry):
        calls.append(entry["type"])
        entry["version"] = version
        return b"\x00" * 8428
    return get_image, calls


# ---------------------------------------------------------------------------
print("quiet boot")
reset_fakes()
c = conductor(leds16=[Leds16(S, "2.2.0")])
gi, calls = image_src()
res = c.usb_fw_pass(gi)
check("nothing parked, nothing pending -> no results", res == [])
check("  ...no Store write, no image read, no reset",
      noknok.store().get("usb") is None and not calls and c.usb_needs_reset is False)

print("\nserial -> type remembered")
c._remember_usb_types([(S, "noknokleds16", Leds16(S, "2.2.0"))])
check("16x app seen -> Store usb[serial].type = usb_leds_16x", rec() == {"type": "usb_leds_16x"}, rec())

print("\nboot N: the OTA pass updates the 16x")
reset_fakes()
FakeFlasher.apps = {S}
c = conductor(leds16=[Leds16(S, "2.2.0")])
entry = {"type": "usb_leds_16x", "bus": "usb", "uid": S, "address": None,
         "installed": "2.2.0", "required": "2.2.1", "needs_update": True}
check("update_module(usb) returns 'pending'", c.update_module(entry, b"x") == "pending")
check("  ...0xB0 sent to that serial, nothing flashed",
      FakeFlasher.requested == [S] and not FakeFlasher.flashed)
check("  ...record: pending, 1 request", rec() == {"type": "usb_leds_16x", "pending": True, "asks": 1}, rec())

print("\nupdate_all() reports it as pending")
reset_fakes()
FakeFlasher.apps = {S}
noknok.store().delete("usb")
c = conductor(leds16=[Leds16(S, "2.2.0")])
c.firmware_report = lambda *a, **k: [dict(entry)]
c.enumerate_all = lambda *a, **k: 0
out = c.update_all({"usb_leds_16x": {}}, lambda e: b"x" * 100, logfn=lambda m: None)
check("one result, pending=True, updated=False, no error",
      len(out) == 1 and out[0].get("pending") is True and out[0]["updated"] is False
      and out[0]["error"] is None, out)

print("\nboot N+1: fresh host sees it at PID 4E42")
reset_fakes()
c = conductor(parked=[S])
gi, calls = image_src("2.2.1")
res = c.usb_fw_pass(gi, logfn=lambda m: None)
check("flashed from the image source", [r["action"] for r in res] == ["flashed"]
      and calls == ["usb_leds_16x"] and FakeFlasher.flashed == [(S, 8428)], res)
check("  ...hard reset requested", c.usb_needs_reset is True)
check("  ...record: 1 flash, version 2.2.1 to verify",
      rec().get("tries") == 1 and rec().get("flashed") == "2.2.1" and rec().get("pending"), rec())

print("\nboot N+2: the app is back")
reset_fakes()
c = conductor(leds16=[Leds16(S, "2.2.1")])
res = c.usb_fw_pass(gi, logfn=lambda m: None)
check("verified 2.2.1", [r["action"] for r in res] == ["verified"], res)
check("  ...record back to just the type, no reset",
      rec() == {"type": "usb_leds_16x"} and c.usb_needs_reset is False, rec())
res = c.usb_fw_pass(gi, logfn=lambda m: None)
check("  ...and the boot after that is quiet again", res == [])

print("\nflashed, but the app reports another version")
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "pending": True, "tries": 1,
                               "flashed": "2.2.1"}})
c = conductor(leds16=[Leds16(S, "2.2.0")])
res = c.usb_fw_pass(gi, logfn=lambda m: None)
check("-> failed (alert), record cleared", [r["action"] for r in res] == ["failed"]
      and rec() == {"type": "usb_leds_16x"}, res)

print("\nasked, but back as the app before it was flashed (power lost)")
reset_fakes()
FakeFlasher.apps = {S}
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "pending": True, "asks": 1}})
c = conductor(leds16=[Leds16(S, "2.2.0")])
res = c.usb_fw_pass(gi, logfn=lambda m: None)
check("asked again (0xB0), hard reset", [r["action"] for r in res] == ["asked"]
      and FakeFlasher.requested == [S] and c.usb_needs_reset is True, res)
check("  ...2 requests on record", rec().get("asks") == 2, rec())
res = c.usb_fw_pass(gi, logfn=lambda m: None)
check("third time -> gave_up, record cleared, no reset",
      [r["action"] for r in res] == ["gave_up"] and rec() == {"type": "usb_leds_16x"}
      and c.usb_needs_reset is False, res)

print("\nupdate_module() caps the requests too")
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "pending": True, "asks": 2}})
try:
    conductor().update_module(entry, b"x")
    raised = False
except RuntimeError:
    raised = True
check("2 requests already -> raises, no 0xB0", raised)
noknok.store().delete("usb")
FakeFlasher.apps = set()
try:
    conductor().update_module(entry, b"x")
    raised = False
except RuntimeError:
    raised = True
check("app not on the bus -> raises", raised)

print("\nrescue caps and failures")
reset_fakes()
noknok.store().set("usb", {S: {"type": "usb_leds_16x", "tries": 2, "pending": True}})
c = conductor(parked=[S])
res = c.usb_fw_pass(gi, logfn=lambda m: None)
check("2 flashes already -> gave_up, not flashed, no reset",
      [r["action"] for r in res] == ["gave_up"] and not FakeFlasher.flashed
      and c.usb_needs_reset is False, res)
reset_fakes()
FakeFlasher.fail_flash = True
noknok.store().set("usb", {S: {"type": "usb_leds_16x"}})
c = conductor(parked=[S])
res = c.usb_fw_pass(gi, logfn=lambda m: None)
check("flash raises -> failed, attempt counted, no reset",
      [r["action"] for r in res] == ["failed"] and rec().get("tries") == 1
      and c.usb_needs_reset is False, res)
reset_fakes()
c = conductor(parked=["0000000000000000000000ff"])
res = c.usb_fw_pass(gi, logfn=lambda m: None)
check("unknown serial in its bootloader -> unknown, nothing flashed",
      [r["action"] for r in res] == ["unknown"] and not FakeFlasher.flashed, res)

# ---------------------------------------------------------------------------
print("\ncode.py 0.27 decisions")
src = open(os.path.join(SW, "code.py"), encoding="utf-8").read().rstrip()
assert src.endswith("main()"), "code.py no longer ends with main() - update this test"
ns = {"__name__": "code_under_test"}
exec(compile(src[: -len("main()")], "code.py", "exec"), ns)
ns["log"] = lambda msg: None
events = []
ns["event"] = lambda msg: events.append(msg)
nk = ns["nk"]
I, MAGIC = ns["POST_OTA_NVM_INDEX"], ns["POST_OTA_MAGIC"]


def fresh(usb_pins):
    nk.pins = (lambda g: ("GP0", "GP1") if g == "usb" else None) if usb_pins \
        else (lambda g: None)
    ns["_hard_reset_why"] = None
    ns["_ota_went_online"] = False
    ns["_field_alerts"][:] = []
    events.clear()
    nvm[I] = 0


def resets():
    try:
        ns["_hard_reset_if_needed"]()
        return False
    except HardReset:
        return True


class FakeC:
    def __init__(self, results, needs):
        self._r, self.usb_needs_reset = results, needs

    def usb_fw_pass(self, get_image, logfn=print):
        return self._r


fresh(usb_pins=False)
ns["_need_hard_reset_for_usb"]("x")
check("I2C-only brain: no hard reset requested", ns["_hard_reset_why"] is None and not resets())

fresh(usb_pins=True)
ns["_usb_fw"](FakeC([{"uid": S, "type": "usb_leds_16x", "action": "flashed", "detail": "d"}], True))
check("_usb_fw: flashed -> event + hard reset requested",
      any("[USBFW]" in e and "flashed" in e for e in events) and ns["_hard_reset_why"])
check("  ...not after an online pass: no DEV-132 marker", resets() and nvm[I] == 0)

fresh(usb_pins=True)
ns["_ota_went_online"] = True
ns["_need_hard_reset_for_usb"]("pending")
check("after an online pass: marker set, then hard reset", resets() and nvm[I] == MAGIC)

fresh(usb_pins=True)
ns["_usb_fw"](FakeC([{"uid": S, "type": "usb_leds_16x", "action": "gave_up", "detail": "d"}], False))
check("_usb_fw: gave_up -> customer alert, no reset",
      ns["_field_alerts"] and ns["_hard_reset_why"] is None and not resets())

fresh(usb_pins=True)


class FakeRescueC:
    def rescue_parked_module(self, get_image, logfn=print):
        return {"uid": "cdabfd273abc0490", "type": "picohub", "reason": "r",
                "action": "reflashed", "detail": "16556 bytes"}


ns["_rescue_parked"](FakeRescueC(), lambda e: b"")
check("X035 rescued on a USB brain -> hard reset requested (DEV-134)",
      ns["_hard_reset_why"] is not None and resets())
fresh(usb_pins=False)
ns["_rescue_parked"](FakeRescueC(), lambda e: b"")
check("  ...on an I2C-only brain -> no reset", ns["_hard_reset_why"] is None)

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
