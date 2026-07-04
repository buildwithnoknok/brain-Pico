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
Compare each connected module's installed firmware against the product manifest.
Read-only — **no flashing** happens here (that runs headless once the Pico is on WiFi).

| Field | Value |
|-------|-------|
| `module_firmware` | JSON string: the manifest's `module_firmware{}` block, e.g. `{"buzzer":{"version":"3.3.1","url":"..."}}` |

**Response** `application/json`:
```json
{ "update_needed": true,
  "modules": [ {"type":"buzzer","installed":"3.3.0","required":"3.3.1","needs_update":true} ] }
```
Degrades gracefully (returns `update_needed:false, modules:[]`) if no Conductor/bus.

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
| `module_firmware` | optional JSON string: the manifest's `module_firmware{}` block, persisted for the headless OTA check |

**Response:** the "Connected!" HTML page. (The Pico acts on the credentials after
the page is delivered.)

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
