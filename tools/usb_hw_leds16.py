# usb_hw_leds16.py - run the REAL noknok_usb.NoknokLEDs16 driver against a real
# LEDs 16x plugged straight into a Pi/PC (DEV-46). No Pico involved.
#
# The driver talks CircuitPython usb.core (write/read on bulk endpoints). Here a
# tiny adapter sends the same bytes over the module's CDC serial port instead -
# the module sees identical bytes, so this proves the driver's protocol against
# the real firmware, from a FAST host (tools.md rule: stress-test USB modules
# from a fast host too, not only through the Pico's slow PIO-USB).
#
#   python3 usb_hw_leds16.py [/dev/serial/by-id/...noknok_LEDs...]
#   (noknok_usb.py must sit next to this file)
#
# Ends with a known frame ON: outer ring alternating red/green, the 4 inner
# LEDs white. Look at the ring - that frame is the stress-test verdict.

import glob
import sys
import time
import types

import serial

# -- stub the CircuitPython modules noknok_usb imports ----------------------
for name in ("usb", "usb.core", "usb_host", "board"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["usb"].core = sys.modules["usb.core"]
sys.modules["board"].GP16 = sys.modules["board"].GP17 = None
sys.modules["usb_host"].Port = lambda dp, dm: None

import noknok_usb as nu   # noqa: E402


class SerialDev:
    """usb.core-device look-alike over the CDC serial port."""
    def __init__(self, port):
        self.s = serial.Serial(port, 115200, timeout=0.3)
        self.s.reset_input_buffer()

    def write(self, ep, data, timeout=None):
        self.s.write(bytes(data))
        self.s.flush()

    def read(self, ep, buf, timeout=300):
        self.s.timeout = timeout / 1000
        got = self.s.read(len(buf))
        if not got:
            raise OSError("timeout")
        buf[:len(got)] = got
        return len(got)


port = sys.argv[1] if len(sys.argv) > 1 else \
    (glob.glob("/dev/serial/by-id/*noknok_LEDs*") or [None])[0]
if not port:
    sys.exit("no noknok LEDs serial port found")
print("port:", port)

ok = True
def check(name, cond, info=""):
    global ok
    ok &= bool(cond)
    print("  %s  %s %s" % ("ok  " if cond else "FAIL", name, info))

dev = SerialDev(port)
print("type byte:", nu._probe_type(dev))
L = nu.NoknokLEDs16(dev)
check("identify() -> 0x06", L.identify())
v = L.version()
check("version()", v and v[0] == 1, v)

s = L.status()
check("status() answered", s is not None)
if s:
    for k in sorted(s):
        print("        %-12s %s" % (k, s[k]))
    check("temp plausible 15-60 C", 15 <= s["temp_c"] <= 60, s["temp_c"])
    check("VBUS plausible 4.5-5.5 V", 4500 <= s["vbus_mv"] <= 5500, s["vbus_mv"])
    check("budget > 0", s["budget_ma"] > 0, s["budget_ma"])
    check("layout 1", s["layout"] == 1)

print("visual steps (1 s each)")
L.set_brightness(60)
L.white(255);                            print("  all white die");  time.sleep(1)
L.set_all(255, 0, 0);                    print("  all red (w=0)");  time.sleep(1)
L.set_all_pixels([(0, 0, 255)] * 12 + [(0, 0, 0, 255)] * 4)
print("  outer blue, inner white");      time.sleep(1)
L.set_pixel(0, 0, 255, 0, w=0);          print("  LED 0 green");    time.sleep(1)
L.set_led("all", 0, 0, 0, brightness=60, duration_ms=800, w=200)
print("  set_led white 800 ms then off"); time.sleep(1.2)
s = L.status()
check("rail off after set_led timeout", s and not s["led_power"],
      s and s["led_power"])

print("stress: 300 x set_all_pixels back-to-back")
t0 = time.monotonic()
for i in range(300):
    L.set_all_pixels([((i * 7 + j * 16) & 0xFF, (i * 3) & 0xFF, j * 15, (i + j) & 0x3F)
                      for j in range(16)])
final = [(255, 0, 0) if j % 2 == 0 else (0, 255, 0) for j in range(12)] + \
        [(0, 0, 0, 255)] * 4
L.set_all_pixels(final)
dt = time.monotonic() - t0
print("  %.2f s" % dt)
time.sleep(0.3)
s = L.status()
check("status() after burst", s is not None)
check("LED rail on (final frame shown)", s and s["led_power"])
print("  FINAL FRAME ON: outer ring alternating red/green, inner 4 white")
print("\nRESULT:", "PASS (check the ring!)" if ok else "FAIL")
