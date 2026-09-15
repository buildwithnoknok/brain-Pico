# noknok Pico — Provisioning HTTP API

The reference for the HTTP API the Pico brain exposes during provisioning. It is
served by `software/code.py` and consumed by the noknok app (and by the captive-
portal setup page). This is the app ↔ brain contract.

> **Private for now.** This is the provisioning layer (Architecture Open Decision
> #9 — provisioning/OTA Pico-side stays private for now). Keep this doc with the
> `brain-Pico` provisioning code, not in a public repo, until that split is settled.

## Transport

- **Base URL:** `http://192.168.4.1` (the Pico's `noknok-setup` AP), port **80**.
- **Content type:** request bodies are `application/x-www-form-urlencoded`.
  Values are URL-decoded server-side (`+` → space, `%XX` → byte).
- **JSON-in-a-field:** where a field carries structured data (e.g. `module_firmware`)
  it is a JSON **string** inside the form field, not a JSON request body.
- **When:** all the `POST` endpoints below are called by the app **while the phone
  is on the `noknok-setup` AP**, before `/connect` hands the Pico onto home WiFi.
  They are transport-agnostic in code, so they could later be served on home WiFi
  unchanged.

## Endpoints

### `GET /`
Serves the HTML WiFi-setup page. Captive-portal probe paths
(`/hotspot-detect.html`, `/generate_204`, `/ncsi.txt`, `/connecttest.txt`, …) also
return this page so the OS shows a "Sign in to network" prompt.

### `POST /firmware/check`
Report each connected module's **installed** firmware. Read-only — **no flashing**
happens here (that runs headless once the Pico is on WiFi).

| Field | Value |
|-------|-------|
| `module_firmware` | JSON string: the manifest's `module_firmware{}` block, e.g. `{"buzzer":{"min":"3.3.1"}}` |

**Response** `application/json`:
```json
{ "update_needed": false,
  "resolved": false,
  "modules": [ {"type":"buzzer","installed":"3.3.0","required":null,"needs_update":false} ] }
```

> **`resolved` is always `false` here, and `update_needed` with it.** This endpoint
> runs while the phone is on the `noknok-setup` AP, so the Pico has **no internet**
> and cannot reach the module registry to find out what the current firmware
> version is. It can only report what is installed. Manifests carry a floor
> (`min`), not a version to install, so there is nothing to compare against
> offline. The real check — resolve, gate, download, flash — runs after the WiFi
> join in `check_and_flash_modules()`. See
> [Module Firmware Index](https://github.com/buildwithnoknok/Ecosystem/blob/main/software/firmware-index.md).

The `modules[]` list is still useful to the app as a "what did the brain actually
see" confirmation. Degrades gracefully (`modules:[]`) if there is no Conductor/bus.

### `POST /roles/assign`  (preferred)
Detect which module the customer interacts with **and** save the role in one round
trip. Blocks up to ~20 s waiting for an interaction.

| Field | Value |
|-------|-------|
| `role_id` | role to assign, e.g. `power_button`, `brightness_knob` |
| `module_type` | `knob`, `led_button`, `buzzer`, … |
| `exclude` | optional, comma-separated `uid_hex` of already-assigned modules |

**Response:** `{"uid":"<hex>","saved":true}` · `{"uid":"<hex>","saved":false}` (detected,
write failed) · `{"timeout":true}` (nobody interacted in time).

### `POST /roles/detect` and `POST /roles/save`  (legacy, still supported)
The two-step form of the above. `/roles/detect` (`module_type`, `exclude`) →
`{"uid","type"}` or `{"timeout":true}`. `/roles/save` (`role_id`, `uid`) → `{"ok":bool}`.
Prefer `/roles/assign` — it avoids a fragile second request right after the long
blocking detect.

### `POST /connect`
Final step: hand the Pico its home-WiFi credentials (plus the chosen product's
script + firmware manifest). The Pico saves them, then hard-resets into WiFi mode
and continues headless (download `product.py`, OTA-update modules, run the product).

| Field | Value |
|-------|-------|
| `ssid` | home WiFi name (required) |
| `password` | home WiFi password |
| `script_url` | raw URL of the product's `product.py` (from the manifest's `files[]`) |
| `module_firmware` | optional JSON string: the manifest's `module_firmware{}` block (floors, e.g. `{"buzzer":{"min":"3.3.1"}}`), persisted with the credentials (Store + `/data/wifi.json`) for the headless OTA check |

**Response:** the "Connected!" HTML page. (The Pico acts on the credentials after
the page is delivered.)

**What happens after the reset**, in order — this is where firmware is actually
handled, because it is the first point at which the Pico has internet:

1. Join home WiFi, download `product.py` if missing.
2. If a firmware check completed less than 24 h ago (time kept in
   `microcontroller.nvm`), skip to step 4 with the cache as the only image
   source — no GitHub requests this boot.
3. Otherwise resolve `module_firmware` floors: fetch
   `Ecosystem/software/modules.json`, then each module's `firmware/index.json`
   **and the stage-1 bootloader's** (`bootloader.stage1` in the registry);
   then refresh the on-device image cache (`/data/fw_<type>.bin` + `.json` sidecar,
   `/data/fw_stage1.bin` for the bootloader) for everything whose cached version/crc
   differs from current — **before any Conductor exists** (downloads after one
   has existed can hang). Each download is verified against the index's
   `size`/`crc32`.
4. Rescue any module parked in its bootloader at `0x7E` (DEV-31), **before**
   enumerating — a parked module never answers the enumeration sweep. Image
   comes from the cache, layout-checked against the module's own bootloader.
5. **Stage-1 pass:** every I²C module below the published stage-1 version gets
   it, and its current app back, in one transaction from the cache — same
   layout only; legacy bootloaders skipped. A module's stage-1 version is read
   once and remembered in the Store's module state (`"bl"`).
6. Compare installed app versions versus published; if nothing is outdated,
   stop here having written nothing further to flash.
7. Check the index's `layout` against each outdated module's actual bootloader
   layout (the remembered `"bl"`, or one read) — exact match, **per module** —
   and exclude the ones that cannot run the image; flash the rest from the
   cache. The radio is not involved from here on.
8. If anything was refused or failed (update, fetch, rescue, stage-1): buzzer
   error motif + LED Buttons red for a moment — the customer's only signal
   until the app can show device status.
9. Run `product.py` under three-strikes crash recovery (restart with backoff;
   after three, a safe idle that still answers the factory-reset gesture and
   re-fetches `product.py` once if online).

**If the WiFi join fails** (router down, out of range): nothing is deleted.
With `product.py` present the product runs offline — no OTA, no NTP, but a
module parked after a power cut is still rescued from the on-device image
cache — and the credentials are retried on the next boot. Without
`product.py` the setup AP is offered, credentials kept.

Every step degrades to a no-op rather than failing the boot. Progress goes to
the serial console and the event history in the runtime Store (`events()` in
`code.py`; what DEV-36 will show the customer); `/data/log.txt` is written only
with the bench marker `/debug_log` present. There is no live channel back to
the phone by this point, since it is long off the setup AP.

## Planned

### `POST /settings`  — NOT YET IMPLEMENTED
Push product configuration to the device so the app can configure a running product
(colour, brightness, sundown length, …). Intended contract:

| Field | Value |
|-------|-------|
| `settings` | JSON object stored in the runtime Store under `settings` (product-tagged) |

Superseded by the Device Protocol v1 `settings.get/set/reset` ops (DEV-34, Confluence
113868802). Constraint carried over from DEV-18: **settings values live in the runtime
Store (FRAM / nvm), never in a file** — the product reads them through `c.settings`.
Convention: product-tagged, `{"product":"<manifest-id>", ...values...}`; a product ignores
values whose tag isn't its own (stale after a switch). App-side, one blob per device.

## Related on-device data

Since DEV-18 (15 Sep 2026) the brain **never writes its filesystem while a product runs**
— a power cut during any FAT write can destroy the filesystem on this platform. Runtime data
lives in the **Store** (`noknok.store()`: I2C FRAM at 0x50 on the PicoHub, else `nvm`);
setup/OTA-time files live in `/data/`.

| Where | Written by | Purpose |
|-------|-----------|---------|
| Store `wifi` + `/data/wifi.json` | `/connect` | home WiFi creds + `script_url` + `module_firmware` (file is primary at boot; rebuilt from the Store copy if lost) |
| Store `roles` + `/data/noknok_roles.json` | `/roles/*` | `role_id → module UID` map |
| Store `state` | `enumerate()` | UID → address/type/bootloader (stable addresses; written only when hardware changes) |
| Store `settings` | DEV-34 `settings.*` | product runtime settings |
| Store `events` | `code.py` | last 40 `[FW]` `[RESCUE]` `[CRASH]` `[ROLE]` `[CFG]` lines (replaces `noknok_events.txt`) |
| `/data/product.py` | first connected boot / re-fetch | the product script (compiled before it replaces the running copy) |
| `/data/fw_<type>.bin` + `.json` | OTA pass | on-device image cache + sidecar (version, layout, size, crc32). Flash source and offline rescue source. |
| `/data/log.txt` | `code.py`, bench only | verbose boot log, written only with the `/debug_log` marker present |

Legacy root `wifi.json` / `product.py` / `noknok_state.json` on brains provisioned before
`/data` existed are still read, never written.

## See also
- Implementation: `software/code.py` (route handlers) and `software/noknok.py`
  (`enumerate_all`, `firmware_report`, `update_all`, `detect_interaction`, `append_role`).
- Confluence: *Software Development → Pico W Provisioning — Process & Implementation*
  (the process/flow narrative; this file is the endpoint reference).
