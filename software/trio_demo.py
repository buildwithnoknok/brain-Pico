# trio_demo.py — noknok Light & Sound Controller
#
# Uses all three modules and all five inputs:
#
#   Knob CW          → step note UP   (buzzer preview blip, LED tracks pitch)
#   Knob CCW         → step note DOWN (buzzer preview blip, LED tracks pitch)
#   Knob press       → sustain current note for 1 second
#   LED btn short    → manually cycle LED colour (5 colours)
#   LED btn long     → play Nokia ringtone on buzzer
#
# Run from Thonny. Ctrl-C to stop.

from noknok import Conductor
import time

# ── Setup ─────────────────────────────────────────────────────────────────────
c = Conductor()
c.enumerate()

if not c.buzzer:
    raise SystemExit("No buzzer found — check wiring.")
if not c.knob:
    raise SystemExit("No knob found — check wiring.")
if not c.ledbutton:
    raise SystemExit("No LED button found — check wiring.")

buz = c.buzzer[0]
knb = c.knob[0]
kbd = c.ledbutton[0]

print("\n noknok trio demo — Light & Sound Controller")
print(" ─────────────────────────────────────────────")
print("  Knob CW / CCW  →  pitch up / down  (LED tracks note)")
print("  Knob press     →  sustain note 1 s")
print("  LED btn short  →  cycle LED colour")
print("  LED btn long   →  play Nokia tune")
print()

# ── Pentatonic scale (10 notes, C4 → A5) ─────────────────────────────────────
NOTES = [262, 294, 330, 392, 440, 523, 587, 659, 784, 880]
NAMES = ["C4", "D4", "E4", "G4", "A4", "C5", "D5", "E5", "G5", "A5"]

# LED colour per note: cool blue (low) → warm red/purple (high) — RGB only
NOTE_COLORS = [
    (  0,   0, 180),  # C4  blue
    (  0,  80, 180),  # D4  blue-cyan
    (  0, 160, 120),  # E4  cyan
    (  0, 180,   0),  # G4  green
    ( 80, 180,   0),  # A4  yellow-green
    (180, 140,   0),  # C5  yellow
    (220,  60,   0),  # D5  orange
    (220,   0,   0),  # E5  red
    (200,   0,  80),  # G5  red-pink
    (160,   0, 180),  # A5  purple
]

# Manual colour cycle for LED button short press — RGB only
MANUAL_COLORS = [
    (180,   0,   0),  # red
    (  0, 180,   0),  # green
    (  0,   0, 180),  # blue
    (200, 160,  60),  # warm white (RGB approximation)
    (120,   0, 160),  # purple
]
MANUAL_NAMES = ["red", "green", "blue", "warm white", "purple"]

LONG_PRESS_S  = 0.6   # seconds to trigger long press
PREVIEW_MS    = 100   # buzzer preview blip duration

# ── State ─────────────────────────────────────────────────────────────────────
note_idx       = 4       # start at A4
manual_color   = False   # True after a short press overrides note colour
manual_idx     = 0

knb_was_pressed  = False
kbd_was_pressed  = False
kbd_press_start  = None
kbd_long_fired   = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def apply_led():
    """Set LED to note colour, unless user has manually overridden it."""
    if manual_color:
        r, g, b = MANUAL_COLORS[manual_idx]
    else:
        r, g, b = NOTE_COLORS[note_idx]
    kbd.set_color(r, g, b)


def show_note():
    color_label = MANUAL_NAMES[manual_idx] if manual_color else "note colour"
    print(f"  ♩  {NAMES[note_idx]} ({NOTES[note_idx]} Hz)   LED: {color_label}")


# ── Initial state ─────────────────────────────────────────────────────────────
apply_led()
show_note()
buz.play(NOTES[note_idx], PREVIEW_MS, 40)

# ── Main loop ─────────────────────────────────────────────────────────────────
while True:
    now    = time.monotonic()
    ks     = knb.read()
    kbd_s  = kbd.read()

    if ks is None or kbd_s is None:
        time.sleep(0.03)
        continue

    # ── Knob rotation ─────────────────────────────────────────────────────────
    if ks.delta != 0:
        step     = 1 if ks.delta > 0 else -1
        note_idx = max(0, min(len(NOTES) - 1, note_idx + step))
        manual_color = False          # rotation re-links LED to note colour
        apply_led()
        show_note()
        buz.play(NOTES[note_idx], PREVIEW_MS, 40)

    # ── Knob button: sustain note ─────────────────────────────────────────────
    if ks.pressed and not knb_was_pressed:
        print(f"  Knob press → sustaining {NAMES[note_idx]} for 1 s")
        buz.play(NOTES[note_idx], 1000, 90)
    knb_was_pressed = ks.pressed

    # ── LED button: press start ────────────────────────────────────────────────
    if kbd_s.pressed and not kbd_was_pressed:
        kbd_press_start = now
        kbd_long_fired  = False

    # ── LED button: long press (fires while still held) ───────────────────────
    if (kbd_s.pressed
            and kbd_press_start is not None
            and not kbd_long_fired
            and (now - kbd_press_start) >= LONG_PRESS_S):
        kbd_long_fired = True
        kbd.set_color(255, 255, 255)   # flash white as feedback
        time.sleep(0.08)
        apply_led()
        print("  LED btn long → playing Nokia tune")
        buz.tune(buz.NOKIA)

    # ── LED button: short press on release ────────────────────────────────────
    if not kbd_s.pressed and kbd_was_pressed and not kbd_long_fired:
        manual_color = True
        manual_idx   = (manual_idx + 1) % len(MANUAL_COLORS)
        apply_led()
        print(f"  LED btn short → manual colour: {MANUAL_NAMES[manual_idx]}")

    if not kbd_s.pressed and kbd_was_pressed:
        kbd_press_start = None

    kbd_was_pressed = kbd_s.pressed

    time.sleep(0.03)