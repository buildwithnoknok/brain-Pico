# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev130_scan_then_ap.py - DEV-130 bench check (run on the brain Pico with
# `pico.py run bench_dev130_scan_then_ap.py 120`). Writes nothing to the FS.
#
# code.py 0.25 scans for WiFi networks only with the hotspot DOWN, right before
# each start_ap() (a scan with the AP up can hang forever on CircuitPython
# 10.3.1 / Pico 2 W). This proves the new order works on the real radio, three
# times, including the path after a failed join (what run_ap_provisioning does
# before it restarts the hotspot):
#   scan (station) -> start_ap -> AP up + IP -> stop_ap -> failed join -> repeat
import time
import wifi

AP_SSID, AP_PASSWORD = "noknok-setup-test", None
ok = True


def scan():
    t = time.monotonic()
    names = set()
    for n in wifi.radio.start_scanning_networks():
        if n.ssid:
            names.add(n.ssid)
        if time.monotonic() - t > 10:
            break
    wifi.radio.stop_scanning_networks()
    return len(names), time.monotonic() - t


for cycle in range(1, 4):
    print("cycle", cycle)
    count, secs = scan()
    print("  scan (AP down): %d network(s) in %.1f s" % (count, secs))
    if count == 0:
        ok = False
    wifi.radio.start_ap(ssid=AP_SSID, password=AP_PASSWORD) if AP_PASSWORD else wifi.radio.start_ap(ssid=AP_SSID)
    time.sleep(1)
    up = wifi.radio.ap_active
    print("  start_ap: ap_active=%s ip=%s" % (up, wifi.radio.ipv4_address_ap))
    if not up:
        ok = False
    wifi.radio.stop_ap()
    print("  stop_ap: ap_active=%s" % wifi.radio.ap_active)
    try:
        wifi.radio.connect("noknok-no-such-network", "wrongpassword", timeout=8)
        print("  join: unexpectedly connected")
    except Exception as e:
        print("  join failed as intended: %r" % (e,))

print("RESULT", "PASS" if ok else "FAIL")
