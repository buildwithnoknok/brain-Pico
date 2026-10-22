# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev79_kick.py - runs ON the Pico (pico.py run bench_dev79_kick.py 200).
# DEV-79 question: does a join started with a short timeout (noknok_rpc LINK_KICK_S)
# finish in the background after connect() has returned? If yes, noknok_rpc can
# set LINK_FULL_EVERY = 0 (no 5 s freezes while a brain is offline).
# Per round: radio off/on (link gone), connect(timeout=KICK) -> catch, then watch
# wifi.radio.connected for WATCH s. Uses the brain's own /data/wifi.json, never
# prints the password. Ends with "RESULT background=N/ROUNDS". pico.py reset afterwards.
import gc, json, time, wifi

KICK, WATCH, ROUNDS = 0.5, 20, 5
with open("/data/wifi.json") as f:
    c = json.load(f)
ssid, pw = c["ssid"], c.get("password") or ""
ok = 0
for r in range(1, ROUNDS + 1):
    wifi.radio.enabled = False
    time.sleep(2)
    wifi.radio.enabled = True
    time.sleep(1)
    gc.collect()
    t0 = time.monotonic()
    try:
        wifi.radio.connect(ssid, pw, timeout=KICK)
        how = "inside connect()"
    except Exception as e:
        how = "connect() raised %r" % (e,)
    blocked = time.monotonic() - t0
    up_at = None
    while time.monotonic() - t0 < WATCH:
        if wifi.radio.connected and wifi.radio.ipv4_address is not None:
            up_at = time.monotonic() - t0
            break
        time.sleep(0.1)
    if up_at is not None and blocked < 2:
        ok += 1
    print("round %d: blocked %.2f s (%s), link up %s" % (
        r, blocked, how, "after %.1f s" % up_at if up_at is not None else "NEVER in %d s" % WATCH))
print("RESULT background=%d/%d" % (ok, ROUNDS))
