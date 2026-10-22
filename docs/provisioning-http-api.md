# noknok Pico — Provisioning HTTP API

The reference for the HTTP API the Pico brain exposes during provisioning. It is
served by `software/code.py` and consumed by the noknok app (and by the captive-
portal setup page). This is the app ↔ brain contract.

> **Open.** Provisioning is open (Architecture Open Decision #9 resolved): this doc
> lives with the provisioning code in the public `brain-Pico` repo (MIT). Only the
> noknok app and the backend stay closed.

## Transport

- **Base URL:** `http://192.168.4.1` (the Pico's `noknok-setup` AP), port **80**.
- **Content type:** request bodies are `application/x-www-form-urlencoded`.
  Values are URL-decoded server-side (`+` → space, `%XX` → byte).
- **JSON-in-a-field:** where a field carries structured data (e.g. `module_firmware`)
  it is a JSON **string** inside the form field, not a JSON request body.
- **When:** the form endpoints below are called by the app **while the phone
  is on the `noknok-setup` AP**, before `/connect` hands the Pico onto home WiFi.
  Since code.py 0.17 the brain also serves **`POST /rpc`** (JSON, see below) on the
  AP *and* on home WiFi for the product's whole lifetime; the form endpoints are
  adapters over the same handlers. Since app 1.5.4 the WiFi step uses `/rpc`
  (`wifi.scan`, `provision`, then `hello` while connecting, DEV-100); role assignment
  still uses the form endpoint `/roles/assign`; after setup the app uses `hello` and
  `settings.*`.
- **⚠ Client requirement — route over the hotspot.** `noknok-setup` has no internet, so
  Android (and iOS) keep mobile data as the default network while the phone is joined to
  it; a plain HTTP request to `192.168.4.1` then leaves over LTE and never arrives. A client
  must bind its traffic to the WiFi network for the whole setup (Android:
  `ConnectivityManager.bindProcessToNetwork` on a WiFi `NetworkRequest` *without* the
  INTERNET capability — app 1.5.1+, `MainActivity.kt`; iOS: `NEHotspotConfiguration` /
  per-request interface binding, not built yet), re-bind before each request (the phone
  reconnects when the brain restarts its hotspot) and unbind once the join is settled.
- **Setup answers before the WiFi join; failures are reported afterwards (DEV-100,
  code.py 0.22).** `provision` / `/connect` first check that the network is visible
  (a scan; skipped with `force` or when scanning is impossible) and refuse at once with
  `accepted:false, reason:"network_not_found"` (hotspot kept). Otherwise they answer
  `accepted:true` with a `join_id` - **not saved, not joined yet**. The brain then stops
  the hotspot and tries the home WiFi (3 attempts; a wrong password stops at the first)
  and saves the credentials only after a successful join. On failure nothing is saved,
  the device plays its alert, and `noknok-setup` comes back with `hello` →
  `setup.last_join {join_id, ok:false, reason}`: `auth` (wrong password), `not_found`,
  `timeout`, `other`, or `not_saved` (drive open to a PC). RAM only: a power cut
  forgets it (the app sees the uptime go backwards and says "restarted"). The brain cannot
  report success (it is gone from the hotspot), so the app treats "no failure within
  ~90 s" as success until DEV-70 can confirm on the home network.
- **The reply must arrive before the hotspot drops (DEV-141, code.py 0.30).** After
  accepting, the brain keeps the hotspot and the server up until the app acks the reply
  (`hello` with `args.ack` = the `join_id`) or 5 s pass (`REPLY_GRACE_S`; the browser
  form and older apps take the 5 s). A fixed 1 s was too short: one lost radio frame is
  retransmitted only after ~3 s, and on a successful join the brain resets, so the reply
  was lost and the app showed an error after a setup that worked. `provision` also takes
  an optional app-chosen `join_id` (letters, digits, `-`, max 32 chars; anything else and
  the brain makes its own), and `hello.setup.received` names the last accepted one. So an
  app whose reply got lost anyway can still follow the join with its own `join_id`, and a
  `received` that is not its own means the brain never got the request.

## Endpoints

### `GET /`
Serves the HTML WiFi-setup page. Captive-portal probe paths
(`/hotspot-detect.html`, `/generate_204`, `/ncsi.txt`, `/connecttest.txt`, …) also
return this page so the OS shows a "Sign in to network" prompt.

### Module firmware during setup: nothing to call
There is **no firmware endpoint at setup time.** Module updates happen **silently** in the
brain's OTA pass after it joins home WiFi (`check_and_flash_modules()` in `code.py`):
resolve each module's current version from the
[Module Firmware Index](https://github.com/buildwithnoknok/Ecosystem/blob/main/software/firmware-index.md),
gate, download, flash. Outcomes land in the event history (`status` op, `[FW]` / `[RESCUE]`).
The app sends the manifest's `module_firmware{}` only inside `/connect` / `provision`.
`POST /firmware/check` and the `firmware.check` op existed until code.py 0.18; they were
removed in 0.19 (DEV-99) because, offline on the hotspot and with floor-only manifests, they
could only ever answer "no update". An older app that still calls the route gets HTTP 404
and shows nothing, exactly as before.

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
script + firmware manifest). The Pico replies at once (see *Transport*: the reply
comes before anything is saved, DEV-100), stops the hotspot and joins the home WiFi.
Only if the join works does it save the credentials (Store copy + `/data/wifi.json`)
and the `config_defaults`, then hard-reset into WiFi mode and continue headless
(download `product.py`, OTA-update modules, run the product). If the join fails nothing
is saved and the hotspot comes back.

| Field | Value |
|-------|-------|
| `ssid` | home WiFi name (required) |
| `password` | home WiFi password |
| `script_url` | raw URL of the product's `product.py` (from the manifest's `files[]`) |
| `module_firmware` | optional JSON string: the manifest's `module_firmware{}` block (floors, e.g. `{"buzzer":{"min":"3.3.1"}}`), persisted with the credentials (Store + `/data/wifi.json`) for the headless OTA check |

| `force` | optional, any value: skip the "is this network visible?" check (hidden network) |

**Response:** the "Connecting..." HTML page (the Pico acts on the credentials after the
page is delivered), or the setup form again with "Your device cannot see the network ..."
when the network is not visible (DEV-100). The app uses the `provision` op instead (JSON,
with `join_id`).

**What happens after the reset**, in order — this is where firmware is actually
handled, because it is the first point at which the Pico has internet:

0. **Boot-hold factory reset** (power-on run only, code.py 0.18): if an LED
   Button or Knob button is held from power-on, feedback after ~5 s (LED
   Buttons white, buzzer click); still held 3 s later → 3 flashes, LEDs dark, two
   rising notes, then the same wipe as the
   `factory_reset` op, then reboot into the setup AP. Otherwise the boot
   continues. Skipped on a factory-fresh device (nothing to reset).
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
cache — and the credentials are retried **while the product runs** (code.py 0.31 /
noknok_rpc 0.2, DEV-79; see *Link* below): once the router is back the brain joins
within 15 min without a power cycle, the `/rpc` channel comes up, the clock is synced
and mDNS starts (if `NOKNOK_MDNS = 1`). The module firmware check stays a boot-time
check (next due boot). Without `product.py` the setup AP is offered, credentials kept.

**A 192.168.4.x address on the home WiFi** (DEV-113, code.py 0.32): the setup hotspot's
DHCP server (built into CircuitPython) answers on every interface, so a device whose
hotspot DHCP server outlives its hotspot hands out `192.168.4.16..` with gateway/DNS
`192.168.4.1` on the home network: no internet, no DNS. Every join logs `[wifi] gateway ...
DNS ... AP <bssid> ch .. rssi ..`; such a lease is rejoined up to 3 times, then kept with a
`[WIFI]` event. The brain stops its hotspot together with that DHCP server (`_stop_ap()`).
Who answers DHCP on a network: `tools/bench/dhcp_offers.py`.

Every step degrades to a no-op rather than failing the boot. Progress goes to
the serial console and the event history in the runtime Store (`events()` in
`code.py`; what DEV-36 will show the customer); `/data/log.txt` is written only
with the bench marker `/debug_log` present. There is no live channel back to
the phone by this point, since it is long off the setup AP.

## `POST /rpc` — Device Protocol v1 (DEV-34, since code.py 0.17)

One JSON message protocol, transport-agnostic (spec: Confluence 113868802 §2). The
form endpoints above are now thin **adapters** over the same handlers. App 1.5.2 still
uses the form endpoints for setup and calls only `hello` and `settings.*` over `/rpc`
(after setup); the other ops are served but not called by the app yet. Served **on the
setup AP and on home WiFi for the product's whole lifetime**. **mDNS is off by default:**
the name **`noknok-XXXX.local`** (`XXXX` = last two bytes of the MCU id; `hello` returns
the name) is advertised only with `NOKNOK_MDNS = 1` in `settings.toml`; the app finds a
brain by sending `hello` to every address of the phone's /24 (or a typed IP).
**No authorisation:** every op is open to anyone who can reach port 80 (setup hotspot,
home LAN); pairing / authorisation is DEV-35. Implementation: `software/noknok_rpc.py`
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
  module read. **An offline brain has no channel** until its WiFi rejoin succeeds
  (code.py 0.31, DEV-79); AP-on-demand = DEV-35.
- **Round trip ≈ 13 ms** on a quiet LAN (18 Sep 2026). ⚠ That depends on
  `wifi.radio.power_management = NONE`, which `noknok_rpc.start_http()` sets
  (`no_power_save()`). In the CYW43's default power-save mode the radio only listens every
  beacon interval: a phone's request arrives as headers-then-body hundreds of ms apart, so
  the round trip was ~0.4 s **and** bodies read with a short window came back empty — every
  phone request answered `bad json`. Keep power management off, and keep `SOCKET_TIMEOUT` /
  `BODY_GRACE` at 1 s; anyone shortening them again must re-test from a real phone, not
  from curl (curl sends one packet and never reproduces it). `status.last_bad_request`
  keeps the last unparseable body for exactly this diagnosis.

| op | args | reply fields | notes |
|----|------|--------------|-------|
| `hello` | `ack` (optional, code.py 0.30+: the `join_id` just accepted) | `device`, `name`, `state` (`unprovisioned` / `provisioned` / `parked`), `product{id,script,script_url}`, `versions{code,noknok,noknok_usb,rpc,circuitpython}`, `online`, `ap`, `config{pins{i2c_a,i2c_b,usb},source,problems[]}`, `setup{last_join,received}`, `carrier`, `uptime`, `ops[]` | open, cheap — call first. `setup.last_join` (code.py 0.22+): `null`, or `{join_id, ssid, ok:false, reason, attempts, detail}` for the last failed setup join (see *Transport*). `setup.received` (code.py 0.30+): `join_id` of the last accepted `provision` this boot, or `null`. `config` (code.py 0.21+): the pins in use (`["GP10","GP11"]` or `null` = off), `source` (`settings` / `last-known-good` / `none`) and plain-text `problems[]`; non-empty problems = show "device setup incomplete" |
| `status` | `since` (int, optional) | `state`, `strikes`, `store` (`fram`/`nvm`), `drive_visible`, `mem_free`, `ip`, `modules[{type,uid,fw,bus}]` (`bus` = `A` / `B` / `usb`, code.py 0.21+), `power` (code.py 0.23+: the PicoHub's `status()` dict, `{error}` if it did not answer, or `null` without a PicoHub), `events[]`, `events_total` | phase-1 subset; DEV-36 adds firmware state |
| `roles.assign` | `role_id`, `module_type`, `exclude[]` | `uid`, `type`, `saved` / `timeout` | = `/roles/assign`; blocks ≤ 20 s |
| `wifi.scan` | — | `available`, `networks[{ssid,rssi,open}]` (strongest first, deduplicated, own hotspot left out, max 25) | code.py 0.22+, DEV-100. `available:false` = no list (scan failed): let the user type the name. code.py 0.25+ (DEV-130): on the hotspot this is the list scanned just BEFORE the hotspot started (`live:false`, `age_s`), never a live scan (a scan with the AP up can hang the brain); asking again returns the same list; since code.py 0.28 (DEV-136) the brain scans only once per boot, so a hotspot restart after a failed join keeps that list. On home WiFi `live:true` (fresh scan) |
| `provision` | `ssid`, `password`, `script_url`, `module_firmware`, `product_id`, `config_defaults`, `force`, `join_id` (optional, code.py 0.30+) | setup: `accepted`, `mode`, `join_id`, `uptime`; or `accepted:false`, `reason:"network_not_found"`, `networks[]`. Switch: `accepted`, `mode`, `rebooting` | on the AP = `/connect` (see *Transport* for the join feedback, DEV-100); on home WiFi = **product switch**: the network stays (a different `ssid` is refused — use setup mode), the request is parked, the brain reboots, downloads + compiles the new script **before** touching anything, and only on success replaces `product.py`, saves the new product and installs its `config_defaults`. A bad URL / dead uplink keeps product, settings and firmware as they were (`[CFG] product switch FAILED` event). |
| `reboot` | — | `rebooting: true` | flushes settings, replies, resets 0.5 s later |
| `factory_reset` | — | `resetting: true` | same wipe as the boot-hold gesture (both settings scopes too); the only reset path for a product with nothing pressable |
| `settings.get` | `scope` (`product` default / `device`) | `product`, `values`, `defaults`, `seq`, `dirty`, `info?`, `error?` | poll `seq` to pick up knob-driven changes — it bumps **in RAM on every change**, not when the record is written, so a knob turn or button press is visible within one poll (the app's 3 s). Tying it to the write made device-side changes show up only after 5–30 s. `info{id: value}` (noknok.py 1.11+, only when the product registered any) = read-only live values (config_schema type `info`, e.g. a module temperature), computed on every call; they never change `seq`, so compare `info` itself to refresh |
| `settings.set` | `scope`, `values{}` | `changed{}`, `rejected{key: reason}`, `values`, `seq` | values are validated against the declared defaults' types (+ `#RRGGBB` / `HH:MM` formats); an `info` id is rejected as `read-only`; `ok:false, error:"rejected"` when nothing was accepted |
| `settings.reset` | `scope` | `values`, `seq` | back to the product's defaults |

**Limits that protect the brain:** request body ≤ 4 KB (`body too large`); settings: ≤ 32 keys,
key ≤ 32 chars, string ≤ 256 chars, all values ≤ 1 KB JSON; scalars only (no arrays/objects).
**Write policy (nvm wear + power cuts):** a scope is written after 5 s without changes, at the
latest 60 s after the first change, never two writes closer than 30 s; forced before any
reboot / switch / crash reload. **Link:** the servicing slot watches the WiFi link every 30 s
(every 1 s while it is down) and re-joins with backoff (30 s → 5 min), also on a brain that
booted offline. Since noknok_rpc 0.2 (DEV-79) an attempt is a 0.5 s "kick" that lets the radio
finish the join in the background; every 3rd attempt is a full one (≤ 5 s blocking) as a
safety net (`LINK_FULL_EVERY`, 0 = kicks only). Once the link is up the carrier restarts and
code.py's post-join work runs (NTP with a 2 s budget, mDNS if enabled).
`status.link` reports `drops` / `rejoins` / `backoff`; an offline boot is not a drop.

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
*2 Oct 2026:* the libraries now ship as `.mpy` (README → *Precompiled libraries*) — that was
forced by a boot MemoryError, not by this bug. Whether the reload can go needs a cold-boot
test on CircuitPython 10.3.1 with the `.mpy` set; until then it stays.

### Known bug — no internet after the setup AP was up (DEV-47)
Once `code.py` has started the `noknok-setup` AP in a power session, the station loses its
default route: every off-subnet connection fails at once (`EHOSTUNREACH`, DNS `-2`) while
the LAN works. `stop_ap()` and soft reloads do not restore it; a hard reset does. Consequence
today: after any failed download / save the brain falls back to the AP and every later retry
in that session fails as "no internet". Retries must go through `microcontroller.reset()`.

### Settings — where they live (constraint from DEV-18)
**Settings values live in the runtime Store (FRAM / nvm), never in a file** — the product
reads them through `c.settings`. Convention: product-tagged,
`{"product":"<manifest-id>", ...values...}`; a product ignores values whose tag isn't its
own (stale after a switch). App-side, one blob per device. Design for the **nvm** backend —
**the only one** now that the FRAM board revision is cancelled (DEV-40): 79 ms blocking
write, finite endurance, and a single slot, so a power cut mid-write loses the whole record
(it self-heals to empty → settings fall back to the product's defaults, roles and state are
re-derived). Hence: write on change only, debounced on idle, never in a product's hot loop.
The `fram` branch stays in `noknok.store()` for a future board, but no shipping brain has it.

## Related on-device data

Since DEV-18 (15 Sep 2026) the brain **never writes its filesystem while a product runs**
— a power cut during any FAT write can destroy the filesystem on this platform. Runtime data
lives in the **Store** (`noknok.store()`: `microcontroller.nvm` on every shipping brain; an
optional maker-wired I2C FRAM at 0x50 is supported, opt-in with `NOKNOK_FRAM = 1`, see README *Store*);
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
