# bench_dev46.py - DEV-46 acceptance on the Pico: LEDs 8x + LEDs 16x on the
# same USB hub, plus whatever I2C modules are attached (e.g. the Display).
#
#   ./pico.py run bench_dev46.py 120
#
# Checks: enumeration dispatches each USB module to the right driver (8x ->
# c.leds, 16x -> c.leds16), the 8x still works as before, the 16x's white
# channel + status() work through the Pico, a 300-frame burst survives, and
# the firmware report / role tables know usb_leds_16x.
# Leaves a known frame ON (look at the bench):
#   LEDs 8x  : all blue
#   LEDs 16x : outer ring red/green alternating, inner 4 white
#   Display  : "DEV-46 OK"
# Ends with a display status read (bench rule until DEV-44 is fixed).

import time
from noknok import Conductor
import noknok, noknok_usb

ok = True

def check(name, cond, info=""):
    global ok
    ok = ok and bool(cond)
    print("  %s  %s %s" % ("ok  " if cond else "FAIL", name, info))

print("noknok", noknok.__version__, "/ noknok_usb", noknok_usb.__version__)
c = Conductor()
t0 = time.monotonic()
n = c.enumerate_all()
print("enumerate_all: %d modules in %.1f s" % (n, time.monotonic() - t0))
print("  buzzer %d knob %d ledbutton %d display %d | leds(8x) %d leds16 %d"
      % (len(c.buzzer), len(c.knob), len(c.ledbutton), len(c.display),
         len(c.leds), len(c.leds16)))

print("\n[1] dispatch")
check("one 8x in c.leds", len(c.leds) == 1)
check("one 16x in c.leds16", len(c.leds16) == 1)
check("8x is NoknokLEDs", c.leds and type(c.leds[0]) is noknok_usb.NoknokLEDs)
check("16x is NoknokLEDs16", c.leds16 and type(c.leds16[0]) is noknok_usb.NoknokLEDs16)
for m in c.leds + c.leds16:
    print("    %-13s serial %s fw %s" % (type(m).__name__, m._uid_hex, m.firmware_version))

if c.leds:
    print("\n[2] 8x unchanged")
    e = c.leds[0]
    check("8x identify (0x04)", e.identify())
    e.set_brightness(60)
    e.set_all(255, 0, 0); time.sleep(0.5)
    e.set_all_pixels([(0, 255, 0)] * 8); time.sleep(0.5)
    e.set_pixel(0, 255, 255, 255); time.sleep(0.5)
    check("8x has no status() (not a 16x)", not hasattr(e, "status"))

if c.leds16:
    print("\n[3] 16x through the Pico")
    L = c.leds16[0]
    check("16x identify (0x06)", L.identify())
    s = L.status()
    check("status() answered", s is not None)
    if s:
        for k in sorted(s):
            print("        %-12s %s" % (k, s[k]))
        check("temp 15-60 C", 15 <= s["temp_c"] <= 60, s["temp_c"])
        check("VBUS 4.5-5.5 V", 4500 <= s["vbus_mv"] <= 5500, s["vbus_mv"])
        check("budget > 0", s["budget_ma"] > 0, s["budget_ma"])
    L.set_brightness(60)
    L.white(255); time.sleep(0.7)
    L.set_all(255, 0, 0); time.sleep(0.7)
    L.set_all_pixels([(0, 0, 255)] * 12 + [(0, 0, 0, 255)] * 4); time.sleep(0.7)
    L.set_led("all", 0, 0, 0, brightness=60, duration_ms=600, w=200)
    time.sleep(1.0)
    s = L.status()
    check("rail off after timed set_led", s and not s["led_power"])

    print("\n[4] 300-frame burst via the Pico")
    t0 = time.monotonic()
    for i in range(300):
        L.set_all_pixels([((i * 7 + j * 16) & 0xFF, (i * 3) & 0xFF, j * 15,
                           (i + j) & 0x3F) for j in range(16)])
    print("    %.2f s" % (time.monotonic() - t0))
    L.set_all_pixels([(255, 0, 0) if j % 2 == 0 else (0, 255, 0) for j in range(12)]
                     + [(0, 0, 0, 255)] * 4)
    time.sleep(0.3)
    s = L.status()
    check("status() after burst", s is not None)
    check("LED rail on (final frame)", s and s["led_power"])
    check("temperature()", L.temperature() is not None, L.temperature())

print("\n[5] firmware report + roles")
rep = c.firmware_report({})
types = sorted(r["type"] for r in rep if r["bus"] == "usb")
check("USB report has usb_leds + usb_leds_16x", types == ["usb_leds", "usb_leds_16x"], types)
check("role candidates usb_leds_16x", len(c.role_candidates("usb_leds_16x")) == len(c.leds16))
check("role mode output", c.role_select_mode("usb_leds_16x") == "output")

if c.leds:
    c.leds[0].set_all(0, 0, 255)            # final frame 8x: all blue

if c.display:
    print("\n[6] display")
    d = c.display[0]
    try:
        print("    info:", d.info())
        d.clear(0)
        d.text("DEV-46 OK", size=16)
        st = d.status()
        print("    status:", st)
        check("display answered", st is not None)
    except Exception as ex:
        check("display", False, repr(ex))

print("\nRESULT:", "PASS (look at the bench)" if ok else "FAIL")
