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
# ── Firmware needed ──────────────────────────────────────────────────────────
#   Everything here works on display firmware v0.2.0 or later (v0.5.0 is
#   current). Icons, images, print and regions are rendered on the Pico and
#   sent over the existing 1bpp blit, so they need no firmware support at all.

import time
from noknok import Conductor, COLORS, rgb565, icon_names   # noqa: F401

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
  demo2           the DEV-41 showcase: print, icons, image, regions
  status          show the current text settings

The easy way (DEV-41):
  p <text>        d.print(): next line each time, wraps, scrolls when full
  icon <name> [x y] [size]     draw a built-in icon, e.g.  icon wifi 60 2
  icons           list the icon names
  image <path> [x y] [w]       draw a 1-bit .bmp from the Pico's flash
  region <name> <x> <y> <w> <h> [size]   define a named box
  set <name> text <words...>   |  set <name> icon <iconname>   |  set <name>
                  update / wipe a named box (only that box is redrawn)
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

        if cmd == "demo2":
            run_demo2(d)
            continue

        # ── DEV-41: print / icons / images / regions ─────────────────────────
        if cmd == "p":
            try:
                y = d.print(line[2:])
                print("  printed. Next line at y=%d." % y)
            except Exception as e:
                print("  Could not print that: %s" % e)
            continue

        if cmd == "icons":
            print("  " + ", ".join(icon_names()))
            continue

        if cmd == "icon":
            try:
                name = args[0]
                x = int(args[1]) if len(args) > 1 else state["x"]
                y = int(args[2]) if len(args) > 2 else state["y"]
                size = int(args[3]) if len(args) > 3 else None
                state["y"] = d.icon(name, x=x, y=y, size=size,
                                    color=state["color"], bg=state["bg"])
                print("  icon drawn. Next line at y=%d." % state["y"])
            except (IndexError, ValueError) as e:
                print("  Usage: icon <name> [x y] [size]   e.g.  icon wifi 60 2")
                print("  %s" % e)
            continue

        if cmd == "image":
            try:
                path = args[0]
                x = int(args[1]) if len(args) > 1 else 0
                y = int(args[2]) if len(args) > 2 else 0
                w = int(args[3]) if len(args) > 3 else None
                state["y"] = d.image(path, x=x, y=y, w=w,
                                     color=state["color"], bg=state["bg"])
                print("  image drawn. Next line at y=%d." % state["y"])
            except (IndexError, ValueError, OSError) as e:
                print("  Usage: image </path/file.bmp> [x y] [w]   (1-bit .bmp)")
                print("  %s" % e)
            continue

        if cmd == "region":
            try:
                name = args[0]
                x, y, w, h = [int(v) for v in args[1:5]]
                size = int(args[5]) if len(args) > 5 else 16
                d.region(name, x, y, w, h, size=size, color=state["color"])
                print("  region %r = %d,%d %dx%d. Now: set %s text ..." % (name, x, y, w, h, name))
            except (IndexError, ValueError) as e:
                print("  Usage: region <name> <x> <y> <w> <h> [size]   e.g.  region temp 0 40 80 32 32")
                print("  %s" % e)
            continue

        if cmd == "set":
            try:
                name = args[0]
                kind = args[1] if len(args) > 1 else None
                if kind == "text":
                    d.set(name, text=" ".join(args[2:]))
                elif kind == "icon":
                    d.set(name, icon=args[2])
                elif kind == "image":
                    d.set(name, image=args[2])
                elif kind is None:
                    d.set(name)
                else:
                    raise ValueError("second word must be text, icon or image")
                print("  region %r updated." % name)
            except (IndexError, ValueError, OSError) as e:
                print("  Usage: set <name> text <words>  |  set <name> icon <iconname>  |  set <name>")
                print("  %s" % e)
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


def run_demo2(d):
    """DEV-41 showcase: print() as a terminal, icons, an image, live regions."""
    print("  Running the DEV-41 demo — watch the screen...")
    d.clear(COLORS["black"])

    # 1. as simple as print()
    d.print("Hello World")
    d.print("Temp:", 22.5, "C")
    d.print("Big", size=24, color=COLORS["yellow"])
    time.sleep(1.5)

    # 2. every icon, 16 px, in a grid
    d.clear(COLORS["black"])
    names = icon_names()
    for i, name in enumerate(names):
        d.icon(name, x=4 + (i % 4) * 19, y=4 + (i // 4) * 19, color=COLORS["cyan"])
    d.text("icons", size=8, x=2, y=90, color=COLORS["grey"])
    time.sleep(1.5)

    # 3. one icon at several sizes (host-scaled, no firmware icon store)
    d.clear(COLORS["black"])
    x = 0
    for size in (16, 24, 32):
        d.icon("wifi", x=x, y=4, size=size, color=COLORS["noknok"])
        x += size + 4
    d.icon("heart", x=8, y=48, size=64, color=COLORS["red"])
    time.sleep(1.5)

    # 4. regions: a live value that never flickers
    d.clear(COLORS["black"])
    d.region("title", 0, 0, 80, 16, size=16, align="center")
    d.region("value", 0, 40, 80, 32, size=32, align="right", color=COLORS["yellow"])
    d.region("net", 62, 140, 18, 18)
    d.set("title", text="counter")
    d.set("net", icon="wifi")
    for n in range(0, 11):
        d.set("value", text=str(n * 10))
        time.sleep(0.15)
    d.set("net", icon="check", color=COLORS["green"])

    # 5. scrolling terminal
    time.sleep(1.0)
    d.clear(COLORS["black"])
    for i in range(1, 13):
        d.print("line", i)
        time.sleep(0.12)
    print("  Demo done.")


main()
