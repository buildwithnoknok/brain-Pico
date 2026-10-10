# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# verify_brain.py - runs ON the Pico (pico.py run). After a restore: lists the
# libraries with size + CRC (compare with software/lib in the repo), imports the
# modules code.py needs, and prints the library version. Prints "VERIFY OK" or
# "VERIFY FAILED". Read-only.
import os
from module_flasher import crc32

bad = 0


def walk(d):
    total = 0
    for n in sorted(os.listdir(d)):
        p = d + "/" + n
        if os.stat(p)[0] & 0x4000:
            total += walk(p)
        else:
            data = open(p, "rb").read()
            print("  %-52s %7d crc %08x" % (p, len(data), crc32(data)))
            total += len(data)
    return total


print("lib bytes:", walk("/lib"))
for name in ("noknok", "noknok_rpc", "noknok_usb", "module_flasher"):
    try:
        m = __import__(name)
        print("  import %-15s ok %s" % (name, getattr(m, "__version__", "")))
    except Exception as e:
        bad += 1
        print("  import %-15s FAILED %r" % (name, e))
for name in ("code.py", "boot.py", "settings.toml"):
    try:
        os.stat("/" + name)
    except OSError:
        bad += 1
        print("  missing /" + name)
print("VERIFY FAILED" if bad else "VERIFY OK")
