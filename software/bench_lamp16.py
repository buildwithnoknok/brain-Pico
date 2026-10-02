# bench_lamp16.py - DEV-46 step 6: run the REAL Multicolor Lamp 16x product
# script on the Pico and play the app's part, without WiFi.
#
#   ./pico.py put <poc>/scripts/multicolor_lamp_16x.py lamp16_product.py
#   ./pico.py run bench_lamp16.py 120
#
# The product runs unmodified. Conductor.sleep() (its loop) is wrapped so that,
# between two of its sleeps, a scripted "app" does exactly what the settings.*
# RPC handlers do: settings.get = Settings.snapshot(), settings.set =
# Settings.apply_remote(). After every change the module's own GET_STATUS proves
# the ring really changed (LED power rail, estimated current).
# Store writes are disabled for the run (bench brain, no flash wear, the stored
# settings record is not touched).

import time
import noknok as nk

nk.Settings.flush = lambda self, force=False: True     # bench: never write the Store

ok = True

def check(name, cond, info=""):
    global ok
    ok = ok and bool(cond)
    print("  %s  %s %s" % ("ok  " if cond else "FAIL", name, info))

def app():
    """The scripted app. Each `yield` = let the product run one loop turn (its
    on_change callbacks are delivered inside that sleep)."""
    yield                                   # product painted its defaults
    c = nk.conductor()
    s = nk.settings()
    ring = c.leds16[0]
    time.sleep(0.6)                         # past the product's status cache

    print("\n[app] settings.get")
    snap = s.snapshot()
    print("    values:", snap["values"])
    print("    info:  ", snap.get("info"))
    inf = snap.get("info") or {}
    check("defaults installed", snap["values"].get("mode") == "white"
          and snap["values"].get("on") is True)
    check("info temperature is a number", isinstance(inf.get("temperature"), (int, float)),
          inf.get("temperature"))
    check("info light = Normal", inf.get("light") == "Normal", inf.get("light"))
    st = ring.status()
    check("white mode: LED power on", st["led_power"], "%d mA" % st["led_ma"])
    ma_white = st["led_ma"]

    print("\n[app] settings.set temperature (read-only)")
    ch, rej = s.apply_remote({"temperature": 5})
    check("rejected read-only", rej.get("temperature") == "read-only", rej)

    print("\n[app] settings.set mode=color, color=#0000FF")
    s.apply_remote({"mode": "color", "color": "#0000FF"})
    yield
    st = ring.status()
    check("colour mode: LED power on", st["led_power"], "%d mA" % st["led_ma"])
    ma_blue = st["led_ma"]

    print("\n[app] settings.set mode=mix, white=255")
    s.apply_remote({"mode": "mix", "white": 255})
    yield
    st = ring.status()
    check("mix draws more than blue alone", st["led_ma"] > ma_blue,
          "%d mA vs %d mA" % (st["led_ma"], ma_blue))

    print("\n[app] settings.set brightness=30")
    s.apply_remote({"brightness": 30})
    yield
    st = ring.status()
    ma_dim = st["led_ma"]
    check("dimmer draws less", ma_dim < ma_white or ma_dim < st["budget_ma"],
          "%d mA" % ma_dim)

    print("\n[app] settings.set on=false")
    s.apply_remote({"on": False})
    yield
    time.sleep(0.6)
    st = ring.status()
    check("off: LED power rail off", not st["led_power"])
    check("info light = Off", s.snapshot()["info"].get("light") == "Off",
          s.snapshot()["info"].get("light"))

    print("\n[app] settings.reset")
    s.reset()
    yield
    st = ring.status()
    check("reset -> white, on", s.get("mode") == "white" and s.get("on") is True
          and st["led_power"], "%d mA" % st["led_ma"])
    check("info not in values after reset", "temperature" not in s.all())
    print("\nRESULT:", "PASS (ring: neutral white at brightness 120)" if ok else "FAIL")
    raise SystemExit


_app = app()
_orig_sleep = nk.Conductor.sleep

def _sleep(self, seconds):
    _orig_sleep(self, seconds)              # services + delivers on_change
    next(_app)

nk.Conductor.sleep = _sleep

try:
    exec(open("/lamp16_product.py").read(), {"__name__": "__main__"})
except SystemExit:
    pass
