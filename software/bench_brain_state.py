# SPDX-License-Identifier: MIT
# bench_brain_state.py - what is this brain set up as, is it online, what did
# it do last? Prints product + WiFi state (never the password) and the tail of
# the event log. Run: ./pico.py run bench_brain_state.py ; then ./pico.py reset
import json, os, wifi

def tail(path, n=12):
    try:
        with open(path) as f:
            lines = f.read().splitlines()
        print("--- %s (last %d)" % (path, n))
        for l in lines[-n:]:
            print("  " + l)
    except OSError as e:
        print("--- %s: %s" % (path, e))

for p in ("/data/wifi.json", "/wifi.json"):
    try:
        with open(p) as f:
            c = json.load(f)
        print("creds %s: ssid=%r product=%r script=%r" % (
            p, c.get("ssid"), c.get("product_id"),
            (c.get("script_url") or "").rsplit("/", 1)[-1]))
    except (OSError, ValueError):
        print("creds %s: none" % p)
print("wifi connected=%s ip=%s" % (wifi.radio.connected, wifi.radio.ipv4_address))
print("product.py present:", "product.py" in os.listdir("/") or "product.py" in os.listdir("/data"))
tail("/noknok_events.txt")
tail("/boot_out.txt", 6)
