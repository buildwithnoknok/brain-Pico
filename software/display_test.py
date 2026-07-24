# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# display_test.py — interactive bench test for the noknok Display Module.
# CircuitPython on a Raspberry Pi Pico. Run it from the Thonny REPL:
#
#     >>> import display_test
#
# Then just type text and it appears on the screen. Type "help" for the
# short list of commands, "q" to quit.
#
# ── Wiring (JST SH 4-pin) ────────────────────────────────────────────────────
#   Pico GP8  → SDA        Pico 3V3 → 3V3
#   Pico GP9  → SCL        Pico GND → GND
#
#   NOTE: the display module has NO I2C pull-up resistors (noknok standard is
#   host-side pull-ups only) and the PicoHub isn't built yet — so chain a
#   buzzer / knob / LED button onto the bus to borrow its pull-ups, otherwise
#   the bus won't come up at all.
#
# ── What works today vs. what needs Sam's stage-2 firmware ───────────────────
#   Works on display firmware v0.1.0:  clear, rect, backlight, on/off/sleep,
#                                      info, version
#   Needs stage-2 firmware (text/blit): text, size, color, bg, demo
#   The text commands will simply report an error until that firmware is on the
#   module — nothing here will crash or hang.

import time
from noknok import Conductor, COLORS, rgb565     # noqa: F401  (rgb565 handy in REPL)

BANNER = """
========================================
  noknok Display — interactive test
========================================
"""

HELP = """
Just TYPE ANYTHING and press Enter -> it is drawn on the display.

Commands:
  size <n>        text height in pixels, e.g.  size 24    (any number works)
  color <name>    text colour, e.g.  color yellow         (or: color 0xFF8800)
  bg <name>       background behind the text: a colour, "auto", or "none"
  clear [colour]  wipe the screen, e.g.  clear  /  clear blue
  at <x> <y>      where the next text starts (pixels from the top-left)
  rect <x> <y> <w> <h> <colour>    draw a filled rectangle
  bright <0-100>  backlight brightness in percent, e.g.  bright 60
  on / off / sleep                 panel power
  info            ask the module its size, colour depth, fonts, icons
  version         installed firmware version
  demo            a quick showcase of sizes and colours
  status          show the current text settings
  help            this list
  q               quit

Colour names: """ + ", ".join(sorted(COLORS.keys())) + """
(or any hex like 0xFF8800)
"""


def _parse_color(word, default=None):
    """Turn a typed word into something the driver accepts, or None if bad."""
    if word is None:
        return default
    w = word.strip().lower()
    if w in ("none", "-", "transparent"):
        return None
    if w == "auto":
        return "auto"
    if w in COLORS:
        return COLORS[w]
    try:
        return int(w, 16) if w.startswith("0x") else int(w)
    except ValueError:
        return "BAD"


def main():
    print(BANNER)
    print("Looking for modules on the I2C bus...")

    c = Conductor()
    c.enumerate()

    if not c.display:
        print("")
        print("No display module found.")
        print("Things to check:")
        print("  - is the JST SH cable the right way round?")
        print("  - is another I2C module (buzzer/knob) on the bus for pull-ups?")
        print("  - does the backlight come on at all when powered?")
        return

    d = c.display[0]
    print("Found the display at 0x%02X (firmware %s)"
          % (d.address, d.firmware_version or "unknown"))

    nfo = d.info()
    if nfo:
        print("Panel: %s" % nfo)
    else:
        print("Panel: the module didn't answer GET_INFO — using %dx%d as a guess."
              % (d.width, d.height))

    # Start from a known state.
    d.backlight(1.0)
    d.on()
    d.clear(COLORS["black"])

    # Current text settings — changed with the size/color/bg/at commands.
    state = {"size": 16, "color": COLORS["white"], "bg": "auto", "x": 0, "y": 0}

    print(HELP)

    while True:
        try:
            line = input("display> ")
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            return

        if line is None:
            continue
        line = line.strip()
        if not line:
            continue

        parts = line.split()
        cmd   = parts[0].lower()
        args  = parts[1:]

        # ── quit ─────────────────────────────────────────────────────────────
        if cmd in ("q", "quit", "exit"):
            print("Bye. The screen keeps whatever is on it right now.")
            return

        if cmd in ("help", "?"):
            print(HELP)
            continue

        # ── settings ─────────────────────────────────────────────────────────
        if cmd == "size":
            try:
                n = int(args[0])
                if n < 1 or n > 255:
                    raise ValueError
                state["size"] = n
                print("  text size is now %d pixels tall." % n)
            except (IndexError, ValueError):
                print("  Usage: size <number of pixels, 1-255>   e.g.  size 24")
            continue

        if cmd in ("color", "colour"):
            col = _parse_color(args[0] if args else None, "BAD")
            if col == "BAD" or col is None:
                print("  Usage: color <name or 0xRRGGBB>   e.g.  color yellow")
            else:
                state["color"] = col
                print("  text colour set.")
            continue

        if cmd == "bg":
            col = _parse_color(args[0] if args else None, "BAD")
            if col == "BAD":
                print("  Usage: bg <colour | auto | none>   e.g.  bg none")
            else:
                state["bg"] = col
                print("  background set to %s."
                      % ("transparent" if col is None else col))
            continue

        if cmd == "at":
            try:
                state["x"], state["y"] = int(args[0]), int(args[1])
                print("  next text starts at x=%d y=%d." % (state["x"], state["y"]))
            except (IndexError, ValueError):
                print("  Usage: at <x> <y>   e.g.  at 4 40")
            continue

        if cmd == "status":
            print("  size=%d  color=%s  bg=%s  at=(%d,%d)  panel=%dx%d"
                  % (state["size"], state["color"], state["bg"],
                     state["x"], state["y"], d.width, d.height))
            continue

        # ── screen commands ──────────────────────────────────────────────────
        if cmd == "clear":
            col = _parse_color(args[0] if args else None, COLORS["black"])
            if col == "BAD" or col is None:
                col = COLORS["black"]
            d.clear(col)
            state["x"], state["y"] = 0, 0
            print("  cleared.")
            continue

        if cmd == "rect":
            try:
                x, y, w, h = [int(v) for v in args[:4]]
                col = _parse_color(args[4] if len(args) > 4 else "white", "BAD")
                if col == "BAD" or col is None:
                    raise ValueError
                d.fill_rect(x, y, w, h, col)
                print("  rectangle drawn.")
            except (IndexError, ValueError):
                print("  Usage: rect <x> <y> <w> <h> <colour>   e.g.  rect 0 0 80 10 red")
            continue

        if cmd in ("bright", "brightness", "backlight"):
            try:
                pct = float(args[0])
                pct = 0.0 if pct < 0 else (100.0 if pct > 100 else pct)
                d.backlight(pct / 100.0)
                print("  backlight at %d%%." % int(pct))
            except (IndexError, ValueError):
                print("  Usage: bright <0-100>   e.g.  bright 60")
            continue

        if cmd == "on":
            d.on()
            print("  panel on.")
            continue

        if cmd == "off":
            d.off()
            print("  panel off.")
            continue

        if cmd == "sleep":
            d.sleep()
            print("  panel asleep. Type 'on' to wake it.")
            continue

        if cmd == "info":
            nfo = d.info(refresh=True)
            print("  %s" % (nfo if nfo else "the module didn't answer GET_INFO."))
            continue

        if cmd == "version":
            print("  firmware %s (protocol %s)"
                  % (d.firmware_version or "unknown", d.protocol_version))
            continue

        if cmd == "demo":
            run_demo(d)
            continue

        # ── anything else = text to draw ─────────────────────────────────────
        try:
            state["y"] = d.text(line,
                                size=state["size"],
                                color=state["color"],
                                bg=state["bg"],
                                x=state["x"],
                                y=state["y"])
            busy, err = d.status()
            if err:
                print("  drawn, but the module reported error code %d." % err)
            else:
                print("  drawn. Next line will start at y=%d "
                      "(use 'at' or 'clear' to reset)." % state["y"])
        except Exception as e:
            print("  Could not draw that: %s" % e)
            print("  (If this is a text/size problem, the module may still be on")
            print("   stage-1 firmware, which cannot draw text yet.)")


def run_demo(d):
    """A quick showcase: native sizes, an odd size, colours and a bar."""
    print("  Running the demo — watch the screen...")
    d.clear(COLORS["black"])
    time.sleep(0.2)

    d.fill_rect(0, 0, d.width, 12, COLORS["noknok"])
    d.text("noknok", size=8, x=2, y=2, color=COLORS["white"], bg=COLORS["noknok"])

    y = 16
    for size, color in ((16, "white"), (24, "yellow"), (11, "cyan")):
        y = d.text("Size %d" % size, size=size, x=2, y=y, color=COLORS[color])
        y += 2

    d.text("Any size!", size=13, x=2, y=y + 4, color=COLORS["lime"])
    print("  Demo done.")


main()
