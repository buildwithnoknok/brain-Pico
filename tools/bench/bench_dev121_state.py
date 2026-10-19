# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev121_state.py - READ-ONLY: what the MOD-0011 power manager says right now
# (contract, input, rails, interlocks, faults, warnings) plus the modules the brain sees.
# Used for the DEV-121/122 charger tests (5 V-only adapter, PD charger back, J2 devices).
import json
import noknok

c = noknok.Conductor()
c.enumerate()
ph = c.picohub
if ph is None:
    print("NO PICOHUB FOUND")
else:
    for attempt in range(2):
        try:
            s = ph.status()
            print("STATUS", json.dumps(s))
            break
        except Exception as e:
            print("status error", repr(e))
    try:
        print("CAPS", ph.source_caps())
    except Exception as e:
        print("caps error", repr(e))
for attr in ("buzzer", "knob", "ledbutton", "display"):
    for m in getattr(c, attr, []) or []:
        print("MODULE", attr, "0x%02X" % m.address, getattr(m, "i2c_bus", "?"))
print("USB modules:", [(s, k) for s, k, m in noknok.noknok_usb.discover()] if hasattr(noknok, "noknok_usb") else "n/a")
