# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev133_rescue_setup.py - DEV-133, tier M, runs ON THE PICO (pico.py run).
# Sets up the USB RESCUE test and the downgrade for the update test in one go:
#   1. the on-device cache for usb_leds_16x becomes v2.2.0 (/leds16_220.bin, sidecar
#      version 2.2.0, size, crc32) - so the rescue pushes 2.2.0 = a downgrade
#   2. the Store learns serial -> usb_leds_16x (as enumerate_usb() does on a normal boot)
#   3. the OTA check is made NOT due (nvm[1:5] = now; needs an NTP-synced RTC), so the
#      next boot takes the cache-only rescue path and does not refresh the cache
#   4. /debug_log is created so log.txt records the next boots
#   5. the 16x is sent 0xB0 WITHOUT a pending record = a module stuck in its bootloader
#   6. hard reset
# Expected next boots: [USBFW] ... action=flashed (2.2.0), hard reset, [USBFW] ... verified.
# Needs noknok.mpy 1.18 + noknok_usb.mpy 1.2 + code.py 0.27 on the Pico.
import json
import os
import time
import microcontroller
import noknok as nk
import noknok_usb
from module_flasher import crc32

IMG = "/leds16_220.bin"
image = open(IMG, "rb").read()
meta = {"version": "2.2.0", "layout": None, "size": len(image), "crc32": "%08x" % crc32(image)}
with nk.writable():
    nk.write_atomic(nk.DATA_DIR + "/fw_usb_leds_16x.bin", image)
    nk.write_atomic(nk.DATA_DIR + "/fw_usb_leds_16x.json", json.dumps(meta))
    open("/debug_log", "w").close()
print("cache now", meta)

found = noknok_usb.discover()
leds16 = [(s, m) for s, t, m in found if t == "noknokleds16"]
assert leds16, "no LEDs 16x running as an app on the USB host"
serial, m = leds16[0]
print("16x", serial, "running", m.version())
nk.store().set("usb", {serial: {"type": "usb_leds_16x"}})
print("store usb =", nk.store().get("usb"))

if time.localtime().tm_year < 2026:
    raise SystemExit("RTC not synced - cannot make the OTA check 'not due'; boot online first")
microcontroller.nvm[1:5] = int(time.time()).to_bytes(4, "little")
print("OTA check marked done now:", int(time.time()))
for f in ("/log.txt", "/noknok_events.txt"):
    try:
        print("size", f, os.stat(f)[6])
    except OSError:
        print("size", f, 0)

print("0xB0 sent:", noknok_usb.UsbModuleFlasher().request_bootloader(serial))
time.sleep(1.0)
print("hard reset")
microcontroller.reset()
