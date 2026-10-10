#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# wifi_offline.py IN OUT - make a brain deliberately offline for a test (e.g. DEV-7: knob reset
# in safe idle only stays reachable offline - online, safe idle re-fetches the published product).
#
# Reads a wifi.json fetched from the brain (`pico.py get /data/wifi.json IN`), writes OUT with the
# SSID replaced by a network that does not exist and the password emptied, product fields kept.
# Prints key NAMES and non-secret fields only - never the password.
# Then `pico.py put OUT /data/wifi.json`. Delete IN afterwards: it holds a real WiFi password.
import json, sys

src, dst = sys.argv[1], sys.argv[2]
d = json.load(open(src))
print("keys:", sorted(d.keys()))
d["ssid"] = "bench-offline-does-not-exist"
if "password" in d:
    d["password"] = ""
json.dump(d, open(dst, "w"))
print("written %s: ssid=%s script_url=%s product_id=%s"
      % (dst, d["ssid"], d.get("script_url"), d.get("product_id")))
