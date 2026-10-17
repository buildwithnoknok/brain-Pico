# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# verify_brain.py - runs ON the Pico (pico.py run). After a restore, answers "does this
# brain actually work?", not just "are the files there" (DEV-111):
#   * lists /lib with size + CRC (compare with software/lib in the repo)
#   * imports the modules code.py needs, so a missing .mpy or a library that does not fit
#     in RAM (MemoryError) is caught here
#   * compiles the installed product (product.py / data/product.py): a product that is too
#     big for the free RAM fails here instead of crash-looping into safe idle
#   * enumerates the I2C modules on the CONFIGURED pins (settings.toml) and compares them
#     with the module state the brain remembers (Store): modules it knew but cannot find
#     = wrong pins or a dead bus = FAILED
#   * reports the crash-strike counter (must be 0 after a restore)
# Prints "VERIFY OK" or "VERIFY FAILED". Read-only.
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


def exists(p):
    try:
        os.stat(p)
        return True
    except OSError:
        return False


print("lib bytes:", walk("/lib"))
for name in ("noknok", "noknok_rpc", "noknok_usb", "module_flasher"):
    try:
        m = __import__(name)
        print("  import %-15s ok %s" % (name, getattr(m, "__version__", "")))
    except Exception as e:
        bad += 1
        print("  import %-15s FAILED %r" % (name, e))
for name in ("code.py", "boot.py", "settings.toml"):
    if not exists("/" + name):
        bad += 1
        print("  missing /" + name)
for name in ("noknok.py", "noknok_rpc.py", "noknok_usb.py", "module_flasher.py"):
    if exists("/" + name[:-3] + ".mpy") and exists("/" + name):
        bad += 1
        print("  both /%s and /%s.mpy exist (the .py wins and may not fit in RAM)" % (name, name[:-3]))

# The installed product must at least compile in the RAM that is left after the imports.
for path in ("/data/product.py", "/product.py"):
    if exists(path):
        try:
            src = open(path, "r").read()
            compile(src, path, "exec")
            print("  product %-20s compiles ok (%d bytes)" % (path, len(src)))
        except MemoryError as e:
            bad += 1
            print("  product %s: MemoryError - too big for the free RAM (libraries as .mpy?) %r" % (path, e))
        except Exception as e:
            bad += 1
            print("  product %s: FAILED %r" % (path, e))
        break

# The I2C bus on the configured pins must show the modules the brain remembers.
try:
    import noknok
    c = noknok.Conductor()
    found = c.enumerate(total_timeout_sec=6)
    known = [u for u, i in c._state_map().items() if isinstance(i, dict) and i.get("address")]
    print("  I2C: %d module(s) answer on the configured pins; %d remembered in the Store" % (found, len(known)))
    if known and found == 0:
        bad += 1
        print("  I2C: modules are remembered but none answers: wrong pins in settings.toml, or the bus is dead")
    elif found < len(known):
        print("  I2C: WARNING %d remembered module(s) did not answer (unplugged?)" % (len(known) - found))
except Exception as e:
    bad += 1
    print("  I2C check FAILED %r" % (e,))

try:
    import microcontroller
    b = microcontroller.nvm[0]
    strikes = (b & 0x0F) if (b & 0xF0) == 0xC0 else 0
    print("  crash strikes: %d" % strikes)
    if strikes:
        bad += 1
        print("  crash strikes are not 0: the brain would stay parked (run clear_strikes.py)")
except Exception as e:
    print("  crash strikes: unreadable %r" % (e,))

print("VERIFY FAILED" if bad else "VERIFY OK")
