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
| `code.py` | Provisioning brain + launcher. WiFi-AP setup on first boot, then connect + download + run the app-selected product script crash-safely on every boot. |
| `noknok.py` | Conductor library — module discovery/enumeration + drivers (Buzzer, Knob, LED Button, ...). Includes the factory-reset watchdog. |
| `trio_demo.py` | Light & Sound Controller demo — uses all three I2C modules. |
| `noknok_setup_roles.py` | One-time role-assignment wizard. |
| `noknok_roles_test.py` | Roles smoke test. |
| `knob_test.py` | Knob module standalone test. |
| `keyboard_test.py` | LED button module standalone test. |

## Provisioning (PoC Step 1 — done)

First boot: Pico starts a WiFi AP `noknok-setup` and serves a captive-portal setup page.
The user (or the noknok app) enters their home WiFi; the Pico saves it, hard-resets into WiFi
mode, downloads the product script from GitHub over HTTPS, saves it as `product.py`, and runs
it. Subsequent boots reconnect to WiFi and re-run the product directly.

**Note:** BLE was the original plan but is not supported on RP2350 in CircuitPython 10.x —
provisioning uses WiFi AP instead. Full process, test steps, and gotchas are documented in
Confluence: *Software Development -> Pico W Provisioning — Process & Implementation*.

## Current versions & features (PoC v1)

**`code.py` v0.7** — provisioning + launcher:
- The app POSTs `ssid`, `password` and **`script_url`** to `192.168.4.1/connect`. The Pico
  downloads whatever product `script_url` points to, so the brain is **product-agnostic** —
  a new product is just a new manifest + script, no firmware change. (`SCRIPT_URL` remains a
  fallback default.)
- **URL-decodes** the form fields (the app sends `application/x-www-form-urlencoded`).
- **Retries the WiFi join 3×** on both the provisioning and direct-boot paths — the Pico W
  radio often fails the first join after AP mode with "Unknown failure 205", then succeeds.
- **Timestamped logging** to `log.txt` (uptime, plus UTC wall-clock once `adafruit_ntp` syncs).
- Crash-safe: a failing `product.py` is caught and the board enters a safe idle, not a reboot loop.

**`noknok.py` v1.3** — Conductor library:
- Dynamic I2C addressing: modules boot at staging address `0x7F` and are assigned runtime
  addresses; `noknok_state.json` caches the UID→address map so reboots re-find modules without
  re-enumerating (and self-heals if hardware changed).
- **`Conductor.check_factory_reset(knob_status)`** — call once per product loop, passing the
  `KnobStatus` you already read. Hold the Knob button ~5 s to wipe `wifi.json`, `product.py` and
  `noknok_roles.json` and reboot into the `noknok-setup` AP, so the app can install a different
  product. `noknok_state.json` is deliberately **kept** (a soft reset doesn't power-cycle the
  modules, so they keep their addresses).

### Required CircuitPython libs (/lib)
- `adafruit_httpserver/`
- `adafruit_requests.mpy`
- `adafruit_connection_manager.mpy`
- `adafruit_ntp.mpy` (optional — enables wall-clock timestamps)

### Flash / test
1. Copy `boot.py` + `code.py` + `noknok.py` + the libs to the Pico (CIRCUITPY drive or Thonny).
   Write `.py` files **without a BOM** — CircuitPython errors on a leading byte-order mark.
2. **Power-cycle** the Pico (the radio is not reset by a soft reboot; a power cycle also returns
   the I2C modules to their `0x7F` staging address).
3. Join `noknok-setup`, open the setup page (or use the noknok app), enter WiFi credentials.
4. Review `log.txt` on the Pico for the boot/provisioning log (or watch the live serial console
   in Thonny — the host drive view of `log.txt` can be stale while the device owns the filesystem).
