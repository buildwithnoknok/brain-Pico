# noknok_roles_test.py
# Tests the role management system — run in Thonny after noknok_setup_roles.py
#
# What this tests:
#   1. Enumeration finds all modules
#   2. load_roles() reads noknok_roles.json correctly
#   3. c.role["name"] points to the right physical module
#   4. Commanding a module by role actually works
#   5. Reboot stability — roles survive power cycle (re-run to verify)

from noknok import Conductor, NoknokBuzzer, NoknokLedButton
import time

ROLES_FILE = "noknok_roles.json"

# ── Step 1: Enumerate ─────────────────────────────────────────────────────────
print()
print("Step 1 — Enumerate")
print("──────────────────")
c = Conductor()
found = c.enumerate()

if found == 0:
    print("No modules found. Check wiring.")
    raise SystemExit

print()

# ── Step 2: Load roles ────────────────────────────────────────────────────────
print("Step 2 — Load roles")
print("───────────────────")
ok = c.load_roles(ROLES_FILE)

if not ok:
    print()
    print("Run noknok_setup_roles.py first to create the roles file.")
    raise SystemExit

print()

# ── Step 3: Show role map ─────────────────────────────────────────────────────
print("Step 3 — Role map")
print("─────────────────")
for role_name, module in c.role.items():
    if module is None:
        print(f"  '{role_name}' → NOT FOUND")
    else:
        print(f"  '{role_name}' → {type(module).__name__}  "
              f"addr=0x{module.address:02X}  uid={module._uid_hex}")

print()

# ── Step 4: Cross-check — role vs discovery index ────────────────────────────
print("Step 4 — Discovery order")
print("────────────────────────")
if c.buzzer:
    for i, b in enumerate(c.buzzer):
        print(f"  buzzer[{i}]     addr=0x{b.address:02X}  uid={b._uid_hex}")
if c.knob:
    for i, k in enumerate(c.knob):
        print(f"  knob[{i}]       addr=0x{k.address:02X}  uid={k._uid_hex}")
if c.ledbutton:
    for i, k in enumerate(c.ledbutton):
        print(f"  ledbutton[{i}]  addr=0x{k.address:02X}  uid={k._uid_hex}")

print()

# ── Step 5: Command each role ─────────────────────────────────────────────────
print("Step 5 — Command each role individually")
print("────────────────────────────────────────")
for role_name, module in c.role.items():
    if module is None:
        print(f"  '{role_name}' → skipped (not found)")
        continue

    if isinstance(module, NoknokBuzzer):
        print(f"  '{role_name}' (noknokbuzzer) → playing Beep OK...")
        module.tune(module.BEEP_OK)
        time.sleep(0.6)
        print(f"    is_playing() = {module.is_playing()}  (expect False)")

    elif isinstance(module, NoknokLedButton):
        print(f"  '{role_name}' (noknokledbutton) → cycling LED colours...")
        module.set_color(64, 0, 0)   # red
        time.sleep(0.4)
        module.set_color(0, 64, 0)   # green
        time.sleep(0.4)
        module.set_color(0, 0, 64)   # blue
        time.sleep(0.4)
        module.led_off()
        print(f"    LED off.")

        print(f"    Reading button state...")
        s = module.read()
        if s is not None:
            print(f"    pressed={s.pressed}  press_event={s.press_event}  "
                  f"release_event={s.release_event}  count={s.count}")

    time.sleep(0.3)

print()

# ── Step 6: Button press test (LED button modules only) ──────────────────────
ledbuttons = [(name, m) for name, m in c.role.items() if isinstance(m, NoknokLedButton)]

if ledbuttons:
    print("Step 6 — Button press test")
    print("──────────────────────────")
    print("Press any LED button within 5 seconds...")
    deadline = time.monotonic() + 5.0
    detected = False
    while time.monotonic() < deadline:
        for role_name, module in ledbuttons:
            s = module.read()
            if s is not None and s.press_event:
                module.set_color(0, 80, 0)   # green flash on press
                print(f"  '{role_name}' pressed!  count={s.count}")
                time.sleep(0.2)
                module.led_off()
                detected = True
        time.sleep(0.02)
    if not detected:
        print("  No press detected.")
    print()

# ── Step 7: Reboot reminder ───────────────────────────────────────────────────
print("Step 7 — Reboot test")
print("────────────────────")
print("To verify roles survive a power cycle:")
print("  1. Unplug and replug the Pico")
print("  2. Re-run this script")
print("  3. Check that the same role names map to the same UIDs")
print()
print("The UID column in Step 3 should be identical after reboot.")
print("The discovery index (Step 4) may differ — that is expected and OK.")
print()
print("All tests complete.")