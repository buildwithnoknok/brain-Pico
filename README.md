# brain-Pico

CircuitPython code for the **noknok product brain** — a Raspberry Pi Pico W (Pico 2 W / RP2350)
that provisions itself, downloads its product script, and controls the connected noknok modules
over I2C.

> Private repo. Open vs closed source for the provisioning layer is still **TBD** (see the
> architecture doc). For now everything lives here together.

## software/

| File | Role |
|------|------|
| `boot.py` | Runs first on boot. Remounts the filesystem writable so the firmware can save files. |
| `code.py` | Provisioning brain + launcher. WiFi-AP setup on first boot, then connect + download + run the product script crash-safely on every boot. |
| `noknok.py` | Conductor library — module discovery/enumeration + drivers (Buzzer, Knob, LED Button, ...). |
| `trio_demo.py` | Light & Sound Controller demo — uses all three I2C modules. |
| `noknok_setup_roles.py` | One-time role-assignment wizard. |
| `noknok_roles_test.py` | Roles smoke test. |
| `knob_test.py` | Knob module standalone test. |
| `keyboard_test.py` | LED button module standalone test. |

## Provisioning (PoC Step 1 — done)

First boot: Pico starts a WiFi AP `noknok-setup` and serves a captive-portal setup page.
The user enters their home WiFi; the Pico saves it, hard-resets into WiFi mode, downloads
the product script from GitHub over HTTPS, saves it as `product.py`, and runs it. Subsequent
boots reconnect to WiFi and re-run the product directly.

**Note:** BLE was the original plan but is not supported on RP2350 in CircuitPython 10.x —
provisioning uses WiFi AP instead. Full process, test steps, and gotchas are documented in
Confluence: *Software Development -> Pico W Provisioning — Process & Implementation*.

### Required CircuitPython libs (/lib)
- `adafruit_httpserver/`
- `adafruit_requests.mpy`
- `adafruit_connection_manager.mpy`

### Flash / test
1. Copy `boot.py` + `code.py` + the libs to the Pico (CIRCUITPY drive or Thonny).
2. **Power-cycle** the Pico (the radio is not reset by a soft reboot).
3. Join `noknok-setup`, open the setup page, enter WiFi credentials.
4. Review `log.txt` on the Pico for the boot/provisioning log.