# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev65.py - DEV-65, tier M: the flash-layout gate on REAL modules.
# Runs on the Pico via `pico.py run`. Needs noknok.py >= 1.12 and module_flasher.py.
#
# It never flashes anything: get_image() and ModuleFlasher.flash() are replaced
# by tripwires, so a failure of the gate shows up as "TRIPWIRE", not a bricked module.
#
#   1. read-only report of every I2C module: installed vs image layout
#   2. mismatch: the manifest claims the WRONG layout for a module that is outdated
#      on paper (required version 99.0.0) -> must be BLOCKED, never written
#   3. fail closed: the same without any layout in the manifest -> BLOCKED
#   4. legacy/layout-1 module (only if one is on the bus): shown, blocked from a layout-2 image
#
# The strongest proof (a real layout-1 module vs a real layout-2 image) needs a
# layout-1 board on the bus; with only layout-2 modules the manifest is made to lie
# about the image layout instead - the code path is the same comparison.

import board
import noknok
from noknok import Conductor

BUSES = (("GP20/21", board.GP20, board.GP21), ("GP8/9", board.GP8, board.GP9),
         ("GP18/19", board.GP18, board.GP19))
fails = 0
tested = 0          # modules actually exercised - zero means the test proved nothing


def check(name, cond, extra=""):
    global fails
    print("  %-60s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails += 1


class Tripwire(Exception):
    pass


def get_image(entry):
    raise Tripwire("get_image() called for %s - the gate did not stop it" % entry["type"])


# Any flash call on the real flasher is a failure, whatever the caller does.
import module_flasher
_real_flash = module_flasher.ModuleFlasher.flash


def _tripwire_flash(self, *a, **k):
    raise Tripwire("ModuleFlasher.flash() was called")


module_flasher.ModuleFlasher.flash = _tripwire_flash

print("noknok.py", noknok.__version__)
try:
    for (label, sda, scl) in BUSES:
        c = Conductor(sda=sda, scl=scl)
        c.enumerate(total_timeout_sec=6)
        mods = [(k, m) for k in ("buzzer", "knob", "ledbutton", "display") for m in getattr(c, k)]
        if not mods:
            print("\n== %s: no I2C modules" % label)
            c.i2c.deinit() if getattr(c, "i2c", None) else None
            continue
        print("\n== %s: %d module(s)" % (label, len(mods)))
        tested += len(mods)

        # 1. read-only report with the real layout read from each module
        # The fake "required" version must have the SAME MAJOR as what is installed:
        # a major-version gap is "confirm before flashing" (no update offered), and the
        # layout gate only looks at modules that have an update pending.
        fake = {}
        for k, m in mods:
            t = {"ledbutton": "led_button"}.get(k, k)
            fake[t] = "%s.99.0" % (getattr(m, "firmware_version", None) or "0").split(".")[0]

        def manifest(layout):
            return {t: dict({"version": v, "url": "x"}, **({} if layout is None else {"layout": layout}))
                    for t, v in fake.items()}

        rep = c.firmware_report(manifest(2), read_layout=True, strict_layout=True)
        for r in rep:
            print("    %-11s uid=%s fw %-7s layout installed=%s image=%s  %s"
                  % (r["type"], r["uid"], r["installed"], r["layout_installed"],
                     r["layout_image"], r["blocked"] or "ok"))
        check("every module has an update pending (so the gate looked at it)",
              len(rep) == len(mods) and all(r["blocked"] or r["needs_update"] for r in rep))
        check("a module whose layout is unknown/legacy is blocked, not 'ok'",
              all(r["blocked"] for r in rep if r["layout_installed"] in (None, 0)))

        # 2. wrong layout in the manifest -> every module with another layout is BLOCKED
        for wrong in (1, 2):
            lie = manifest(wrong)
            res = c.update_all(lie, get_image, logfn=lambda *a: None)
            mismatched = [r for r in rep if r["layout_installed"] != wrong]
            refused = {r["uid"] for r in res if not r["updated"] and r["error"]}
            check("image layout %d: every mismatching module refused, none written" % wrong,
                  {r["uid"] for r in mismatched} <= refused
                  and not any(r["updated"] for r in res if r["layout_installed"] != wrong))
        # (a MATCHING layout passes the gate and reaches get_image(); the tripwire
        # raises there, update_all() records the failure - nothing is flashed)

        # 3. fail closed: no layout in the manifest
        res = c.update_all(manifest(None), get_image, logfn=lambda *a: None)
        check("manifest without layout: all refused, none written",
              len(res) == len(mods) and not any(r["updated"] for r in res))

        # 4. versions unchanged by all of the above (read back from the hardware)
        before = {r["uid"]: r["installed"] for r in rep}
        c.enumerate(total_timeout_sec=6)
        after = {r["uid"]: r["installed"] for r in c.firmware_report({})}
        check("installed versions unchanged after the run", before == after)
        try:
            c.i2c.deinit()
        except Exception:
            pass
finally:
    module_flasher.ModuleFlasher.flash = _real_flash

if tested == 0:
    print("\nNO MODULES FOUND on any bus - nothing was tested (not a pass)")
    fails += 1
print("\n%s" % ("ALL PASS (%d module(s) exercised)" % tested if not fails else "%d FAILED" % fails))
