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
| `module_flasher.py` | I2C OTA flasher — streams a module application `.bin` to the CH32V003 bootloader (`ModuleFlasher`). Shared by the bench tool and (later) the provisioning flow. |
| `bench_flash.py` | Bench bring-up tool — flashes application firmware onto a blank module (bootloader only) one at a time from the REPL. See [Bench-flashing modules](#bench-flashing-modules-bring-up). |
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

**`code.py` v0.9** — provisioning + launcher:
- The app POSTs `ssid`, `password` and **`script_url`** to `192.168.4.1/connect`. The Pico
  downloads whatever product `script_url` points to, so the brain is **product-agnostic** —
  a new product is just a new manifest + script, no firmware change. (`SCRIPT_URL` remains a
  fallback default.)
- **URL-decodes** the form fields (the app sends `application/x-www-form-urlencoded`).
- **Retries the WiFi join 3×** on both the provisioning and direct-boot paths — the Pico W
  radio often fails the first join after AP mode with "Unknown failure 205", then succeeds.
- **Timestamped logging** to `log.txt` (uptime, plus UTC wall-clock once `adafruit_ntp` syncs).
- Crash-safe: a failing `product.py` is caught and the board enters a safe idle, not a reboot loop.
- **Role assignment over the AP** (PoC v1 Step 3): `POST /roles/assign` (form `role_id`,
  `module_type`, `exclude`) detects which module the customer touches **and** saves the
  `role → UID` in one request → `{"uid","saved":true}` or `{"timeout":true}`. (Older
  `/roles/detect` + `/roles/save` endpoints are kept too.) A `Conductor` is created and
  enumerated lazily on first use and cached. Handlers are transport-agnostic (reusable for a
  future home-WiFi settings page).

**`noknok.py` v1.5** — Conductor library:
- Dynamic I2C addressing: modules boot at staging address `0x7F` and are assigned runtime
  addresses; `noknok_state.json` caches the UID→address map so reboots re-find modules without
  re-enumerating (and self-heals if hardware changed).
- **`Conductor.check_factory_reset(knob_status)`** — call once per product loop, passing the
  `KnobStatus` you already read. Hold the Knob button ~5 s to wipe `wifi.json`, `product.py` and
  `noknok_roles.json` and reboot into the `noknok-setup` AP, so the app can install a different
  product. `noknok_state.json` is deliberately **kept** (a soft reset doesn't power-cycle the
  modules, so they keep their addresses).
- **`Conductor.detect_interaction(module_type, timeout, exclude)`** — return the UID of the
  module the customer interacts with (knob turn/press, LED-button press). Guides them with
  light + sound: candidate LED buttons go amber, the picked one green, with ready/confirm
  buzzer beeps (best-effort). **`Conductor.append_role(role_id, uid)`** writes one entry to
  `noknok_roles.json`. **`load_roles()`** maps roles back to modules for the product to use.

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

## Bench-flashing modules (bring-up)

`bench_flash.py` puts **application firmware** onto a CH32V003 module that has only the
bootloader on it (a fresh board flashed via SWD with `module-I2C-bootloader`, no app yet). It's
a one-time factory/bench step — customers receive modules already flashed.

**Why it's manual / one module at a time:** a blank module has *no type identity*. The bootloader
is the same code on every buzzer, knob and LED button, so a module waiting in flash mode at `0x7E`
can't tell the Pico what it is — the type only exists once an app is flashed (and reported during
enumeration). And every blank module answers at `0x7E`, so two on the bus at once = address
collision. Hence: connect one module, tell it the type, flash, swap in the next.

### Prerequisites — files on the CIRCUITPY root
| File | From |
|------|------|
| `noknok.py` | this folder |
| `module_flasher.py` | this folder |
| `bench_flash.py` | this folder |
| `buzzer_firmware.bin` | `module-I2C-buzzer/firmware/bin/` |
| `knob_firmware.bin` | `module-I2C-knob/firmware/bin/` |
| `keyboard_firmware.bin` | `module-I2C-ledbutton/firmware/bin/` (this is the LED button — note the legacy name) |

Copy the `.py` files **without a BOM** (use Thonny or plain file copy, not
`Set-Content -Encoding utf8`). The `.bin` files are the **offset-linked application images**
(`make build` output, linked at `0x1000`) — *not* full-flash images. They are open-source and
also published on each module repo's GitHub Releases.

### Steps
1. Connect **one** module to the Pico's I2C bus (PicoHub). It can be blank or already running an app.
2. In Thonny's REPL: `import bench_flash`
3. Pick the module type from the menu (`1` buzzer / `2` knob / `3` led_button).
4. It flashes over I2C (with a live `%` progress print), boots the app, then re-enumerates to
   **confirm** the module came up as the expected type and prints its UID.
5. Swap in the next module and press Enter; type `q` at the menu to stop.

The flash is CRC-verified by the bootloader and the validity marker is only written on a full,
correct flash — so a failed or interrupted flash never bricks the module; it simply stays in the
bootloader at `0x7E`, ready to retry. SWD (the 5-pin header) remains the unbrickable fallback.
Full bootloader design is in Confluence: *Software Development → I²C Module Bootloader — Design & Process*.
