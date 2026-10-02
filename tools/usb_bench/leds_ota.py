# SPDX-License-Identifier: MIT
# leds_ota.py - run ON THE PICO: OTA-flash /leds8_182.bin onto the attached noknok LEDs
# module through its USB bootloader (noknok_usb.UsbModuleFlasher), then read the version.
import time
import noknok_usb

IMG = "/leds8_182.bin"   # <- set to the app image you put on the Pico (pico.py put <file>)
data = open(IMG, "rb").read()
print("image", IMG, len(data), "bytes, crc32 %08x" % noknok_usb.crc32(data))

serial, kind, m = noknok_usb.discover()[0]
print("before:", serial, m.version())

def prog(done, total):
    if done == total or done % 1024 < noknok_usb.UsbModuleFlasher.CHUNK:
        print("  %d / %d" % (done, total))

t0 = time.monotonic()
dev = noknok_usb.UsbModuleFlasher().flash(data, serial=serial, progress=prog)
print("flash done in %.1f s, app re-enumerated: %s" % (time.monotonic() - t0, dev is not None))
time.sleep(1)
serial2, kind2, m2 = noknok_usb.discover()[0]
print("after:", serial2, m2.version())
