# SPDX-License-Identifier: MIT
# leds_ota_bl.py - run ON THE PICO: flash /leds8_182.bin to a module that is ALREADY in
# the noknok USB bootloader (PID 4E42), then wait for it to come back as the app.
import time
import noknok_usb
IMG = "/leds8_182.bin"   # <- set to the app image you put on the Pico (pico.py put <file>)
data = open(IMG, "rb").read()
print("image", len(data), "bytes, crc32 %08x" % noknok_usb.crc32(data))
t0 = time.monotonic()
dev = noknok_usb.UsbModuleFlasher().flash(data)
print("flash done in %.1f s, app re-enumerated on the Pico: %s" % (time.monotonic() - t0, dev is not None))
