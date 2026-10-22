# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev113_rogue_dhcp.py - runs ON the Pico (pico.py run bench_dev113_rogue_dhcp.py 120).
# DEV-113 question: after start_ap() + stop_ap() and a join to the home WiFi (no reset),
# does this Pico answer DHCP on the HOME network with 192.168.4.x? Set STOP_DHCP = True
# to test the fix (code.py 0.32 _stop_ap(): stop_dhcp_ap() after stop_ap()).
# While it prints "PROBE NOW", run on the Pi:
#   echo raspberry | sudo -S python3 tools/bench/dhcp_offers.py wlan0 8
# A "<-- noknok setup-hotspot DHCP (rogue)" line = cause confirmed. Uses /data/wifi.json,
# never prints the password. Ends with microcontroller.reset() (clean radio again).
import json, time, wifi, microcontroller

STOP_DHCP = False
HOLD_S = 60
with open("/data/wifi.json") as f:
    c = json.load(f)
print("CircuitPython wifi.radio has stop_dhcp_ap:", hasattr(wifi.radio, "stop_dhcp_ap"))
wifi.radio.start_ap(ssid="noknok-dev113", password="dev113test")
print("AP up:", wifi.radio.ap_active, wifi.radio.ipv4_address_ap)
time.sleep(3)
wifi.radio.stop_ap()
if STOP_DHCP:
    wifi.radio.stop_dhcp_ap()
print("AP stopped:", not wifi.radio.ap_active, " stop_dhcp_ap called:", STOP_DHCP)
wifi.radio.connect(c["ssid"], c.get("password") or "", timeout=15)
print("joined: ip %s gateway %s dns %s" % (wifi.radio.ipv4_address, wifi.radio.ipv4_gateway,
                                          wifi.radio.ipv4_dns))
print("PROBE NOW (%d s)" % HOLD_S)
time.sleep(HOLD_S)
print("done - hard reset")
microcontroller.reset()
