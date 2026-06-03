# keyboard_test.py
# On each button press: LED changes colour, buzzer plays one note higher.
# Cycles back to the start after the last note.

from noknok import Conductor
import time

# ── Scale and colours (one entry per step) ───────────────────────────────────

NOTES = [262, 294, 330, 349, 392, 440, 494, 523]   # C4 → C5

COLORS = [              # (R, G, B)
    (64,  0,   0),      # red
    (64,  32,  0),      # orange
    (64,  64,  0),      # yellow
    (0,   64,  0),      # green
    (0,   64,  64),     # cyan
    (0,   0,   64),     # blue
    (32,  0,   64),     # purple
    (64,  64,  64),     # white
]

# ── Setup ─────────────────────────────────────────────────────────────────────

print("Starting up...")
c = Conductor()
c.enumerate()

if not c.ledbutton:
    print("No LED button module found. Check wiring.")
    raise SystemExit

if not c.buzzer:
    print("No buzzer module found. Check wiring.")
    raise SystemExit

kb  = c.ledbutton[0]
buz = c.buzzer[0]

print(f"LED button at 0x{kb.address:02X}, Buzzer at 0x{buz.address:02X}")
print("Press the button!")

# ── Main loop ─────────────────────────────────────────────────────────────────

step = 0

while True:
    s = kb.read()

    if s is not None and s.press_event:
        freq  = NOTES[step % len(NOTES)]
        color = COLORS[step % len(COLORS)]

        kb.set_color(*color)
        buz.play(freq, 150)

        print(f"  Step {step + 1}  note={freq} Hz  color=RGB{color}")
        step += 1

    time.sleep(0.02)   # 20 ms poll — fast enough to catch any press