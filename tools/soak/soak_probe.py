# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# soak_probe.py - ONE soak iteration, run ON the Pico via `pico.py run`.
#
# Enumerates BOTH I2C buses (the dual-bus layout we ship going forward),
# exercises every module it finds, and prints exactly one machine-readable
# line:   @@NK@@{...json...}
# The Pi-side orchestrator (soak_run.py) parses that line; everything else
# printed here is human noise kept only when an iteration fails.
#
# Rules this script obeys:
#   * NEVER writes the filesystem (DEV-18: no runtime FS writes).
#   * NEVER makes noise - the buzzer is only ever READ (is_playing/stop), so a
#     soak can run overnight next to a sleeping human.
#   * LEDs are driven dim and briefly; the display backlight is held at 15%.
#   * Never raises: every module operation is counted, not fatal.
#
# The orchestrator prepends a small prelude defining HOLD_SEC / DO_USB / REPS /
# EXPECT (the baseline: {bus: {uid: [type, addr, fw]}}). When a baselined module
# is MISSING, or answers with a version that is not its baseline one, the probe
# CAPTURES what the bus looks like (DEV-165 / DEV-166): see capture().

import gc
import json
import sys
import time

import board
import microcontroller
import supervisor

import noknok
from noknok import Conductor

# ── Prelude-supplied knobs (defaults when run standalone) ────────────────────
try:
    HOLD_SEC
except NameError:
    HOLD_SEC = 0          # >0 = idle-hold scenario: keep hammering I2C this long
try:
    DO_USB
except NameError:
    DO_USB = 0            # 1 = also enumerate the USB (PIO-USB) side
try:
    REPS
except NameError:
    REPS = 10             # I2C transactions per module per pass
try:
    EXPECT
except NameError:
    EXPECT = {}           # no baseline yet (or standalone): nothing to compare, no capture
CAPTURE_WAIT_SEC = 20     # second enumeration this long after a capture

# The I2C buses settings.toml configures (DEV-74: no pins in code).
BUSES = tuple((name, label, sda, scl) for name, sda, scl, label in noknok.i2c_bus_pins())
if not BUSES:
    raise SystemExit(noknok.NO_PINS_MSG)

TYPE_NAME = {
    "NoknokBuzzer": "buzzer",
    "NoknokKnob": "knob",
    "NoknokLedButton": "ledbutton",
    "NoknokDisplay": "display",
}


hub_status = {}          # last PicoHub GET_STATUS of the current bus scan (read-only)


class Counter:
    """Counts ok/err over module operations and remembers distinct failures."""

    def __init__(self):
        self.ok = 0
        self.err = 0
        self.seen = []

    def do(self, label, fn):
        try:
            fn()
            self.ok += 1
        except Exception as e:                      # noqa: BLE001 - soak must not die
            self.err += 1
            tag = "%s:%s:%s" % (label, type(e).__name__, e)
            if tag not in self.seen and len(self.seen) < 8:
                self.seen.append(tag)


def exercise(module, kind, c):
    """A representative burst of real traffic for one module. Quiet + low power."""
    for _ in range(REPS):
        if kind == "buzzer":
            # READ ONLY - a soak must not beep all night.
            c.do("buzzer.is_playing", module.is_playing)
        elif kind == "knob":
            c.do("knob.read", module.read)
            c.do("knob.position", lambda: module.position)
        elif kind == "ledbutton":
            c.do("ledbutton.read", module.read)
            c.do("ledbutton.set_color", lambda: module.set_color(6, 0, 6))
            c.do("ledbutton.led_off", module.led_off)
        elif kind == "picohub":
            # GET_STATUS only: read-only. Never set_rail/cycle_rail/reset_pico.
            c.do("picohub.status", lambda: hub_status.update(module.status()))
        elif kind == "display":
            c.do("display.status", module.status)
            c.do("display.fill_rect", lambda: module.fill_rect(0, 0, 8, 8, 0x001F))
            c.do("display.wait_ready", lambda: module.wait_ready(1.0))


def _hex(buf):
    return None if buf is None else bytes(buf).hex()


def _fingerprint(c):
    """[(type, uid, addr, fw)] of everything the Conductor currently has on its bus."""
    out = []
    for kind in ("buzzer", "knob", "ledbutton", "display"):
        for m in getattr(c, kind, []):
            out.append({"type": kind, "uid": getattr(m, "_uid_hex", None),
                        "addr": m.address, "fw": getattr(m, "firmware_version", None)})
    hub = getattr(c, "picohub", None)
    if hub is not None:
        out.append({"type": "picohub", "uid": getattr(hub, "_uid_hex", None),
                    "addr": hub.address, "fw": getattr(hub, "firmware_version", None)})
    return out


def capture(c, expect, found):
    """What does the bus look like right now, when a baselined module is missing or
    reports an odd version? Read-only: raw scan, the 0x7F hand-out address (nothing
    is assigned by reading it), the bootloader address 0x7E (GET_VERSION only), the
    raw bytes at each suspect module's own address; then wait and enumerate again
    to see whether it comes back by itself (a slow boot) or stays gone (stuck).
    `expect` = {uid: [type, addr, fw]}, `found` = [{type, uid, addr, fw}]."""
    bus = c.i2c
    cap = {"uptime": round(time.monotonic(), 1), "missing": [], "wrong": [],
           "scan": None, "staging": None, "boot_status": None, "boot_ver": None,
           "raw": {}, "second": None}
    have = {f["uid"]: f for f in found}
    for uid, (typ, addr, fw) in expect.items():
        f = have.get(uid)
        if f is None:
            cap["missing"].append("%s/%s/0x%02x" % (typ, uid, addr))
        elif f["fw"] != fw:
            cap["wrong"].append("%s/%s fw %s (baseline %s)" % (typ, uid, f["fw"], fw))

    def probe_addr(addr):
        """Plain 2-byte read, then GET_VERSION 0xB1 and its 4 raw bytes."""
        r = {"read2": _hex(c._read(addr, 2, bus))}
        if c._write(addr, [0xB1], bus):
            time.sleep(0.003)
            r["ver4"] = _hex(c._read(addr, 4, bus))
        else:
            r["ver4"] = "nack"
        return r

    try:
        cap["scan"] = ["0x%02x" % a for a in c._scan(bus)]
        cap["staging"] = _hex(c._read(0x7F, 10, bus))         # UID(8) type CRC, or None = nobody waits
        cap["boot_status"] = _hex(c._read(0x7E, 2, bus))
        if c._write(0x7E, [0xB1], bus):
            time.sleep(0.005)
            cap["boot_ver"] = _hex(c._read(0x7E, 5, bus))      # stage-1: proto, major, minor, patch, layout
        for uid, (typ, addr, fw) in expect.items():
            f = have.get(uid)
            if f is None or f["fw"] != fw:
                cap["raw"]["%s/0x%02x" % (typ, addr)] = probe_addr(addr if f is None else f["addr"])
    except Exception as e:                                    # noqa: BLE001 - capture must not kill the probe
        cap["error"] = "%s: %s" % (type(e).__name__, e)

    try:
        time.sleep(CAPTURE_WAIT_SEC)
        t0 = time.monotonic()
        n = c.enumerate(total_timeout_sec=8)
        sec = {"after_s": CAPTURE_WAIT_SEC, "n": n, "enum_sec": round(time.monotonic() - t0, 2),
               "modules": _fingerprint(c), "scan": ["0x%02x" % a for a in c._scan(c.i2c)],
               "staging": _hex(c._read(0x7F, 10, c.i2c))}
        if getattr(c, "picohub", None) is not None:
            try:
                st = c.picohub.status()
                sec["hub"] = {k: st.get(k) for k in ("pd_state", "vin", "v5v", "faults",
                                                      "warnings", "reset_cause")}
            except Exception as e:                            # noqa: BLE001
                sec["hub"] = "status failed: %s" % e
        cap["second"] = sec
    except Exception as e:                                    # noqa: BLE001
        cap["second"] = "failed: %s: %s" % (type(e).__name__, e)
    return cap


def scan_bus(name, pins, sda, scl, do_capture=True):
    """Enumerate one bus, exercise what is on it, return a result dict."""
    res = {"pins": pins, "n": 0, "modules": [], "ok": 0, "err": 0,
           "errors": [], "enum_sec": None, "fatal": None}
    c = None
    try:
        t0 = time.monotonic()
        c = Conductor(sda=sda, scl=scl)
        n = c.enumerate(total_timeout_sec=8)
        res["enum_sec"] = round(time.monotonic() - t0, 2)
        res["n"] = n

        counter = Counter()
        hub_status.clear()
        # c.picohub is a single module (the carrier's power manager) or None.
        for kind in ("buzzer", "knob", "ledbutton", "display", "picohub"):
            mods = getattr(c, kind, [])
            if kind == "picohub":
                mods = [mods] if mods is not None else []
            for m in mods:
                res["modules"].append({
                    "uid": getattr(m, "_uid_hex", None),
                    "type": kind,
                    "addr": m.address,
                    "fw": getattr(m, "firmware_version", None),
                    "proto": getattr(m, "protocol_version", None),
                })
                if kind == "display":
                    counter.do("display.backlight", lambda: m.backlight(0.15))
                exercise(m, kind, counter)

        if hub_status:
            res["hub"] = {k: hub_status.get(k) for k in
                          ("pd_state", "contract_v", "vin", "v5v", "temp_c",
                           "faults", "warnings", "reset_cause")}
        res["ok"] = counter.ok
        res["err"] = counter.err
        res["errors"] = counter.seen

        found = _fingerprint(c)
        expect = EXPECT.get(name) or {}
        have = {f["uid"]: f for f in found}
        res["missing"] = [u for u in expect if u not in have]
        if do_capture and expect and (res["missing"] or
                                      any(have[u]["fw"] != expect[u][2] for u in expect if u in have)):
            res["capture"] = capture(c, expect, found)
    except Exception as e:                          # noqa: BLE001
        res["fatal"] = "%s: %s" % (type(e).__name__, e)
    finally:
        if c is not None:
            try:
                c.i2c.deinit()
            except Exception:
                pass
    return res


def hold(seconds):
    """Idle-hold: keep both buses busy for `seconds` and watch memory drift.
    Catches slow leaks and bus lock-ups that a 20 s probe never reaches."""
    out = {"sec": seconds, "loops": 0, "ok": 0, "err": 0,
           "mem_start": None, "mem_end": None, "errors": []}
    gc.collect()
    out["mem_start"] = gc.mem_free()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for (name, pins, sda, scl) in BUSES:
            r = scan_bus(name, pins, sda, scl, do_capture=False)
            if r.get("missing"):                    # a module that vanished under load
                out["missing_loops"] = out.get("missing_loops", 0) + 1
                tag = "%s:missing %s" % (name, ",".join(r["missing"]))
                if tag not in out["errors"] and len(out["errors"]) < 8:
                    out["errors"].append(tag)
            out["ok"] += r["ok"]
            out["err"] += r["err"]
            if r["fatal"] and len(out["errors"]) < 8:
                out["errors"].append("%s:%s" % (name, r["fatal"]))
            for e in r["errors"]:
                if e not in out["errors"] and len(out["errors"]) < 8:
                    out["errors"].append(e)
        out["loops"] += 1
        gc.collect()
    out["mem_end"] = gc.mem_free()
    return out


def main():
    gc.collect()
    rep = {
        "cp": "%d.%d.%d" % sys.implementation.version[:3],
        "reset_reason": str(microcontroller.cpu.reset_reason).split(".")[-1],
        "run_reason": str(supervisor.runtime.run_reason).split(".")[-1],
        "safe_mode": str(supervisor.runtime.safe_mode_reason).split(".")[-1],
        "nvm0": microcontroller.nvm[0],
        "uptime": round(time.monotonic(), 1),   # near 0 = the Pico really lost power
        "mem_start": gc.mem_free(),
        "hold_sec": HOLD_SEC,
        "buses": {},
        "usb": None,
        "hold": None,
    }

    for (name, pins, sda, scl) in BUSES:
        rep["buses"][name] = scan_bus(name, pins, sda, scl)

    if DO_USB:
        u = {"n": 0, "modules": [], "error": None}
        c = None
        try:
            # Pins from settings.toml (DEV-74), the USB host pins included.
            c = Conductor()
            n = c.enumerate_usb()
            u["n"] = n
            # LEDs (8x) and LEDs 16x: enumeration only, nothing is lit.
            for kind, lst in (("leds", getattr(c, "leds", [])),
                              ("leds16", getattr(c, "leds16", []))):
                for m in lst:
                    u["modules"].append({
                        "kind": kind,
                        "serial": getattr(m, "_uid_hex", None) or getattr(m, "serial", None),
                        "fw": getattr(m, "firmware_version", None),
                    })
        except Exception as e:                      # noqa: BLE001
            u["error"] = "%s: %s" % (type(e).__name__, e)
        finally:
            if c is not None:
                try:
                    c.i2c.deinit()
                except Exception:
                    pass
        rep["usb"] = u

    if HOLD_SEC > 0:
        rep["hold"] = hold(HOLD_SEC)

    gc.collect()
    rep["mem_end"] = gc.mem_free()
    print("@@NK@@" + json.dumps(rep))


if not globals().get("SOAK_NO_MAIN"):      # a host test sets this to import the functions
    main()
