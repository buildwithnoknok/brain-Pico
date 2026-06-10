# noknok Buzzer Module — Test Script
# CircuitPython on Raspberry Pi Pico
# Run this in Thonny. Watch the console and listen to the buzzer.
#
# Wiring:
#   Pico GP8  →  Buzzer SDA  (JST SH pin 3)
#   Pico GP9  →  Buzzer SCL  (JST SH pin 4)
#   Pico 3V3  →  Buzzer 3V3  (JST SH pin 2)
#   Pico GND  →  Buzzer GND  (JST SH pin 1)

import busio
import board
import time

# ── I2C setup ────────────────────────────────────────────────────────────────
BUZZER_ADDR = 0x45
i2c = busio.I2C(scl=board.GP9, sda=board.GP8, frequency=100_000)

# ── Helper functions ──────────────────────────────────────────────────────────

def send(data):
    """Send a list of bytes to the buzzer over I2C."""
    while not i2c.try_lock():
        pass
    try:
        i2c.writeto(BUZZER_ADDR, bytes(data))
    finally:
        i2c.unlock()

def read_status():
    """Read 1 status byte. Returns 1 = playing, 0 = idle."""
    buf = bytearray(1)
    while not i2c.try_lock():
        pass
    try:
        i2c.readfrom_into(BUZZER_ADDR, buf)
    finally:
        i2c.unlock()
    return buf[0]

def stop():
    send([0x00])

def play_note(freq_hz, duration_sec, volume=100):
    """
    Play a single note.
      freq_hz      — frequency in Hz (e.g. 440 for concert A)
      duration_sec — how long in seconds (0 = play forever)
      volume       — 0 to 100
    """
    freq_hi = (freq_hz >> 8) & 0xFF
    freq_lo =  freq_hz       & 0xFF
    dur     = int(duration_sec * 10)   # convert seconds → 100 ms units
    vol     = max(0, min(100, volume))
    send([0x01, freq_hi, freq_lo, dur, vol])

def play_tune(tune_id):
    """
    Play a preloaded tune by ID:
      1 = Nokia Tune
      2 = Happy Birthday
      3 = Beep OK
      4 = Beep Error
      5 = Startup Chime
    """
    send([0x02, tune_id])

def wait_until_idle(timeout_sec=30):
    """Poll status until the buzzer is idle (or timeout)."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if read_status() == 0x00:
            return True
        time.sleep(0.05)
    print("  ⚠ Timeout waiting for idle")
    return False


# ═════════════════════════════════════════════════════════════════════════════
# TESTS
# Each test prints what it's doing, plays something, waits, then moves on.
# ═════════════════════════════════════════════════════════════════════════════

print()
print("noknok Buzzer — Test Script")
print("════════════════════════════")
print()
time.sleep(1)


# ── Test 1: Status read at boot ───────────────────────────────────────────────
print("Test 1 — Status read (expect: 0 = idle)")
status = read_status()
print(f"  Status: {status}  {'✓ idle' if status == 0 else '? unexpected'}")
print()
time.sleep(0.5)


# ── Test 2: Single note (concert A, 1 second, full volume) ───────────────────
print("Test 2 — Play A4 (440 Hz) for 1 second, full volume")
play_note(440, 1.0)
time.sleep(0.2)
print(f"  Status while playing: {read_status()}  (expect 1)")
time.sleep(1.2)
print(f"  Status after note:    {read_status()}  (expect 0)")
print()


# ── Test 3: Volume control ────────────────────────────────────────────────────
print("Test 3 — Volume: 100% → 50% → 25% → 10% → 5% → 1% (same note, stepping down)")
for vol in [100, 50, 25, 10, 5, 1]:
    print(f"  Volume {vol}% — 600 ms")
    play_note(880, 0.6, volume=vol)
    time.sleep(0.8)
print()


# ── Test 4: Interrupt — send new note while one is playing ───────────────────
print("Test 4 — Interrupt test")
print("  Starting 5-second note (G4)...")
play_note(392, 5.0)
time.sleep(1.0)
print("  Interrupting with A4 after 1 second...")
play_note(440, 0.5)
time.sleep(0.8)
print("  Interrupting again with C5...")
play_note(523, 0.5)
time.sleep(0.8)
print("  Stopping.")
stop()
print()


# ── Test 5: Predefined tunes ──────────────────────────────────────────────────
tune_names = {
    1: "Nokia Tune",
    2: "Happy Birthday",
    3: "Beep OK",
    4: "Beep Error",
    5: "Startup Chime",
}

print("Test 5 — Predefined tunes")
for tune_id, name in tune_names.items():
    print(f"  Tune {tune_id}: {name}")
    play_tune(tune_id)
    wait_until_idle(timeout_sec=15)
    time.sleep(0.5)   # brief pause between tunes
print()


# ── Test 6: STOP during a tune ────────────────────────────────────────────────
print("Test 6 — Stop mid-tune (Happy Birthday, stopped after 2 seconds)")
play_tune(2)
time.sleep(2.0)
stop()
time.sleep(0.3)
print(f"  Status after stop: {read_status()}  (expect 0)")
print()


# ── Done ──────────────────────────────────────────────────────────────────────
print("════════════════════════════")
print("All tests complete.")
print()
print("Quick reference — call these in the Thonny REPL:")
print("  play_note(440, 1.0)          # A4 for 1 second")
print("  play_note(440, 2.0, vol=50)  # A4 at half volume")
print("  play_tune(1)                 # Nokia Tune")
print("  play_tune(2)                 # Happy Birthday")
print("  play_tune(3)                 # Beep OK")
print("  play_tune(4)                 # Beep Error")
print("  play_tune(5)                 # Startup Chime")
print("  stop()                       # silence")
print("  read_status()                # 1=playing  0=idle")
