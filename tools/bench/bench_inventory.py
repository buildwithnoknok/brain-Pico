# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_inventory.py - read-only "what is this brain and what is on it". Run: pico.py run.
# Library versions, WiFi setup (SSID only), then every I2C bus settings.toml configures (DEV-74; was GP8/9, GP20/21,
# GP18/19) and the USB host. Writes nothing. To learn bootloader versions/layouts use
# bench_dev65.py (it reads them with the flasher replaced by tripwires).
import board, os, wifi
import noknok, noknok_rpc
from noknok import Conductor

print("noknok", noknok.__version__, "| rpc", getattr(noknok_rpc, "__version__", "?"))
try:
    import noknok_usb
    print("noknok_usb", getattr(noknok_usb, "__version__", "?"))
except ImportError:
    print("noknok_usb missing")
print("settings.toml I2C:", os.getenv("NOKNOK_I2C_SDA"), os.getenv("NOKNOK_I2C_SCL"))
try:
    d = noknok.read_json(noknok.DATA_DIR + "/wifi.json")
    print("wifi.json: ssid=%r product_id=%r" % (d.get("ssid"), d.get("product_id")) if d else "wifi.json: none")
except Exception as e:
    print("wifi.json unreadable:", e)
print("radio connected:", wifi.radio.connected, wifi.radio.ipv4_address)
# The I2C buses settings.toml configures (DEV-74: no pins in code).
cfg = noknok.pin_config()
print("pins:", {g: cfg.get(g) for g in ("i2c_a", "i2c_b", "usb")}, "source:", cfg.get("source"))
for p in cfg.get("problems", ()):
    print("  PROBLEM:", p)
for name, sda, scl, label in noknok.i2c_bus_pins():
    print("== I2C bus", name, label)
    try:
        c = Conductor(sda=sda, scl=scl)
        c.enumerate(total_timeout_sec=8)
        for kind in ("buzzer", "knob", "ledbutton", "display"):
            for m in getattr(c, kind, []):
                print("   %-10s 0x%02X fw %s uid %s" % (kind, m.address, getattr(m, "firmware_version", None),
                                                       getattr(m, "_uid_hex", None)))
        if name == "A":
            try:
                c.enumerate_usb()
                for m in getattr(c, "leds", []) + getattr(c, "leds16", []):
                    print("   usb        %s fw %s" % (type(m).__name__, getattr(m, "firmware_version", None)))
            except Exception as e:
                print("   usb enumeration failed:", e)
        c.deinit()
    except Exception as e:
        print("  ", type(e).__name__, e)
