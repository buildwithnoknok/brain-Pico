# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_state_merge.py — DEV-31 hardening D: the Conductor must NOT forget a
# module that is parked in its bootloader. Enumerate while the LED Button is
# PARKED (only the buzzer answers), check the state file still knows the LED
# Button, then rescue it. Before the _save_state() merge fix, the enumerate
# wiped the file and the rescue said "unknown UID".
#
# Step 6 of module-I2C-bootloader/firmware/stage0/test/regress_all.sh.
# Precondition: the LED Button is parked at 0x7E (regress_all.sh does that) and
# keyboard_firmware.bin is on the Pico for the rescue.
# DEV-156: the saved module state lives in the Store (nvm) since DEV-18, not in
# noknok_state.json - read it with noknok.store().get("state").
import noknok
from noknok import Conductor

print("=== 1. enumerate with the LED Button parked ===")
c = Conductor(); c.enumerate()
print("   LED Buttons seen:", len(c.ledbutton), " buzzers:", len(c.buzzer))

print("=== 2. saved state (Store) after that enumerate ===")
st = noknok.store().get("state") or {}
for uid, info in st.items():
    a = info.get("address")
    print("   %s -> type %d @%s" % (uid, info["type"], ("0x%02X" % a) if a else "None (no address)"))
if not any(i["type"] == 3 for i in st.values()):
    raise SystemExit("FAIL: the parked LED Button was forgotten (merge not working)")
print("   LED Button entry SURVIVED the enumerate")

print("=== 3. rescue ===")
def get_image(entry):
    with open("keyboard_firmware.bin", "rb") as f: return f.read()
r = c.rescue_parked_module(get_image)
print("   ", r)
if not r or r["action"] != "reflashed":
    raise SystemExit("FAIL: rescue did not reflash")
c.enumerate()
print("   LED Buttons now:", [hex(m.address) for m in c.ledbutton])
print("ALL PASS - state survives a parked module; rescue works after it")
