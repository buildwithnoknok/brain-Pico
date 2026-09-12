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
| `ledbutton_color_test.py` | Test one or many LED Buttons at once: a button press cycles that button's colour; number keys 1-9 set all to a colour (0 = off). Quick "is this module alive?" check + brightness comparison. |

## Provisioning (PoC Step 1 — done)

First boot: Pico starts a WiFi AP `noknok-setup` and serves a captive-portal setup page.
The user (or the noknok app) enters their home WiFi; the Pico saves it, hard-resets into WiFi
mode, downloads the product script from GitHub over HTTPS, saves it as `product.py`, and runs
it. Subsequent boots reconnect to WiFi and re-run the product directly.

**Note:** BLE was the original plan but is not supported on RP2350 in CircuitPython 10.x —
provisioning uses WiFi AP instead. Full process, test steps, and gotchas are documented in
Confluence: *Software Development -> Pico W Provisioning — Process & Implementation*.

**HTTP API reference:** the app ↔ brain endpoints (`/connect`, `/firmware/check`,
`/roles/assign`, and the planned `/settings`) are documented in
[docs/provisioning-http-api.md](docs/provisioning-http-api.md).

## Current versions & features (PoC v1)

**`code.py` v0.15** — provisioning + launcher + module firmware OTA. Field-hardened 12 Sep 2026:
- **Bootloader updates over the air (DEV-31).** The registry names the current stage-1
  (`module-I2C-bootloader/firmware/index.json`); it is cached like any image, and before the
  app pass every I²C module below the published stage-1 gets it — and its current app back —
  in one `stage1_update()` transaction from the cache. Same flash layout only (the module
  refuses a cross-layout stage-1 itself, error 8, so the brain refuses first); a legacy
  monolithic bootloader is logged once and left for SWD. Each module's stage-1 version is
  read **once** (a bootloader round-trip) and remembered in `noknok_state.json` (`"bl"`), so
  later checks and the layout gate are a free comparison.
- **Cache-first, once a day.** On a connected boot the OTA pass first refreshes an on-device
  image cache (one download per type per published version) — **before any Conductor exists**,
  the one condition under which downloads on this board are reliable — then creates the
  Conductor and flashes from the cache. It does so at most once per 24 h (last-check time in
  `microcontroller.nvm`); other boots make no requests and still rescue a parked module from
  the cache. The gate refuses per module, not per type. Any refused/failed update or rescue
  plays the buzzer error motif and flashes the LED Buttons red before the product starts.
- **Runs offline.** A failed WiFi join never deletes `wifi.json` any more; if `product.py`
  exists the product runs without updates and the credentials are retried next boot. Only the
  factory-reset gesture removes them. (It used to wipe them after three attempts and fall into
  AP mode — a router hiccup meant a full re-setup, and no product until then.)
- **Three-strikes crash recovery.** A `product.py` exception restarts it with backoff; the count
  lives in `microcontroller.nvm` and clears on a real power-on. After three, a clean boot parks
  in a safe idle that still answers the knob-hold factory reset and, if online, fetches a fresh
  `product.py` once — acting on it only if it differs. (It used to end at "Code done running".)
- **Flash writes only when they earn it.** `log.txt` is written only with the bench marker
  `/debug_log` present, or once on a crash (a "RING FLUSH" of the last ~80 lines); otherwise
  logging is serial + RAM. `noknok_state.json` is rewritten only on a real change. The events
  file is unchanged. See DEV-18 for why this matters on an unjournaled FAT filesystem.
- **Image integrity + offline rescue cache.** Downloads are checked against `index.json`
  `size`/`crc32` before touching a module, and every fetched image is kept on the Pico with a
  sidecar so a module parked after a power cut is rescued with no internet.

Earlier features:
- The app POSTs `ssid`, `password` and **`script_url`** to `192.168.4.1/connect`. The Pico
  downloads whatever product `script_url` points to, so the brain is **product-agnostic** —
  a new product is just a new manifest + script, no firmware change. (`SCRIPT_URL` remains a
  fallback default.)
- **URL-decodes** the form fields (the app sends `application/x-www-form-urlencoded`).
- **Retries the WiFi join 3×** on both the provisioning and direct-boot paths — the Pico W
  radio often fails the first join after AP mode with "Unknown failure 205", then succeeds.
- **Timestamped logging** (uptime, plus UTC wall-clock once `adafruit_ntp` syncs) to the serial
  console and a RAM ring; to `log.txt` only with the `/debug_log` marker (see above).
- **Role assignment over the AP** (PoC v1 Step 3): `POST /roles/assign` (form `role_id`,
  `module_type`, `exclude`) detects which module the customer touches **and** saves the
  `role → UID` in one request → `{"uid","saved":true}` or `{"timeout":true}`. (Older
  `/roles/detect` + `/roles/save` endpoints are kept too.) A `Conductor` is created and
  enumerated lazily on first use and cached. Handlers are transport-agnostic (reusable for a
  future home-WiFi settings page).
- **Module firmware OTA.** A manifest declares only a **floor** per module type
  (`{"buzzer":{"min":"3.3.1"}}`), not a version or a `.bin` URL. On the connected boot, before
  `product.py` runs, `check_and_flash_modules()` resolves that floor into concrete firmware:
  it fetches the [module registry](https://github.com/buildwithnoknok/Ecosystem/blob/main/software/modules.json)
  to map each type to its repo, then that repo's `firmware/index.json` for the current
  `{version, url, layout}`. Firmware is backwards compatible, so the brain
  installs what is **current**, not what the product was written against — and because the
  version lives in the same commit as the binary, the two cannot drift apart. See
  [firmware-index.md](https://github.com/buildwithnoknok/Ecosystem/blob/main/software/firmware-index.md).
- **Cache-first.** When a check is due (once per 24 h), the pass refreshes the on-device cache
  (`/fw_<type>.bin` + `.json` sidecar) for every type whose cached version/crc differs from
  current — **before any Conductor exists** — then creates the Conductor, compares versions,
  gates per module, and flashes from the cache. Past the Conductor no step needs the radio, so
  a WiFi drop cannot leave one module half-written and the rest untouched. The cache stays: it
  is the offline rescue source. (A download made after a Conductor has existed in the process
  can hang without timing out — reproduced 12 Sep 2026 — which is why the order is fixed.)
- **`_bootloader_gate()` refuses an image the module cannot run.** Backwards compatibility is a
  promise about the *protocol*, not about *installability* — an app relinked to a new base
  address is wire-compatible and still hangs a module whose bootloader writes elsewhere, and
  the CRC cannot catch it (image bytes, not link address). The index declares a numeric flash
  `layout`; the bootloader **states its own** as byte 5 of its `0xB1` reply (stage-1 1.2.0+);
  `Conductor.bootloader_layout()` compares them and the match is **exact**. Fails closed: a
  legacy bootloader (no `0xB1`), a stage-1 too old to say (byte 5 = 0), or an index with no
  layout is refused, never guessed at. Nothing host-side infers a layout from a version.
- **Parked-module rescue (DEV-31).** A module stuck in its bootloader at `0x7E` never answers
  the enumeration sweep, so it would otherwise be invisible — the product would simply start a
  module short. `get_conductor()` runs `rescue_parked_module()` **between** `Conductor()` and
  `enumerate_all()`, applies the same layout check, and pushes a good app back onto it. Logged
  as `[RESCUE]`. Proven 12 Sep 2026: an app that never armed its watchdog was parked by stage-1
  1.2.0 and rescued over the bus by the ordinary boot path in 2 s — no SWD.
- `POST /firmware/check` (AP time) reports installed versions only and returns
  `resolved:false` — on the setup AP the Pico has no internet and cannot reach the registry.
- Crash-safe throughout — a failed flash leaves the module safe in its bootloader (`0x7E`).
  Outcomes go to the serial console and `noknok_events.txt` (durable `[FW]`/`[RESCUE]`/`[CRASH]`
  audit trail). The post-flash re-enumerate deliberately does **not** wipe `noknok_state.json`,
  so modules that weren't flashed keep their addresses.

**`noknok.py` v1.6** — Conductor library.

DEV-31 additions (Sam), all bench-proven over I2C:
`bootloader_version(entry)` — the fleet discriminator; `None` means the legacy monolithic
bootloader, which cannot self-update and needs SWD. `stage1_update(entry, image, app_image)` —
replace a module's bootloader over the bus, then restore its app.
`rescue_parked_module(get_image)` — recover a module stuck at `0x7E`; **call it before
`enumerate()`**, since a parked module never answers the enumeration sweep. Bootloader error
codes to know: **7** = app unhealthy, module parked, rescue it; **8** = wrong file.

Core:
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
4. Watch the live serial console, or — to have `log.txt` written on the Pico — first create an
   empty file named `debug_log` in its root (that marker is the only thing that turns on flash
   logging; the field default is off). The host drive view of `log.txt` can be stale while the
   device owns the filesystem. `noknok_events.txt` is always written.

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

---

## License

- Code: MIT — see [LICENSE](LICENSE).

---

## Safety & Liability

noknok hardware is an electronic device and a DIY/maker kit. You assemble, modify, flash, power, and operate it at your own risk, and it is provided as is, without warranty. See the full notice: [License, Safety & Liability](https://buildwithnoknok.github.io/safety-and-license/).
