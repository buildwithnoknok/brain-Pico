# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_factory_reset.py - bench-side factory reset of the brain's SETUP data: removes
# /data/wifi.json, /data/product.py and the Store copy of the credentials, so the next
# boot opens the noknok-setup hotspot. Keeps the firmware cache (/data/fw_*) and settings.toml.
# Use when the brain is not reachable on the LAN (the /rpc factory_reset op is the normal way).
import noknok as nk

KEY = "wifi"          # code.py WIFI_STORE_KEY
print("store wifi before:", bool(nk.store().get(KEY)))
nk.store().delete(KEY)
with nk.writable():
    print("removed:", nk.remove("/data/wifi.json", "/data/product.py"))
print("store wifi after:", bool(nk.store().get(KEY)))
