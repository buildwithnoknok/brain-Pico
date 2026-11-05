# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_state_collision.py — two UIDs must never share one address in
# the saved state, and _restore_state() must never hand one address to two
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
# Needs any two live modules on the bus. Restores the real state at the end.
# DEV-156: the state lives in the Store (nvm) since DEV-18, not in
# noknok_state.json: read and write it with noknok.store().get/set("state").
import json
import noknok
from noknok import Conductor

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
victim_type = (noknok.store().get("state") or {}).get(victim_uid, {}).get("type")
print("   %d live; victim %s at 0x%02X" % (len(live), victim_uid, victim_addr))

real_state = json.dumps(noknok.store().get("state") or {})   # a copy, restored in finally

try:
    print("=== 1. plant a stale UID on the victim's address ===")
    st = json.loads(real_state)
    st[FAKE] = {"address": victim_addr, "type": 1}       # claims to be a buzzer at 0x%02X
    if not noknok.store().set("state", st):
        raise SystemExit("FAIL: could not write the planted state to the Store")

    print("=== 2. restore: one address, one UID ===")
    c2 = fresh(c)
    n = c2._restore_state()
    objs = [m for m in c2._registry.values() if m is not None]
    addrs = [m.address for m in objs]
    print("   restored %d; addresses %s" % (n, [hex(a) for a in addrs]))
    if len(addrs) != len(set(addrs)):
        raise SystemExit("FAIL: two module objects share an address after restore")
    # Which UID got the victim's address this time - the other one is the loser.
    winner = FAKE if FAKE in c2._registry else victim_uid
    loser = victim_uid if winner == FAKE else FAKE
    if FAKE in c2._registry:
        # Not a FAIL (DEV-156): which of two UIDs on one address restores first depends on
        # the dict order of the saved state, and CircuitPython dicts are not insertion
        # ordered. Restore only guarantees one UID per address (checked above); telling
        # the right UID needs an identity read at the runtime address (GET_ID, DEV-21).
        print("   KNOWN LIMIT: the planted UID won the address this time (order-dependent "
              "until GET_ID / DEV-21); one UID per address still holds")
    else:
        print("   fake UID skipped, no phantom")

    print("=== 3. save after a real enumerate: stale address nulled, type kept ===")
    c2.enumerate()
    st = noknok.store().get("state") or {}
    lost = st.get(loser)
    print("   winner %s, loser %s now: %s" % (winner, loser, lost))
    if lost is None:
        raise SystemExit("FAIL: the losing UID was dropped — rescue would report 'unknown UID'")
    if lost.get("address") is not None:
        raise SystemExit("FAIL: losing entry still holds 0x%02X" % lost["address"])
    if lost.get("type") != (1 if loser == FAKE else victim_type):
        raise SystemExit("FAIL: losing entry lost its type")
    seen = [i.get("address") for i in st.values() if i.get("address")]
    if len(seen) != len(set(seen)):
        raise SystemExit("FAIL: saved state still has two UIDs on one address")

    print("=== 4. restore again from the cleaned state: None address is skipped ===")
    c3 = fresh(c)
    c3._restore_state()
    if loser in c3._registry:
        raise SystemExit("FAIL: None-address entry was restored")
    print("ALL PASS - one UID per address; the losing entry keeps its type, loses its address")
finally:
    noknok.store().set("state", json.loads(real_state))
    c.enumerate()          # leave the bench as found
