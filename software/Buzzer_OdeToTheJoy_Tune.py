from noknok import setup, NoknokBuzzer

b = NoknokBuzzer(setup())

# ── Note frequencies (Hz) ─────────────────────────────────────────────────────
C4, D4, E4, F4, G4 = 262, 294, 330, 349, 392

# ── Note durations (ms) ───────────────────────────────────────────────────────
q  = 400   # quarter note
e  = 200   # eighth note
dq = 600   # dotted quarter
h  = 800   # half note

# ── Ode to Joy — full chorus ──────────────────────────────────────────────────
chorus = [
    # Line 1
    (E4,q),(E4,q),(F4,q),(G4,q),
    (G4,q),(F4,q),(E4,q),(D4,q),
    (C4,q),(C4,q),(D4,q),(E4,q),
    (E4,dq),(D4,e),(D4,h),
    # Line 2
    (E4,q),(E4,q),(F4,q),(G4,q),
    (G4,q),(F4,q),(E4,q),(D4,q),
    (C4,q),(C4,q),(D4,q),(E4,q),
    (D4,dq),(C4,e),(C4,h),
    # Bridge
    (D4,q),(D4,q),(E4,q),(C4,q),
    (D4,q),(E4,e),(F4,e),(E4,q),(C4,q),
    (D4,q),(E4,e),(F4,e),(E4,q),(D4,q),
    (C4,q),(D4,q),(G4,h),
    # Line 4
    (E4,q),(E4,q),(F4,q),(G4,q),
    (G4,q),(F4,q),(E4,q),(D4,q),
    (C4,q),(C4,q),(D4,q),(E4,q),
    (D4,dq),(C4,e),(C4,h),
]

print("Playing Ode to Joy — press Ctrl+C to stop.")
while True:
    for freq, ms in chorus:
        b.note(freq, ms)
