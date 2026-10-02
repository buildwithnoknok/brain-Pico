# bench_info_settings.py - DEV-46 step 4: read-only "info" settings on the Pico.
#
#   ./pico.py run bench_info_settings.py 90
#
# Registers the LEDs 16x temperature as an info field and checks what
# settings.get (= Settings.snapshot()) returns, that neither the app
# (apply_remote) nor the product (set) can write an info id, that info never
# bumps seq or marks the record dirty (= no flash write), and that a provider
# which raises shows as None instead of breaking the page.

import time
import noknok as nk
from noknok import Conductor

ok = True

def check(name, cond, info=""):
    global ok
    ok = ok and bool(cond)
    print("  %s  %s %s" % ("ok  " if cond else "FAIL", name, info))

print("noknok", nk.__version__)
c = Conductor()
c.enumerate_all()
check("16x present", len(c.leds16) == 1)
check("display found via settings.toml pins", len(c.display) == 1,
      "(I2C %d modules)" % (len(c.display) + len(c.buzzer) + len(c.knob) + len(c.ledbutton)))

# A throwaway instance under its own Store key: never registered in
# nk._settings_by_key, so it never flushes - the installed product's real
# settings record is not touched.
s = nk.Settings("bench", key="bench_info")
seq0, dirty0 = s.seq, s.snapshot()["dirty"]

calls = [0]
def temp():
    calls[0] += 1
    return c.leds16[0].temperature()

def broken():
    raise RuntimeError("module unplugged")

s.info("temperature", temp)
s.info("note", broken)
s.info("model", "LEDs 16x")

snap = s.snapshot()
print("    snapshot:", snap)
inf = snap.get("info") or {}
check("info present in settings.get", set(inf) == {"temperature", "note", "model"})
check("temperature is a number 15-60", isinstance(inf.get("temperature"), (int, float))
      and 15 <= inf["temperature"] <= 60, inf.get("temperature"))
check("raising provider -> None", inf.get("note") is None)
check("constant provider", inf.get("model") == "LEDs 16x")
check("info not in values", "temperature" not in snap["values"])
check("seq unchanged by info", s.seq == seq0)
check("dirty unchanged by info", snap["dirty"] == dirty0)

n = calls[0]
s.snapshot(); s.snapshot()
check("provider runs on every settings.get", calls[0] == n + 2)

changed, rejected = s.apply_remote({"temperature": 99, "brightness": 200})
check("app write to info rejected", rejected.get("temperature") == "read-only", rejected)
check("normal setting still accepted", changed == {"brightness": 200}, changed)
check("product set() on info rejected", s.set("temperature", 1) is False)
check("info still live after rejects", isinstance(s.info_values()["temperature"], (int, float)))

c.leds16[0].set_all(255, 255, 255, w=255)   # warm it up a little, then re-read
time.sleep(2)
print("    temp after 2 s full white:", s.info_values()["temperature"])
c.leds16[0].set_all_pixels([(255, 0, 0) if j % 2 == 0 else (0, 255, 0) for j in range(12)]
                           + [(0, 0, 0, 255)] * 4)

if c.display:
    print("    display status:", c.display[0].status())
print("\nRESULT:", "PASS" if ok else "FAIL")
