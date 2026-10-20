# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev133_read.py - DEV-133, runs ON THE PICO (pico.py run). Prints what the last
# boots did for the USB update / rescue: the tail of the Store event ring, the [usb]/[fw]/
# [USBFW]/[BOOT]/[RESCUE]/[CRASH] lines of log.txt (needs /debug_log), the Store "usb"
# record and nvm[0:6]. Read-only.
import microcontroller
import noknok as nk

KEYS = ("[usb]", "[USBFW]", "[FW]", "[fw]", "[BOOT]", "[boot]", "[RESCUE]", "[CRASH]",
        "[maxi]", "USB:", "PENDING", "bootloader")
print("== events (Store ring, last 25)")
for ln in (nk.store().get("events") or [])[-25:]:
    print(ln)
try:
    lines = open(nk.DATA_DIR + "/log.txt").read().splitlines()
    print("== log.txt, last 400 lines filtered")
    for ln in lines[-400:]:
        if any(k in ln for k in KEYS):
            print(ln)
except OSError:
    print("no log.txt")
print("== store usb", nk.store().get("usb"))
print("== nvm[0:6]", list(microcontroller.nvm[0:6]))
