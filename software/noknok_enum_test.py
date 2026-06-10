# noknok Enumeration Test
# CircuitPython on Raspberry Pi Pico — run in Thonny
#
# Connect all 4 buzzer modules to the Pico via I2C daisy-chain.
# This script discovers them, prints their UIDs and assigned addresses,
# then plays a different note on each one to confirm individual control.

from noknok import Conductor
import time

# ── Discover all modules ──────────────────────────────────────────────────────
c = Conductor()
found = c.enumerate()

if found == 0:
    print("No modules found. Check wiring.")
    raise SystemExit

print()

# ── Play a rising scale — one note per buzzer ─────────────────────────────────
notes = [262, 330, 392, 523]   # C4, E4, G4, C5
print(f"Playing one note per buzzer ({found} module(s)):")

for i, buzzer in enumerate(c.buzzer):
    freq = notes[i % len(notes)]
    print(f"  Buzzer {i} (0x{buzzer.address:02X}) → {freq} Hz")
    buzzer.play(freq, 600)
    time.sleep(0.7)

time.sleep(0.5)

# ── Play the same tune on all buzzers simultaneously ──────────────────────────
print()
print("Playing Beep OK on all buzzers simultaneously...")
for buzzer in c.buzzer:
    buzzer.tune(buzzer.BEEP_OK)

# Wait for all to finish
time.sleep(1.0)

# ── Play Nokia tune on buzzer 0 while others are silent ──────────────────────
if len(c.buzzer) > 0:
    print()
    print("Nokia Tune on buzzer[0]...")
    c.buzzer[0].tune(c.buzzer[0].NOKIA)
    c.buzzer[0].wait()

print()
print("All done.")
print()
print("Modules available in REPL:")
for i, b in enumerate(c.buzzer):
    print(f"  c.buzzer[{i}]  address=0x{b.address:02X}")
