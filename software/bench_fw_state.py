# SPDX-License-Identifier: MIT
# bench_fw_state.py - after a module went missing: raw I2C scan (a module
# parked in the bootloader sits at 0x7E), the brain's per-module state
# (noknok_state.json: fw + stage-1 "bl") and the firmware/event logs.
# Run: ./pico.py run bench_fw_state.py ; then ./pico.py reset
import os, busio, board, json

i2c = busio.I2C(board.GP21, board.GP20)
while not i2c.try_lock():
    pass
print("I2C scan GP20/21:", [hex(a) for a in i2c.scan()])
i2c.unlock()
i2c.deinit()

def find(name):
    for d in ("/", "/data/"):
        if name in os.listdir(d):
            return d + name
    return None

p = find("noknok_state.json")
if p:
    with open(p) as f:
        st = json.load(f)
    print("--- %s" % p)
    for uid, v in (st.get("modules") or st).items():
        print("  %s %s" % (uid, v))
for name in ("noknok_events.txt", "log.txt"):
    p = find(name)
    if not p:
        print("--- %s: none" % name)
        continue
    with open(p) as f:
        lines = f.read().splitlines()
    sel = [l for l in lines if "[fw" in l or "FW" in l or "RESCUE" in l or "knob" in l.lower()]
    print("--- %s (%d lines, fw-related last 25)" % (p, len(lines)))
    for l in sel[-25:]:
        print("  " + l)
