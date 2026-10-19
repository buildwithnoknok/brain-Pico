# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev123.py - DEV-123, tier M: the NoknokPicoHub driver on a real noknok
# PicoHub (MOD-0011, power-manager firmware 0.2.0+). Runs ON the Pico:
#   python3 pico.py run bench_dev123.py      # phase 1 ... ends with reset_pico()
#   (wait ~5 s for the port to come back)
#   python3 pico.py run bench_dev123.py      # phase 2: after the reboot
# Needs noknok.mpy 1.16+ and the PicoHub pins in settings.toml (bus A GP10/GP11).
# Phase 1: enumerate, status() plausibility, source_caps(), set_rail usb auto,
#          set_min_input_voltage(20) and the contract it gives, note addresses,
#          reset_pico(100) (the Pico reboots).
# Phase 2: hub + every module back at the SAME address, the 20 V minimum
#          survived the reboot; then the minimum and the scratch note are cleared.
# Scratch note: microcontroller.nvm[3900:4090] (outside the Store record).

import json
import time
import microcontroller
import noknok

NOTE = slice(3900, 4090)
fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-56s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails += 1


def read_note():
    raw = bytes(microcontroller.nvm[NOTE]).rstrip(b"\xff\x00")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def write_note(obj):
    b = json.dumps(obj).encode()
    buf = bytearray(b"\xff" * (NOTE.stop - NOTE.start))
    buf[:len(b)] = b
    microcontroller.nvm[NOTE] = buf


print("noknok.py", noknok.__version__)
cfg = noknok.pin_config()
print("pins:", {g: cfg.get(g) for g in ("i2c_a", "i2c_b", "usb")}, cfg.get("problems"))
c = noknok.Conductor()
c.enumerate()
hub = c.picohub
check("c.picohub found", hub is not None, repr(hub))
if hub is None:
    raise SystemExit("no PicoHub - stop")
addrs = {uid: m.address for uid, m in c._registry.items() if m is not None}
print("modules:", {uid: (type(m).__name__, hex(m.address), getattr(m, "i2c_bus", "?"))
                   for uid, m in c._registry.items() if m is not None})
note = read_note()

if not note:
    print("\n== phase 1")
    check("firmware version read", bool(hub.firmware_version), hub.firmware_version)
    s = hub.status()
    print("  status:", s)
    check("vin plausible (4.5-21 V)", 4.5 <= s["vin"] <= 21, s["vin"])
    check("5 V rail plausible (4.6-5.5 V)", 4.6 <= s["v5v"] <= 5.5, s["v5v"])
    check("temperature plausible (-10..90 C)", -10 <= s["temp_c"] <= 90, s["temp_c"])
    check("contract voltage is a PD/Type-C level",
          round(s["contract_v"]) in (5, 9, 15, 20), s["contract_v"])
    check("bus A and buck on", s["rails"]["bus_a"] and s["rails"]["buck"])
    caps = hub.source_caps()
    print("  source caps:", caps)
    t0 = time.monotonic()
    for _ in range(20):
        hub.status()
    check("20 status() reads, no error", True, "%.1f ms each" % ((time.monotonic() - t0) * 50))
    check("set_rail('usb','auto') applied", hub.set_rail("usb", "auto") == hub.APPLIED, hub.last_result)
    r = hub.set_min_input_voltage(20)
    has20 = any(round(v) == 20 for v, a in caps)
    print("  set_min_input_voltage(20) ->", r, hub.RESULT_TEXT.get(r))
    want_v = 20 if has20 else None
    v = None
    for _ in range(30):                     # up to ~6 s for the new contract
        time.sleep(0.2)
        v = hub.status()["contract_v"]
        if want_v is None or round(v) == want_v:
            break
    s = hub.status()
    if has20:
        check("charger offers 20 V -> 20 V contract", round(s["contract_v"]) == 20, s["contract_v"])
        check("  minimum shows as set", s["inputs"]["min_set"])
    else:
        check("charger lacks 20 V -> NO_POWER, min_not_met warning",
              r == hub.NO_POWER and "min_not_met" in s["warnings"], (r, s["warnings"]))
    write_note({"addrs": addrs, "hub": hub.address, "min20": True, "fails": fails})
    print("\nphase 1 done, %d failure(s). reset_pico(100) now - run this script again in ~5 s" % fails)
    r = hub.reset_pico(100)                 # the Pico is held in reset 50 ms from now
    print("  reset_pico ->", r)
    time.sleep(2)
    print("  STILL RUNNING - reset_pico did not reboot the brain")
else:
    print("\n== phase 2 (after reset_pico)")
    check("PicoHub at the same address", hub.address == note["hub"], (hex(hub.address), hex(note["hub"])))
    same = all(addrs.get(uid) == a for uid, a in note["addrs"].items())
    check("every module at the same address", same, (note["addrs"], addrs))
    s = hub.status()
    check("20 V minimum survived the reboot", s["inputs"]["min_set"], s["inputs"])
    print("  contract now: %.1f V / %.2f A, reset_cause %s" % (s["contract_v"], s["contract_a"], s["reset_cause"]))
    check("set_min_input_voltage(None) applied", hub.set_min_input_voltage(None) == hub.APPLIED)
    write_note({})
    microcontroller.nvm[NOTE] = bytearray(b"\xff" * (NOTE.stop - NOTE.start))
    print("\nphase 2 done: %d failure(s) here, %d in phase 1" % (fails, note.get("fails", 0)))
    c.deinit()
