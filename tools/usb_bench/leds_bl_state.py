# SPDX-License-Identifier: MIT
# leds_bl_state.py - run ON THE PICO: is a noknok USB module present as APP (4E4E) or BOOTLOADER (4E42)?
import time, usb.core
import noknok_usb
noknok_usb.ensure_host_port()
for _ in range(20):
    a = usb.core.find(idVendor=0x1209, idProduct=0x4E4E)
    b = usb.core.find(idVendor=0x1209, idProduct=0x4E42)
    if a or b:
        break
    time.sleep(0.5)
print("app 4E4E:", a is not None, " bootloader 4E42:", b is not None)
