# knob_test.py
# Run from Thonny REPL or as main.py to test the noknok Knob Module.
#
# What this does:
#   1. Enumerates all modules on the bus
#   2. Confirms a knob was found
#   3. Loops: prints position + delta + button state whenever something changes
#   4. Press the knob button to reset position to 0
#   5. Press Ctrl-C in Thonny to stop

from noknok import Conductor
import time

c = Conductor()
c.enumerate()

if not c.knob:
    print("No knob module found. Check wiring and power, then re-run.")
    raise SystemExit

knob = c.knob[0]
print(f"\nKnob ready at 0x{knob.address:02X}  UID: {knob._uid_hex}")
print("Turn the knob — position and delta will print on each change.")
print("Press the knob button to reset position to 0.")
print("Ctrl-C to stop.\n")

last_pressed = False

# First read establishes baseline (Conductor's restore ping may have cleared
# enc_delta in the firmware, so position and delta can be out of sync here).
baseline = knob.read()
print(f"Baseline position: {baseline.position}\n")

while True:
    s = knob.read()

    # Button state change — print independently of rotation
    if s.pressed and not last_pressed:
        knob.reset()
        print(f"  BTN  DOWN → position reset to 0")
    elif not s.pressed and last_pressed:
        print(f"  BTN  up")
    last_pressed = s.pressed

    # Print on every rotation
    if s.delta != 0:
        direction = "CW " if s.delta > 0 else "CCW"
        print(f"  {direction}  pos={s.position:>5}  delta={s.delta:>+3}  btn={'DOWN' if s.pressed else 'up  '}")

    time.sleep(0.03)   # ~33 Hz poll — fast enough to catch single detents
