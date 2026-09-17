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
- **When:** the form endpoints below are called by the app **while the phone
  is on the `noknok-setup` AP**, before `/connect` hands the Pico onto home WiFi.
  Since code.py 0.17 the brain also serves **`POST /rpc`** (JSON, see below) on the
  AP *and* on home WiFi for the product's whole lifetime — that is the channel new
  app code uses; the form endpoints are adapters over the same handlers.

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

## `POST /rpc` — Device Protocol v1 (DEV-34, since code.py 0.17)

One JSON message protocol, transport-agnostic (spec: Confluence 113868802 §2). The
form endpoints above are now thin **adapters** over the same handlers; new app code
uses `/rpc` only. Served **on the setup AP and on home WiFi for the product's whole
lifetime**, advertised as **`noknok-XXXX.local`** (mDNS, `XXXX` = last two bytes of
the MCU id; `hello` returns the name). Implementation: `software/noknok_rpc.py`
(dispatcher + HTTP carrier + servicing), ops registered in `code.py`.

- **Request:** `POST /rpc`, body `application/json`:
  `{"id": <client-chosen>, "op": "<name>", "args": {...}}`. `token` is reserved
  (pairing = DEV-35; v1 is open, like the AP today).
- **Reply:** `{"id": <echoed>, "ok": true, ...fields}` or
  `{"id": <echoed>, "ok": false, "error": "<code>", "detail": "..."}`. Unknown op →
  `"error": "unknown op: x"`; malformed body → `"bad json"`, `id: null`.
- **While a product runs** it is answered between module transactions (noknok.py's
  drivers pump the channel, throttled to 50 ms; ~1 ms idle, 10–100 ms per request,
  ≤ 240 ms when a phone stalls mid-request — soak, DEV-34 comment 10829). A product
  idling in `time.sleep()` should use `c.sleep()`; otherwise replies wait for its next
  module read. **Offline brains have no channel** (AP-on-demand = DEV-35).
- Expect **~0.4 s round trip** for now (two-segment response, see DEV-34 follow-up).

| op | args | reply fields | notes |
|----|------|--------------|-------|
| `hello` | — | `device`, `name`, `state` (`unprovisioned` / `provisioned` / `parked`), `product{id,script,script_url}`, `versions{code,noknok,noknok_usb,rpc,circuitpython}`, `online`, `ap`, `carrier`, `uptime`, `ops[]` | open, cheap — call first |
| `status` | `since` (int, optional) | `state`, `strikes`, `store` (`fram`/`nvm`), `drive_visible`, `mem_free`, `ip`, `modules[{type,uid,fw}]`, `events[]`, `events_total` | phase-1 subset; DEV-36 adds firmware state |
| `roles.assign` | `role_id`, `module_type`, `exclude[]` | `uid`, `type`, `saved` / `timeout` | = `/roles/assign`; blocks ≤ 20 s |
| `firmware.check` | `module_firmware{}` | as `/firmware/check` | AP time only: `resolved:false` |
| `provision` | `ssid`, `password`, `script_url`, `module_firmware`, `product_id` | `accepted`, `mode` (`setup` / `switch`), `rebooting` | on the AP = `/connect`; on home WiFi = **product switch**: `ssid`/`password` optional (keeps the network), saves, drops `product.py`, reboots; the boot path downloads the new script |
| `reboot` | — | `rebooting: true` | reply first, reset 0.5 s later |
| `factory_reset` | — | `resetting: true` | same wipe as the knob-hold gesture |
| `settings.get/set/reset` | — | — | **next** (c.settings, DEV-34) |

`product_id` (the manifest id) is new and optional on `/connect` and `provision`; it is
stored with the credentials so the app can fetch the right `config_schema` later.

Bench: `tools/rpc_call.py <host> <op> ['{json args}']` from the Pi; the smallest product
that exercises the channel is `software/bench_rpc_product.py` (put as `/data/product.py`).

### Boot note — reload on power-on (17 Sep 2026)
`main()` reloads itself once on every power-on / hard reset. CircuitPython 10.3.0 on the
Pico 2 W never associates after a cold boot when a multi-second compile (this file +
noknok.py) freezes the VM right after the radio's cold init; a soft reload joins in 3 s.
Bisected on the bench, nothing from Python un-wedges the radio. Costs ~3 s per power-on.
Real fix: no boot-time compile (precompiled `.mpy` / frozen modules, DEV-38).

### Settings — where they live (constraint from DEV-18)
**Settings values live in the runtime Store (FRAM / nvm), never in a file** — the product
reads them through `c.settings`. Convention: product-tagged,
`{"product":"<manifest-id>", ...values...}`; a product ignores values whose tag isn't its
own (stale after a switch). App-side, one blob per device. Design for the **nvm** backend
(FRAM undecided): 79 ms blocking write, finite endurance → write on change only, debounced
on idle (5–10 s), never in a product's hot loop.

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
