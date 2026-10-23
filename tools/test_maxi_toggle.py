# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_maxi_toggle.py - DEV-151, tier S (host Python, no hardware).
#
# Runs the REAL Smart Lamp Maxi product script against a fake Conductor (one
# LEDs 16x, one LED Button, no knob/buzzer/display) and a fake clock, presses
# the button on/off/on/off quickly and checks what reaches the LEDs:
#   - each toggle sends the lamp state at once (as before);
#   - LAMP_SETTLE_S after the LAST toggle, exactly one more full frame with the
#     same colour + brightness (on) or one more off (off) - never a different
#     colour, so nothing flickers;
#   - quick toggles do not each add a re-send: only the last one counts;
#   - the 10 s refresh still runs while the lamp is on.
#
# Run from the brain-Pico repo root (poc checked out next to it):
#   python -I tools/test_maxi_toggle.py ../poc/scripts/smart_lamp_maxi.py

import os
import sys
import types

script = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "poc", "scripts", "smart_lamp_maxi.py")


class Clock:
    t = 1000.0


class Stop(Exception):
    pass


class Btn:
    """Scripted LED Button: press/release events at given fake times."""
    def __init__(self, events):
        self.events = sorted(events)       # [(t, "press"|"release")]
        self.pressed = False

    def read(self):
        r = types.SimpleNamespace(press_event=False, release_event=False, pressed=self.pressed)
        while self.events and self.events[0][0] <= Clock.t:
            _, what = self.events.pop(0)
            if what == "press":
                self.pressed, r.press_event = True, True
            else:
                self.pressed, r.release_event = False, True
            r.pressed = self.pressed
        return r

    def set_color(self, *a): pass
    def led_off(self): pass


class Leds16:
    """Records every command with the fake time."""
    def __init__(self):
        self.log = []
        self.white = True                  # NoknokLEDs16 has a white channel

    def set_brightness(self, v): self.log.append((Clock.t, "bri", v))
    def set_all(self, *c): self.log.append((Clock.t, "all", c))
    def off(self): self.log.append((Clock.t, "off", None))
    def play_preset(self, *a): self.log.append((Clock.t, "preset", a))


class Settings:
    def __init__(self):
        self.d = {}
    def defaults(self, d):
        for k, v in d.items():
            self.d.setdefault(k, v)
    def get(self, k, default=None): return self.d.get(k, default)
    def set(self, k, v): self.d[k] = v
    def all(self): return dict(self.d)
    def info(self, *a): pass
    def on_change(self, fn): pass


RUN_UNTIL = [0.0]


class Conductor:
    def __init__(self):
        self.ledbutton = [BUTTON]
        self.knob, self.buzzer, self.display = [], [], []
        self.leds16, self.leds = [LAMP], []
        self.settings = SETTINGS
    def enumerate_all(self): pass
    def load_roles(self): pass
    def sleep(self, s):
        Clock.t += s
        if Clock.t >= RUN_UNTIL[0]:
            raise Stop()


fake_time = types.ModuleType("time")
fake_time.monotonic = lambda: Clock.t
fake_time.time = lambda: 0.0               # clock not set (offline): "--:--"
import time as _real_time
fake_time.localtime = _real_time.localtime
fake_time.mktime = _real_time.mktime
fake_time.sleep = lambda s: None
nk = types.ModuleType("noknok")
nk.Conductor = Conductor
for n in ("BLACK", "WHITE", "GREY", "DARK_GREY", "LANDSCAPE"):
    setattr(nk, n, 0)

src = open(script, encoding="utf-8").read()
fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-66s %s %s" % (name, "PASS" if cond else "FAIL", extra if not cond else ""))
    if not cond:
        fails += 1


def run(events, until):
    """Run the product from a fresh start with scripted button events."""
    global BUTTON, LAMP, SETTINGS
    Clock.t = 1000.0
    BUTTON, LAMP, SETTINGS = Btn([(1000.0 + t, w) for t, w in events]), Leds16(), Settings()
    RUN_UNTIL[0] = 1000.0 + until
    saved = sys.modules.get("time")
    sys.modules["time"], sys.modules["noknok"] = fake_time, nk
    ns = {"__name__": "__main__"}
    try:
        exec(compile(src, script, "exec"), ns)
    except Stop:
        pass
    finally:
        sys.modules["time"] = saved
    return ns, [(round(t - 1000.0, 2), k, v) for t, k, v in LAMP.log]


def tap(t):
    return [(t, "press"), (t + 0.05, "release")]


print("DEV-151: quick on/off/on/off on the Smart Lamp Maxi (%s)" % os.path.basename(script))
SETTLE = None

# 1. lamp starts on; four quick taps (off, on, off, on) 0.2 s apart; watch 3 s
ns, log = run(tap(1.0) + tap(1.2) + tap(1.4) + tap(1.6), until=3.0)
SETTLE = ns.get("LAMP_SETTLE_S")
check("LAMP_SETTLE_S exists and is short (<= 1 s)", SETTLE is not None and 0 < SETTLE <= 1.0, SETTLE)
SETTLE = SETTLE or 0.5
on_colour = ns["_rgbw"](LAMP, *ns["color_rgb"]())
last_tap = 1.65
after = [e for e in log if e[0] > last_tap + 0.05]
check("ends ON", ns["lamp_on"]() is True)
check("one re-send ~LAMP_SETTLE_S after the LAST toggle",
      any(k == "all" and abs(t - (last_tap + SETTLE)) < 0.1 for t, k, v in after), after)
check("  ...it is the set colour (no different colour -> no flicker)",
      all(v == on_colour for t, k, v in after if k == "all"), after)
check("  ...exactly one re-send, not one per quick toggle",
      len([e for e in after if e[1] == "all"]) == 1, after)
check("  ...and no 'off' after the last (on) toggle", not [e for e in after if e[1] == "off"], after)
taps = [e for e in log if 1.0 <= e[0] <= last_tap + 0.05]
check("each toggle still sends its state at once",
      len([e for e in taps if e[1] == "off"]) >= 2 and len([e for e in taps if e[1] == "all"]) >= 2, taps)

# 2. ends OFF: the settle re-send is an off, nothing lights up
ns, log = run(tap(1.0) + tap(1.2) + tap(1.4), until=3.0)
after = [e for e in log if e[0] > 1.45 + 0.05]
check("ends OFF", ns["lamp_on"]() is False)
check("  ...one 'off' re-send ~LAMP_SETTLE_S after the last toggle",
      [k for t, k, v in after] == ["off"] and abs(after[0][0] - (1.45 + SETTLE)) < 0.1, after)

# 3. single tap with nothing else: one extra frame, then quiet until the refresh
ns, log = run(tap(1.0), until=1.0 + 9.0)
after = [e for e in log if e[0] > 1.1]
check("single toggle off: exactly one off re-send, then quiet", [k for t, k, v in after] == ["off"], after)

# 4. the 10 s refresh still runs while on
ns, log = run([], until=25.0)
refresh = ns.get("LAMP_REFRESH_S", 10.0)
alls = [t for t, k, v in log if k == "all"]
check("no toggle: only the periodic refresh (every LAMP_REFRESH_S)",
      len(alls) >= 2 and all(abs((b - a) - refresh) < 0.2 for a, b in zip(alls[1:], alls[2:]) ), alls)

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
