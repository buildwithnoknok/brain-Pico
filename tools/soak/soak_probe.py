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
# The orchestrator prepends a small prelude defining HOLD_SEC / DO_USB / REPS.

import gc
import json
import sys
import time

import board
import microcontroller
import supervisor

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

# The dual-bus PicoHub layout. I2C0 = CN2/CN5/CN7, I2C1 = CN1/CN4/CN6.
BUSES = (
    ("i2c0", "GP20/21", board.GP20, board.GP21),
    ("i2c1", "GP18/19", board.GP18, board.GP19),
)

TYPE_NAME = {
    "NoknokBuzzer": "buzzer",
    "NoknokKnob": "knob",
    "NoknokLedButton": "ledbutton",
    "NoknokDisplay": "display",
}


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
        elif kind == "display":
            c.do("display.status", module.status)
            c.do("display.fill_rect", lambda: module.fill_rect(0, 0, 8, 8, 0x001F))
            c.do("display.wait_ready", lambda: module.wait_ready(1.0))


def scan_bus(name, pins, sda, scl):
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
        for kind in ("buzzer", "knob", "ledbutton", "display"):
            for m in getattr(c, kind, []):
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

        res["ok"] = counter.ok
        res["err"] = counter.err
        res["errors"] = counter.seen
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
            r = scan_bus(name, pins, sda, scl)
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
            # Pins of a REAL bus, not the GP8/9 default - a Conductor built on
            # empty pins prints "I2C bus unavailable", which is noise that could
            # mask a genuine bus fault in the log.
            c = Conductor(sda=board.GP18, scl=board.GP19)
            n = c.enumerate_usb()
            u["n"] = n
            for m in getattr(c, "leds", []):
                u["modules"].append({
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


main()
