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
| `module_firmware` | optional JSON string: the manifest's `module_firmware{}` block (floors, e.g. `{"buzzer":{"min":"3.3.1"}}`), persisted to `wifi.json` for the headless OTA check |

**Response:** the "Connected!" HTML page. (The Pico acts on the credentials after
the page is delivered.)

**What happens after the reset**, in order — this is where firmware is actually
handled, because it is the first point at which the Pico has internet:

1. Join home WiFi, download `product.py` if missing.
2. Resolve `module_firmware` floors into concrete versions: fetch
   `Ecosystem/software/modules.json`, then each module's `firmware/index.json`.
3. Rescue any module parked in its bootloader at `0x7E` (DEV-31), **before**
   enumerating — a parked module never answers the enumeration sweep.
4. Compare installed versus published; if nothing is outdated, stop here having
   written nothing to flash.
5. Otherwise check the index's `layout` against each module's actual bootloader
   layout (byte 5 of the bootloader's `0xB1` reply) — exact match — and drop any
   type whose modules cannot run the image; fetch every remaining image
   **before** touching a module; flash from local files; delete them.
6. Run `product.py`.

Every step degrades to a no-op rather than failing the boot. Progress is
log-only (`log.txt` + `noknok_events.txt`) — there is no live channel back to the
phone by this point, since it is long off the setup AP.

## Planned

### `POST /settings`  — NOT YET IMPLEMENTED
Push product configuration to the device so the app can configure a running product
(colour, brightness, sundown length, …). Intended contract:

| Field | Value |
|-------|-------|
| `settings` | JSON string written verbatim to the device's `product_settings.json` |

The product reads `product_settings.json` on its next start (and, later, could watch
it live). **Device settings convention** (see `poc/scripts/smart_lamp.py`):

- **One generic file per device:** `product_settings.json` — a Pico runs one product
  at a time, so the filename is product-agnostic and this endpoint needn't know which
  product is installed (mirrors `wifi.json` / `noknok_roles.json` / `noknok_state.json`).
- **Product-tagged inside:** `{"product":"<manifest-id>", ...state...}`. A product
  ignores a settings file whose `product` tag isn't its own (stale after a switch).

App-side, store one such blob per device (each device = one product) rather than one
monolithic all-products document.

## Related on-device files

| File | Written by | Purpose |
|------|-----------|---------|
| `wifi.json` | `/connect` | home WiFi creds + `script_url` + `module_firmware` |
| `noknok_roles.json` | `/roles/*` | `role_id → module UID` map |
| `noknok_state.json` | `enumerate()` | last-known module addresses (fast reconnect) |
| `product_settings.json` | the product (+ future `/settings`) | product runtime settings |
| `log.txt` / `noknok_events.txt` | `code.py` + products | boot log / durable `[FW]` audit |

## See also
- Implementation: `software/code.py` (route handlers) and `software/noknok.py`
  (`enumerate_all`, `firmware_report`, `update_all`, `detect_interaction`, `append_role`).
- Confluence: *Software Development → Pico W Provisioning — Process & Implementation*
  (the process/flow narrative; this file is the endpoint reference).
