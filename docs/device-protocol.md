<!-- SPDX-FileCopyrightText: 2026 noknok (Christopher Houben) -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# noknok Device Protocol v1

The message protocol between a client (the noknok app, a script, `curl`) and a noknok
brain (Pico 2 W running `software/code.py`). It covers how a request is carried, which
operations exist, how settings work, and what is **not** built.

This page is written from the code (`software/noknok_rpc.py` 0.5, `software/code.py` 0.37,
`software/noknok.py` 1.23). Where it and the code disagree, the code wins.

**Where the details live** (one home per fact, not copied here):

| Topic | Home |
|-------|------|
| Every op: arguments, reply fields, limits, write policy, link watch | [provisioning-http-api.md](provisioning-http-api.md) |
| `config_schema` v2 (the settings schema inside a product manifest) | Ecosystem repo: `software/product-manifest.md` and `product-manifest.schema.json` |
| Writing a product that uses settings (`c.settings`, `c.sleep`) | Ecosystem repo: `software/authoring-products.md` |
| Env flags (`NOKNOK_MDNS`, `NOKNOK_WATCHDOG`, `NOKNOK_FRAM`) | [README.md](../README.md) |

## 1. Status

| Part | State |
|------|-------|
| `POST /rpc` carrier (setup hotspot and home WiFi), dispatcher | Shipped |
| `hello`, `status`, `settings.get/set/reset`, `roles.assign`, `provision`, `wifi.scan`, `reboot`, `factory_reset` | Shipped |
| `config_schema` v2 and `c.settings` (product and device scope) | Shipped |
| Authorisation of `/rpc` | **Not built.** `Dispatcher.authorize` exists as a hook and nothing sets it (see section 5) |
| `status`: per-module installed-vs-published firmware state | **Not built** (the op returns modules, events, strikes and link state only) |
| `clock.set` op and a `timezone` device setting | **Not built.** A `time` setting is declarable, but nothing manages the clock behind it (online brains get NTP) |
| BLE carrier, `file.put`, `module.update`, `pair` ops | **Not built, not planned** (BLE was skipped for the launch brain) |
| Unsolicited device-to-client messages (`event`) | **Not built.** HTTP has no push; clients poll |

## 2. Messages

One JSON request and one JSON reply, independent of the carrier.

```
-> {"id": 7, "op": "settings.set", "args": {"scope": "product", "values": {"color": "#FF8040"}}}
<- {"id": 7, "ok": true, "scope": "product", "changed": {"color": "#FF8040"}, "rejected": {}, "values": {...}, "seq": 12}
<- {"id": 8, "ok": false, "error": "unknown op: foo"}
```

- `id`: chosen by the client, echoed in the reply. `op`: the operation name. `args`: an
  object (default `{}`).
- `token`: reserved in the message format and **ignored today**.
- A reply is `{"id", "ok": true, ...fields}` or `{"id", "ok": false, "error": "<code>", "detail"?}`.
- Error codes the dispatcher itself produces: `unknown op: <name>`, `bad args` (args not an
  object), `bad message` (body is not a JSON object), `bad json` (body not parseable, `id` is
  `null`), `body too large` (over 4096 bytes, `id` is `null`), `unauthorized` (only if an
  `authorize` hook is installed), `internal` (a handler raised; `detail` holds the exception).
  Handlers add their own, for example `rejected`, `bad scope`.
- `hello` lists the ops a given brain serves (`ops[]`); use it rather than assuming.

## 3. Carrier

Only one carrier exists: HTTP.

- `POST /rpc`, body `application/json`, port 80. The reply is `application/json`.
- Served on the setup hotspot (`http://192.168.4.1`) and on the home WiFi for the product's
  whole lifetime.
- The brain is found on the home network by sending `hello` to the addresses of the client's
  /24, or by name: `noknok-XXXX.local` (mDNS, last two bytes of the MCU id) is advertised
  **by default** since code.py 0.36 and switched off with `NOKNOK_MDNS = 0` in `settings.toml`.
- The older form endpoints (`/connect`, `/roles/assign`, `/roles/detect`, `/roles/save`) are
  adapters over the same handlers, kept for the captive-portal page. New clients use `/rpc`.
- Request bounds: body at most 4 KB; one request must complete within about 1 s
  (`REQUEST_DEADLINE`) or it is dropped, so a slow or half-closed client cannot hold the
  product loop.
- A second carrier would feed the same `Dispatcher`; nothing in the dispatcher is HTTP-specific.

## 4. Operations

Names and one-line purpose. Arguments and reply fields: [provisioning-http-api.md](provisioning-http-api.md).

| op | Purpose |
|----|---------|
| `hello` | Device id, name, state (`unprovisioned` / `provisioned` / `parked`), product, component versions, pin configuration, last setup-join result, op list. Open and cheap: call it first |
| `status` | State, crash strikes, Store backend, free memory, IP, modules, power-manager status (PicoHub only), recent events, link counters |
| `settings.get` / `settings.set` / `settings.reset` | Read, merge, reset to defaults; `scope` is `product` (default) or `device` |
| `roles.assign` | Wait up to 20 s for the customer to touch a module, save it under a role id |
| `wifi.scan` | List visible networks (on the hotspot: the list scanned before the hotspot started) |
| `provision` | On the hotspot: hand over home-WiFi credentials and the product. On home WiFi: switch product (downloads and compiles before replacing anything) |
| `reboot`, `factory_reset` | Flush settings and reset; wipe everything including both settings scopes |

## 5. Authorisation: none today

**Every op is open to anyone who can reach port 80**, on the setup hotspot and on the home
LAN. That includes changing settings, switching the product and `factory_reset`.
`Dispatcher.authorize` (a function taking the message and returning a bool) is the single
place where authorisation would be added; the code never installs one, and `token` is not
checked. Authorising `/rpc` is tracked as DEV-35; this page will change when it lands.

Related, by design: during setup the home-WiFi password crosses the open hotspot in the
clear inside the `provision` message.

## 6. Settings

**The product manifest carries the schema; the device carries the values.** The app reads the
`config_schema` from the manifest, renders a page, and talks to the device only in values.
The brain never needs the schema.

- **Source of truth is the device.** A knob or the product itself can change a value too; the
  client polls `settings.get` and compares `seq`. `seq` bumps in RAM on every change, not
  when the record is written, so a device-side change is visible within one poll. Last write
  wins.
- **Validation:** the brain checks each value against the type of the product's declared
  default (plus `#RRGGBB` for colours and `HH:MM` for times) and rejects the rest in
  `rejected{key: reason}`. Ranges (`min`/`max`/`options`) are enforced by the client and
  clamped by `product.py`; the brain does not know them. `info` ids are read-only.
- **Limits:** at most 32 keys per scope, key 32 characters, string 256 characters, all
  values of one scope 1 KB as JSON; scalars only.
- **Scopes:** `product` (belongs to the installed product) and `device` (survives product
  changes, wiped only by factory reset). The device scope exists so generic settings can be
  added later without a protocol change; no device-scope key is defined yet.
- **Storage:** the runtime Store (`microcontroller.nvm`), never a file. A scope is written
  after 5 s without changes, at the latest 60 s after the first change, never twice within
  30 s, and forced before a reboot or switch. A power cut during the write loses the whole
  record and the settings fall back to the product's defaults.
- **Lifetimes:**

| Event | Result |
|-------|--------|
| Same product id reinstalled | Values kept; new schema entries get defaults, removed ones drop |
| Different product installed | Replaced by the new product's `config_defaults`, written at install time |
| Same product, new major id (`-v2`) | A different product, so defaults |
| `settings.reset` | Defaults rewritten |
| Factory reset | Everything wiped, both scopes |

- **`config_schema` types** (`toggle`, `slider`, `color`, `select`, `text`, `time`, `info`):
  see the Ecosystem manifest spec. `time` is a plain `"HH:MM"` string; the brain has no
  battery-backed clock (see section 1).

## 7. Staying reachable while a product runs

CircuitPython runs one thing at a time, and after setup that is the product's `while True:`
loop. There is no asyncio and no thread. The channel is serviced inside the library:
every module read and write in `noknok.py` first calls `service()`, which is non-blocking and
throttled to one pass per 50 ms. Typical cost: about 1 ms idle, 10 to 100 ms to answer a
request. A product that sleeps for long should call `c.sleep(seconds)`; otherwise replies wait
for its next module access. A handler that talks to a module cannot re-enter the service pass;
work that must follow a reply (reboot, `on_change` callbacks) is queued and runs afterwards.
A brain with no WiFi has no channel until its automatic rejoin succeeds (retried every 30 s up to
5 min apart, also while the product runs).

Radio note for client authors: the Pico's WiFi power saving is switched off at server start.
In the chip's default mode a phone's headers and body arrive hundreds of ms apart and short
reads see an empty body (`bad json`). `curl` never reproduces it; test from a real phone.

## 8. Not covered here

- Provisioning flow narrative and what happens after the reset: [provisioning-http-api.md](provisioning-http-api.md).
- Module firmware updates: they happen in the brain's own pass after it joins WiFi, not
  through `/rpc`.

Bench tools: `tools/rpc_call.py <host> <op> ['{json args}']`; `software/bench_rpc_product.py`
is the smallest product that exercises the channel.
