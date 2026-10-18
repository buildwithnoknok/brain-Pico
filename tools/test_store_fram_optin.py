# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_store_fram_optin.py - DEV-101, tier S (host Python, no hardware).
#
# Proves the runtime Store only looks for an I2C FRAM at 0x50 when the maker
# opts in with settings.toml NOKNOK_FRAM = 1:
#   - unset, "0" or any other value -> nvm backend, the I2C bus is never touched
#   - "1" and no chip answers        -> nvm backend (probe ran, found nothing)
#   - "1" and a chip answers         -> fram backend
#
# Run from the repo root:   python -I tools/test_store_fram_optin.py
# (busio/board/microcontroller are stubbed)

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))
for name in ("busio", "board", "digitalio", "supervisor"):
    sys.modules.setdefault(name, types.ModuleType(name))
mc = types.ModuleType("microcontroller")
mc.nvm = bytearray(4096)
sys.modules["microcontroller"] = mc

import noknok   # noqa: E402

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-58s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails += 1


def run(setting, chip_present):
    """Fresh Store with NOKNOK_FRAM = setting (None = unset). Returns
    (backend, number of times the I2C bus was used)."""
    if setting is None:
        os.environ.pop("NOKNOK_FRAM", None)
    else:
        os.environ["NOKNOK_FRAM"] = setting
    st = noknok.Store()
    used = []

    def fake_with_bus(fn):                  # stands in for the real bus
        used.append(1)
        return fn(object())
    st._with_bus = fake_with_bus
    st._fram_present = staticmethod(lambda i2c: chip_present)
    st._fram_read = staticmethod(lambda i2c, addr, n: bytearray(n))   # blank chip
    st._load()
    return st._backend, len(used)


print("DEV-101: FRAM probe is opt-in")
for setting in (None, "0", "", "yes", "2"):
    backend, used = run(setting, chip_present=True)    # a chip IS there
    label = "unset" if setting is None else repr(setting)
    check("NOKNOK_FRAM %-6s + chip at 0x50 -> nvm, bus untouched" % label,
          backend == "nvm" and used == 0, "(%s, bus used %d x)" % (backend, used))

backend, used = run("1", chip_present=False)
check("NOKNOK_FRAM = 1, no chip -> nvm", backend == "nvm" and used == 1, backend)

backend, used = run("1", chip_present=True)
check("NOKNOK_FRAM = 1, chip present -> fram", backend == "fram", backend)

backend, used = run(" 1 ", chip_present=True)
check("NOKNOK_FRAM = ' 1 ' (stray spaces) -> fram", backend == "fram", backend)

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
