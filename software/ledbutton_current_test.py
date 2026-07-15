# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# ledbutton_current_test.py — brownout / current-draw bench test for LED Buttons.
#
# Turn ON a chosen NUMBER of LED Buttons at a chosen BRIGHTNESS (white), hold that
# load steady, and read the current on your USB power meter / multimeter. Change
# the numbers and measure again to find where the bus browns out.
#
# Symptom this is meant to chase: e.g. "7 buttons glow greenish, 3 are off" — a
# classic daisy-chain brownout, where modules far down the chain don't get enough
# voltage to run. The FIRST thing this script prints — how many buttons actually
# answered enumeration — is already a big clue: fewer than you plugged in = those
# modules aren't getting enough power to boot.
#
# Each round you enter two values (press Enter to keep the previous one):
#     LEDs ON   — how many of the found buttons to light (the rest are forced off)
#     Brightness — 0-255, applied to R=G=B (white = worst-case current per LED)
#
# The LEDs hold their setting while the prompt waits, so measure during the wait.
# Ctrl-C stops and turns everything off.
#
# Setup: copy noknok.py + this file to CIRCUITPY, connect the LED Buttons, then in
# Thonny run:  import ledbutton_current_test
#
# NOTE: lights only ONE colour channel set (white). To stress a single channel
# instead, change CHANNELS below.

from noknok import Conductor

# Which channels to drive. ("r","g","b") = white (max current). Use e.g. ("g",)
# to test just the green channel.
CHANNELS = ("r", "g", "b")

# Rough current per LED channel at full brightness (SK6812MINI-E), for a sanity
# estimate only — your meter is the real measurement.
MA_PER_CHANNEL_FULL = 15.0


def color_for(brightness):
    """Return (r, g, b) with `brightness` on the CHANNELS we're testing, else 0."""
    r = brightness if "r" in CHANNELS else 0
    g = brightness if "g" in CHANNELS else 0
    b = brightness if "b" in CHANNELS else 0
    return (r, g, b)


def ask_int(prompt, default, lo, hi):
    """Prompt for an int in [lo, hi]. Blank input keeps `default`. 'q' raises to quit."""
    while True:
        s = input("  %s [%d]: " % (prompt, default)).strip().lower()
        if s == "":
            return default
        if s == "q":
            raise KeyboardInterrupt
        try:
            v = int(s)
        except ValueError:
            print("    enter a whole number (or 'q' to quit)")
            continue
        if v < lo or v > hi:
            print("    must be %d-%d" % (lo, hi))
            continue
        return v


# ── Enumerate ─────────────────────────────────────────────────────────────────
c = Conductor()
c.enumerate()

buttons = c.ledbutton
n = len(buttons)

print("\n noknok LED Button — current / brownout test")
print(" ────────────────────────────────────────────")
print("  LED Buttons that answered enumeration: %d" % n)
if n == 0:
    raise SystemExit("  None found — check wiring/power. (Nothing to test.)")
print("  (If that's fewer than you connected, the missing ones aren't booting —")
print("   likely under-powered. That's already a strong brownout signal.)\n")
print("  Enter 'q' at any prompt (or Ctrl-C) to stop and turn all LEDs off.\n")

# ── Interactive load loop ─────────────────────────────────────────────────────
count = n         # start with all found buttons...
bright = 32       # ...at a gentle brightness; ramp up yourself to find the edge.

try:
    while True:
        count = ask_int("LEDs ON (0-%d)" % n, count, 0, n)
        bright = ask_int("Brightness (0-255)", bright, 0, 255)

        # Apply: first `count` buttons lit, the rest explicitly off.
        rgb = color_for(bright)
        lit = 0
        for i, btn in enumerate(buttons):
            if i < count:
                if btn.set_color(*rgb):     # returns False on I2C error
                    lit += 1
            else:
                btn.led_off()

        # Rough theoretical LED current (channels only; excludes MCU + Pico baseline).
        est_ma = count * len(CHANNELS) * (bright / 255.0) * MA_PER_CHANNEL_FULL

        print("  → %d LED(s) at brightness %d on channels %s"
              % (count, bright, "".join(CHANNELS)))
        if lit != count:
            print("  ⚠ only %d of %d accepted the command (%d didn't respond — "
                  "possible brownout/dropout!)" % (lit, count, count - lit))
        print("  ~%.0f mA rough LED estimate — read your meter now.\n" % est_ma)

except KeyboardInterrupt:
    for btn in buttons:
        btn.led_off()
    print("\n  Stopped — all LEDs off.")
