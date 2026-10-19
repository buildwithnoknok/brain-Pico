# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev98_downgrade16.py - DEV-98: put an OLDER LEDs 16x app (/leds16_202.bin, v2.0.2) on the
# attached 16x through its USB bootloader (noknok_usb.UsbModuleFlasher, the same code the OTA pass
# uses) and make the daily OTA check due (nvm[1:5] = 0). Then hard-reset the brain (a USB host wedges
# after a module re-enumerates, DEV-45) and let it boot online: the OTA pass must update the 16x.
import time
import microcontroller
import noknok_usb

IMG = "/leds16_202.bin"
data = open(IMG, "rb").read()
print("image", IMG, len(data), "bytes, crc32 %08x" % noknok_usb.crc32(data))
serial, kind, m = noknok_usb.discover()[0]
print("before:", serial, kind, m.version())
dev = noknok_usb.UsbModuleFlasher().flash(data, serial=serial)
print("flash done, app re-enumerated:", dev is not None)
time.sleep(1.5)
try:
    serial2, kind2, m2 = noknok_usb.discover()[0]
    print("after:", serial2, m2.version())
except Exception as e:
    print("after: discover failed (host wedged?)", repr(e))
microcontroller.nvm[1:5] = bytes(4)            # OTA check due
print("OTA check made due; hard reset follows")
time.sleep(0.5)
microcontroller.reset()
