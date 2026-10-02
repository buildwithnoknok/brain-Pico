# SPDX-License-Identifier: MIT
# usb_list.py - run ON THE PICO: list every USB device on the host port (VID/PID/serial).
import time, usb.core
import noknok_usb
noknok_usb.ensure_host_port()
time.sleep(4)
n = 0
for d in usb.core.find(find_all=True):
    n += 1
    try: s = d.serial_number
    except Exception: s = "?"
    try: p = d.product
    except Exception: p = "?"
    print("  %04x:%04x  %s  serial=%s" % (d.idVendor, d.idProduct, p, s))
print("devices:", n)
