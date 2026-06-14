# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# ledbutton_color_test.py — test ONE OR MANY noknok LED Buttons at once.
#
#   * Press a button              -> THAT button steps to its next colour
#   * Type a number 1-9 in Thonny -> ALL buttons jump to that colour (0 = all off)
#       1=yellow 2=red 3=blue 4=green 5=magenta 6=cyan 7=white 8=orange 9=purple
#
# Each button cycles its own colour independently; the number keys drive them all
# together. Handy as a quick "is this module alive?" check (e.g. after reworking a
# board) and to compare LED brightness between units.
#
# Setup: copy noknok.py + this file to CIRCUITPY, connect any number of LED
# Buttons, then in Thonny run:  import ledbutton_color_test    (Ctrl-C stops.)

import time
import sys
import supervisor
from noknok import Conductor

POLL_S = 0.02     # how often we check every button + the keyboard

# Named colours (R, G, B), full brightness — it's a test.
COLORS = {
    "off":     (0,   0,   0),
    "yellow":  (255, 255, 0),
    "red":     (255, 0,   0),
    "blue":    (0,   0,   255),
    "green":   (0,   255, 0),
    "magenta": (255, 0,   255),
    "cyan":    (0,   255, 255),
    "white":   (255, 255, 255),
    "orange":  (255, 80,  0),
    "purple":  (128, 0,   255),
}

# Number key -> colour name.
KEY_COLORS = {
    "1": "yellow", "2": "red",    "3": "blue",   "4": "green",  "5": "magenta",
    "6": "cyan",   "7": "white",  "8": "orange", "9": "purple", "0": "off",
}

# Colours a button press cycles through (no "off").
CYCLE = ["yellow", "red", "blue", "green", "magenta", "cyan", "white", "orange", "purple"]


def read_key():
    """Non-blocking read of one char from the Thonny serial console, or None."""
    if supervisor.runtime.serial_bytes_available:
        return sys.stdin.read(1)
    return None


def main():
    c = Conductor()
    c.enumerate()
    buttons = c.ledbutton
    if not buttons:
        print("No LED Buttons found. Check they're connected to the I2C port and powered.")
        return

    print("Found %d LED Button(s):" % len(buttons))
    for i, b in enumerate(buttons):
        print("  [%d] 0x%02X  UID %s" % (i, b.address, getattr(b, "_uid_hex", "?")))
    print("Press a button = its next colour | keys 1-9 = ALL to colour, 0 = all off")
    print("Ctrl-C to stop.\n")

    # Per-button state: a starting colour index (staggered so you can tell the
    # buttons apart at a glance) + the last-seen pressed level for edge detection.
    state = []
    for i, b in enumerate(buttons):
        idx = i % len(CYCLE)
        b.set_color(*COLORS[CYCLE[idx]])
        state.append({"idx": idx, "was": False})

    while True:
        # 1) keyboard -> ALL buttons to one colour
        key = read_key()
        if key in KEY_COLORS:
            name = KEY_COLORS[key]
            for b in buttons:
                b.set_color(*COLORS[name])
            if name in CYCLE:                       # keep each button's cycle in sync
                for st in state:
                    st["idx"] = CYCLE.index(name)
            print("key %s -> all %s" % (key, name))

        # 2) each button press -> that button's next colour (rising edge)
        for i, b in enumerate(buttons):
            s = b.read()
            pressed = bool(s and s.pressed)
            if pressed and not state[i]["was"]:
                state[i]["idx"] = (state[i]["idx"] + 1) % len(CYCLE)
                name = CYCLE[state[i]["idx"]]
                b.set_color(*COLORS[name])
                print("button [%d] 0x%02X -> %s" % (i, b.address, name))
            state[i]["was"] = pressed

        time.sleep(POLL_S)


main()
