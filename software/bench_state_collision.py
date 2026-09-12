# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_state_collision.py — two UIDs must never share one address in
# noknok_state.json, and _restore_state() must never hand one address to two
# module objects.
#
# The bug (bench, 12 Sep 2026): the buzzer died, only the LED Button
# re-enumerated and took 0x08, the merging _save_state() kept the dead buzzer's
# old 0x08 as well, and _restore_state() — a presence ping, not an identity
# check — "restored" the buzzer as a phantom pointing at the LED Button.
#
# Deterministic: plants a fake stale UID on a live module's address, then checks
# (a) restore takes one and skips the other, (b) the next save nulls the stale
# entry's address while keeping its type for the rescue path.
# Needs any two live modules on the bus. Restores the real state file at the end.
import json
from noknok import Conductor

STATE = "noknok_state.json"
FAKE  = "deadbeef00000000"          # a UID that no real module has


def fresh(c):
    """Forget everything the Conductor knows, as a new Conductor() would —
    without a second Conductor(), since busio.I2C owns the pins once."""
    c.buzzer, c.knob, c.ledbutton, c.display = [], [], [], []
    c._registry = {}
    return c


print("=== 0. learn the real bus ===")
c = Conductor(); c.enumerate()
live = {uid: m for uid, m in c._registry.items() if m is not None}
if len(live) < 1:
    raise SystemExit("FAIL: need at least one live module")
victim_uid, victim = next(iter(live.items()))
victim_addr = victim.address
print("   %d live; victim %s at 0x%02X" % (len(live), victim_uid, victim_addr))

with open(STATE) as f:
    real_state = f.read()

try:
    print("=== 1. plant a stale UID on the victim's address ===")
    st = json.loads(real_state)
    st[FAKE] = {"address": victim_addr, "type": 1}       # claims to be a buzzer at 0x%02X
    with open(STATE, "w") as f:
        json.dump(st, f)

    print("=== 2. restore: one address, one UID ===")
    c2 = fresh(c)
    n = c2._restore_state()
    objs = [m for m in c2._registry.values() if m is not None]
    addrs = [m.address for m in objs]
    print("   restored %d; addresses %s" % (n, [hex(a) for a in addrs]))
    if len(addrs) != len(set(addrs)):
        raise SystemExit("FAIL: two module objects share an address after restore")
    if FAKE in c2._registry:
        raise SystemExit("FAIL: the fake stale UID was restored as a phantom")
    print("   fake UID skipped, no phantom")

    print("=== 3. save after a real enumerate: stale address nulled, type kept ===")
    c2.enumerate()
    with open(STATE) as f:
        st = json.load(f)
    fake = st.get(FAKE)
    print("   fake entry now:", fake)
    if fake is None:
        raise SystemExit("FAIL: the stale UID was dropped — rescue would report 'unknown UID'")
    if fake.get("address") is not None:
        raise SystemExit("FAIL: stale entry still holds 0x%02X" % fake["address"])
    if fake.get("type") != 1:
        raise SystemExit("FAIL: stale entry lost its type")
    seen = [i.get("address") for i in st.values() if i.get("address")]
    if len(seen) != len(set(seen)):
        raise SystemExit("FAIL: state file still has two UIDs on one address")

    print("=== 4. restore again from the cleaned file: None address is skipped ===")
    c3 = fresh(c)
    c3._restore_state()
    if FAKE in c3._registry:
        raise SystemExit("FAIL: None-address entry was restored")
    print("ALL PASS - one UID per address; stale entries keep type, lose address")
finally:
    with open(STATE, "w") as f:
        f.write(real_state)
    c.enumerate()          # leave the bench as found
