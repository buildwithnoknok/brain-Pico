# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_registry_complete.py - DEV-98, tier S (host Python, no hardware).
#
# Every module type the brain knows must have an entry in the module registry
# (Ecosystem/software/modules.json). A missing entry is silent on a device: the
# brain logs a no-op and that module is never updated, cached or rescued. That
# is how the noknok Display and LEDs 16x went without OTA until DEV-98.
#
# Checks (offline, default):
#   - every manifest type key in noknok.py's firmware tables (I2C _FW_GROUPS,
#     USB _USB_FW_GROUPS) has a registry entry, and vice versa
#   - every I2C module type byte (TYPE_*) maps to a manifest key
#   - every USB type byte in noknok_usb._USB_TYPES has a firmware group
# With --online (needs internet, no GitHub login - exactly what a brain sees):
#   - each registry index loads anonymously, its "module" equals the registry
#     key, and the image at its "url" matches "size" and "crc32"
#
# Run from the repo root:
#   python -I tools/test_registry_complete.py                 # sibling ../Ecosystem checkout
#   python -I tools/test_registry_complete.py --online        # + fetch every index and image
#   python -I tools/test_registry_complete.py --registry PATH|URL

import json
import os
import sys
import types
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))
for name in ("busio", "board", "microcontroller", "digitalio", "supervisor"):
    sys.modules.setdefault(name, types.ModuleType(name))

import noknok        # noqa: E402
import noknok_usb    # noqa: E402

DEFAULT_REGISTRY = os.path.join(HERE, "..", "..", "Ecosystem", "software", "modules.json")
REGISTRY_URL = "https://raw.githubusercontent.com/buildwithnoknok/Ecosystem/main/software/modules.json"

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-60s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails += 1


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def load_registry(src):
    if src.startswith("http"):
        return json.loads(fetch(src))
    with open(src, encoding="utf-8") as fh:
        return json.load(fh)


args = sys.argv[1:]
online = "--online" in args
src = DEFAULT_REGISTRY
if "--registry" in args:
    src = args[args.index("--registry") + 1]
elif not os.path.exists(src):
    src = REGISTRY_URL
reg = load_registry(src)
reg_keys = set((reg.get("modules") or {}).keys())
print("registry:", src, "version", reg.get("registry_version"))

C = noknok.Conductor
# Types with a driver but deliberately no OTA yet (no bootloader on the module),
# each with the ticket that adds it: listed in the registry, not updated by the brain.
no_ota = dict(getattr(C, "_NO_OTA_YET", {}))      # type byte -> (manifest key, why)
no_ota_keys = {k for k, _ in no_ota.values()}
i2c_keys = {k for _, k in C._FW_GROUPS}
usb_keys = {k for _, k in C._USB_FW_GROUPS}
brain_keys = i2c_keys | usb_keys

print("\nBrain type tables vs registry")
for k in sorted(brain_keys):
    check("brain type %-14s has a registry entry" % k, k in reg_keys)
for k in sorted(reg_keys - brain_keys):
    check("registry entry %-12s is a type the brain knows" % k, k in no_ota_keys,
          "(no OTA yet: %s)" % [w for kk, w in no_ota.values() if kk == k][0] if k in no_ota_keys else "")
for tb, (k, why) in sorted(no_ota.items()):
    check("no-OTA type %-12s has a registry entry (for later)" % k, k in reg_keys, why)

# Every I2C type byte must name a manifest key, and that key a firmware group.
type_bytes = {v: n for n, v in vars(C).items()
              if n.startswith("TYPE_") and isinstance(v, int)}
check("found the I2C type table (Conductor.TYPE_*)", len(type_bytes) >= 4, len(type_bytes))
for tb, name in sorted(type_bytes.items()):
    if tb in no_ota:
        check("I2C 0x%02X %-14s -> no OTA yet, NOT in the OTA map" % (tb, name),
              tb not in C._TYPE_TO_MF_KEY, no_ota[tb][1])
        continue
    key = C._TYPE_TO_MF_KEY.get(tb)
    check("I2C 0x%02X %-14s -> manifest key" % (tb, name), key in i2c_keys, key or "missing")

# Every USB type byte must have a driver list the firmware groups walk.
usb_lists = {attr for attr, _ in C._USB_FW_GROUPS}
for tb, (cls, tname) in sorted(noknok_usb._USB_TYPES.items()):
    attr = {"noknokleds": "leds", "noknokleds16": "leds16"}.get(tname)
    check("USB 0x%02X %-14s -> firmware group" % (tb, tname), attr in usb_lists, attr or "unmapped")

if online:
    print("\nOnline: each index and image, anonymously")
    for k in sorted(reg_keys):
        url = reg["modules"][k].get("index")
        try:
            idx = json.loads(fetch(url))
        except Exception as e:
            check("%s index loads" % k, False, repr(e))
            continue
        check("%s index loads, module == key" % k, idx.get("module") == k, idx.get("module"))
        try:
            b = fetch(idx["url"])
            crc = "%08x" % (zlib.crc32(b) & 0xFFFFFFFF)
            check("%s %s image size + crc32" % (k, idx.get("version")),
                  len(b) == idx.get("size") and crc == idx.get("crc32"),
                  "%d B %s" % (len(b), crc))
        except Exception as e:
            check("%s image loads" % k, False, repr(e))

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
