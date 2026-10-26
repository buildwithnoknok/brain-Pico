# code.py — noknok Pico W provisioning + launcher
# Version: 0.37 (safe mode no longer leaves the lamp dark — DEV-154)
#
# v0.37 changes (Sue): DEV-154 — new safemode.py: when CircuitPython starts in
#   safe mode after a core crash (hard fault, brownout, ...), code.py never runs
#   and the lamp stayed dark until a replug. safemode.py now restarts the brain,
#   at most 2 times in a row (count in nvm[6], reason in nvm[7:23]); then it stays
#   in safe mode (USB recovery possible, no reboot loop). code.py records each such
#   restart as a [SAFE] event and resets the count after a real power-on or 10 min
#   of the product running. Also: the [WDT] event is no longer logged a second time
#   on the reload after an update pass (ADR-006 note).
#
# v0.36 changes (Sue): DEV-70 — mDNS (noknok-XXXX.local) is ON by default: a
#   missing NOKNOK_MDNS key now means on, NOKNOK_MDNS = 0 switches it off. The
#   one idle hang seen with mDNS on (17 Sep 2026) never came back in three full
#   bench nights with it on (12 h + 8 h + 12 h, 22-26 Oct, no hang, no reset).
#
# v0.35 changes (Sue): DEV-150 — setup refused the home WiFi ("not visible")
#   on every try when the one scan before the hotspot had missed it, until a
#   reboot (1 of 2 boots on the bench). Now the scan before the hotspot makes
#   SCAN_PASSES (2) passes and merges them, and a name refused as not visible
#   is tried for real when it is asked for again in the same boot (a typo then
#   fails as not_found, the normal DEV-100 path). No scan with the hotspot up
#   (DEV-130) and none after a failed join (DEV-136), as before. Messages
#   between app and brain unchanged.
#
# v0.34 changes (Sue): DEV-106 — _stage1_pass() no longer sets m.bootloader +
#   saves the state itself after stage1_update(): noknok.py 1.20 does that inside
#   stage1_update() for every caller. Needs noknok.py >= 1.20 (shipped together).
#
# v0.33 changes (Sue): DEV-113 bench fix — wifi.radio.ap_info raises
#   NotImplementedError on the Pico 2 W, which swallowed the whole join log line
#   ("link details unavailable"). Gateway + DNS are now always logged, the access
#   point details only where CircuitPython has them.
#
# v0.32 changes (Sue): DEV-113 — a brain joined the home WiFi but got 192.168.4.16
#   with no DNS (no NTP, no OTA, unreachable). 192.168.4.16 is the first lease of
#   the CircuitPython/MicroPython setup-hotspot DHCP server, which listens on every
#   interface: a device whose hotspot DHCP server outlives its hotspot answers DHCP
#   on the home network too. Now: every join logs gateway, DNS and the access point
#   (BSSID, channel, RSSI); a 192.168.4.x lease with gateway 192.168.4.1 is rejoined
#   up to ROGUE_LEASE_TRIES times, then kept with a [WIFI] event; and the brain's own
#   hotspot is stopped together with its DHCP server (_stop_ap, stop_dhcp_ap() where
#   CircuitPython has it), so a brain cannot be the one handing those leases out.
#
# v0.31 changes (Sue): DEV-79 — a brain that booted while the router was down
#   stayed offline until a power cycle in practice: the rejoin in noknok_rpc's
#   servicing slot existed, but each attempt froze the product for 5 s and the
#   post-join work never ran. With noknok_rpc 0.2 most attempts only start the
#   join (0.5 s) and the link is picked up in the background; once online,
#   _after_join() syncs the clock (NTP, 2 s budget) and starts mDNS when
#   NOKNOK_MDNS = 1. The /rpc carrier comes up as before. The module firmware
#   check stays a boot-time check (next due boot), as on a brain that booted
#   online.
#
# v0.30 changes (Sue): DEV-141 — with the RIGHT password the app showed
#   "Something went wrong" although the brain joined and saved: the provision
#   reply was still in flight when the hotspot dropped 1 s later (one lost frame,
#   lwIP retransmits after ~3 s), and the reset after the join lost it for good.
#   Now the hotspot stays up until the app acks the reply (hello args.ack) or
#   REPLY_GRACE_S (5 s) passes; provision takes an app-chosen join_id, and
#   hello.setup.received names the last accepted one, so the app can follow a
#   join even when the reply is lost.
#
# v0.29 changes (Sue): DEV-139 — download_and_save_script() used response.text,
#   which needs the whole product twice in one block each (bytes + str: 2 x 36 KB
#   for Smart Lamp Maxi). After setup the heap is fragmented and the download
#   failed on EVERY boot ("allocating 36027 bytes"): provisioned, no product.
#   Now the body is streamed to product.py.tmp in 1 KB pieces, checked against
#   Content-Length (truncated -> retry), compile-checked from the file (a
#   MemoryError there is logged and skipped; a SyntaxError still refuses) and
#   renamed over product.py.
#
# v0.28 changes (Sue): DEV-136 — 0.25 scanned in station mode before EVERY
#   start_ap(); right after a router "Authentication failure" that scan hung
#   for good (2 of 2), so noknok-setup never came back. Now the brain scans
#   ONCE per boot, before the first start_ap() on a fresh radio; a hotspot
#   restart after a failed join reuses that list (_scan_before_ap()).
#
# v0.27 changes (Sam): DEV-133 / DEV-134 — the Pico PIO-USB host does not see a
#   USB module re-enumerate within one host session (DEV-45); a fresh host
#   always does. A LEDs 16x update (0xB0, then waiting for PID 4E42) therefore
#   failed and left the module in its bootloader, and after a PicoHub X035
#   flash or rescue (J2 VBUS off while it is in its bootloader) the USB module
#   was gone until a hard reset. Now: a USB update only asks the module into
#   its bootloader ("PENDING", noknok.py 1.18); every boot that rescues runs
#   Conductor.usb_fw_pass() after enumeration, which flashes any module found
#   at PID 4E42 from the cache (= USB rescue) and verifies the version one boot
#   later; and the brain HARD-resets instead of the DEV-132 soft reload before
#   the product whenever a USB module was asked/flashed or the X035 was flashed
#   or rescued on a brain with USB host pins. The nvm[5] marker still skips the
#   pass after a reset that followed an online pass. Each hard reset costs one
#   boot (~25 s), only on a day an update or rescue happens.
#
# v0.26 changes (Sue): DEV-132 — after a boot whose daily OTA check was due
#   (TLS session, registry/index fetches, downloads, flashing) a large product
#   (Smart Lamp Maxi, 36 KB) failed with MemoryError seconds after it started
#   (strike 1/3, 5 of 6 such boots): the heap was fragmented, ~35 KB free vs
#   75-100 KB. Now, once the pass went online, the brain plays any alert and
#   then reloads once (fresh VM) before the product; a marker byte in nvm[5]
#   makes that run skip the pass (cache rescue only), so it can never loop.
#
# v0.25 changes (Sue): DEV-130 — a WiFi scan with the setup hotspot up could
#   block forever inside CircuitPython's scan iterator and freeze the web server
#   (setup dead until a power cycle). The brain now scans ONCE in station mode
#   right before each start_ap() (boot and every hotspot restart after a failed
#   join) and keeps that list: wifi.scan and the provision pre-check read it
#   while the AP runs (wifi.scan adds live:false + age_s); _scan_networks()
#   refuses outright with the AP up and stops collecting after 10 s.
#
# v0.24 changes (Sam): DEV-124 — the carrier's own firmware joins the module OTA
#   pass. Products never list the board they sit on, so check_and_flash_modules()
#   always adds CARRIER_FIRMWARE ("picohub", no floor) to the manifest's types:
#   its index.json is resolved, the image cached, and the power manager updated
#   (and rescued when parked) like any module. A brain without a PicoHub only
#   pays one cached download per published version. The stage-1 pass skips the
#   PicoHub (STAGE1_OWN_LINE): its CH32X035 stage-1 has its own version line, so
#   comparing it with the published CH32V003 stage-1 would raise a daily alert.
#
# v0.23 changes (Sue): DEV-123 — the status op carries `power`: the noknok
#   PicoHub's status (charger contract, input, 5 V rail, temperature, rails,
#   faults, warnings) when the brain sits on one, else null. One I2C read per
#   status call, nothing polled in the background. Modules without a driver
#   are left out of `modules` (they used to show as "nonetype").
#
# v0.22 changes (Sue): DEV-100 — /connect and the provision op used to answer
#   "accepted" at once and the app showed Done even when the join then failed.
#   Now: (1) new wifi.scan op lists the networks the device can see; (2) provision
#   refuses a network that is not visible (reason network_not_found, hotspot
#   kept; force=true for a hidden network); (3) it answers with a join_id, and a
#   failed join is remembered in RAM as setup.last_join {join_id, ok, reason:
#   auth / not_found / timeout / other / not_saved} in hello once the hotspot is
#   back; (4) a wrong password stops retrying at once; (5) a failed join plays
#   the module alert and blinks the Pico's LED. The browser page says
#   "Connecting...", not "Connected!".
#
# v0.21 changes (Sue): DEV-74 — no hard-coded GPIOs. noknok.py 1.15 takes the
#   I2C bus A / bus B / USB host pins only from settings.toml (with a last-known-
#   good copy in the Store). At boot the brain logs the pins in use and reports
#   every problem: console, a [CFG] event (only when the problem changes), the
#   Pico's own LED, and a new `config` block in the hello op. A configured bus
#   that does not come up is reported the same way. _release_conductor() and the
#   boot-hold reset now release BOTH buses (Conductor.deinit()).
#
# v0.20 changes (Sue): DEV-97 — a WiFi boot whose daily OTA check is due used
#   to return early with NO parked-module rescue when the manifest has no
#   module_firmware, the HTTPS session failed or nothing resolved (GitHub
#   unreachable). Those paths now run the same cache rescue as offline and
#   "not due" boots (_rescue_from_cache). A module that stays parked is
#   reported loudly: a "MODULE STILL PARKED" log line, the [RESCUE] event in
#   the Store history (status op), and the buzzer/LED alert; every later boot
#   retries. No new cache: the OTA-time cache from 0.14 is the image source.
#
# v0.19 changes (Sue): DEV-99 — removed POST /firmware/check and the
#   firmware.check op. At AP time the brain has no internet and manifests carry
#   only floors ({"min"}), so it could never answer anything but
#   update_needed:false and the app's "update available" banner never showed
#   (the app 1.5.3 no longer calls it; an older app gets 404 and shows nothing,
#   as before). Module updates happen silently in the post-WiFi OTA pass
#   (check_and_flash_modules), which is unchanged.
#
# v0.18 changes (Sue): factory reset by BOOT-HOLD.
#   - Hold any LED Button or Knob button while plugging in the power and keep
#     holding: once the modules are found (~5 s) every LED Button lights white
#     and a buzzer (if any) clicks — "I see you"; hold 3 s more and the LEDs
#     flash 3x and go DARK, the buzzer plays two rising notes, and the brain
#     wipes + reboots into the setup AP. Dark = accepted, let go. Release
#     earlier and the boot carries on untouched.
#   - Runs ONLY on the power-on run (the one the cold-boot workaround reloads
#     anyway) and BEFORE the reload, so the Conductor it needs never exists in
#     the process that later downloads — the DEV-32 rule holds. Costs ~3-4 s
#     per power-on, and nothing on a device with no credentials and no product
#     (nothing to reset, so the check is skipped).
#   - Why: products no longer have to reserve a gesture for the reset. The
#     Smart Lamp Mini has one button and both of its gestures are taken; the
#     runtime knob-hold (Conductor.check_factory_reset) stays available for
#     products that still want it. A product with nothing pressable resets
#     from the app (the factory_reset op).
#
# v0.17 changes (Sue): DEV-34 phase 1, the protocol dispatcher.
#   - One message protocol (noknok_rpc.py, Confluence 113868802 §2): every
#     handler is an op on a transport-agnostic Dispatcher — hello, status,
#     roles.assign, firmware.check, provision, reboot, factory_reset (the
#     settings.* ops arrive with c.settings). The HTTP routes the app uses
#     today (/connect, /roles/assign, /firmware/check) are thin adapters over
#     the same functions; behaviour unchanged.
#   - POST /rpc served on the setup AP AND on home WiFi for the product's whole
#     lifetime, advertised as noknok-XXXX.local (mDNS). The app can finally
#     reach a provisioned brain: hello answers with state/product/versions,
#     status with the event history and crash strikes. Servicing is implicit —
#     noknok.py's drivers pump the channel between module transactions
#     (noknok.set_service_hook), so product.py needs no change; the parked
#     safe_idle loop pumps it too. Soak-proven 17 Sep 2026 (DEV-34 comment
#     10829): ~1 ms idle, no hang after a Conductor exists.
#   - provision over home WiFi = product switch: save the new script_url
#     (+ product_id), drop product.py and hard-reset; the normal boot path
#     downloads the new product BEFORE any Conductor exists (the DEV-32 rule).
#     No download ever happens from a handler.
#   - /connect and provision accept an optional product_id (the manifest id),
#     kept in wifi.json — the app needs it to fetch the right config_schema.
#
# v0.16 changes (Sue): DEV-18. Bench-proven 15 Sep 2026 that on RP2 flash NO
#   FAT write can be made power-safe: the driver rewrites whole 4 KB blocks in
#   place, so a cut during any write can wipe the directory (3 pulls, 3
#   losses, one total). Unplugging is how a product is switched off, and loose
#   cables do the same. So the brain no longer writes the filesystem while a
#   product runs — at all:
#   - Runtime data lives in the Store (noknok.store(): microcontroller.nvm,
#     self-healing; an I2C FRAM at 0x50 is an optional backend that no
#     noknok board fits - DEV-40 was cancelled): module state, roles, event
#     history, a credentials copy, and product settings (DEV-34).
#     noknok_events.txt is gone; event() appends to the Store ring,
#     events() reads it.
#   - Filesystem writes happen only at setup and OTA — wifi.json, product.py,
#     the firmware cache — into /data, whose directory block is not the one
#     naming code.py / noknok.py / lib. Legacy root files are still read.
#   - wifi.json is restored from the Store copy if a setup-time cut took it.
#   - Enumeration keeps a known module's previous address (noknok.py), so the
#     state no longer changes — and is no longer written — on every power-on.
#   Earlier in v0.16, still true:
#   - boot.py hides the drive from PCs (settings.toml NOKNOK_USB_DRIVE = 0)
#     and remounts the filesystem READ-ONLY to the program. Any stray write
#     raises OSError — the OS enforces the policy. Fail open: a brain whose
#     code.py / noknok.py / settings.toml are missing shows the drive again.
#   - Every write goes through noknok.writable() (short remount-rw window,
#     synced and closed) and noknok.write_atomic() (temp file + rename, the
#     old file survives a power cut mid-write): wifi.json, product.py, the
#     firmware cache, roles, state. Orphan .tmp files are cleaned at boot.
#   - product.py is compiled BEFORE it replaces the running copy, so a
#     truncated download can never take a working product down.
#   - Windows exist only during provisioning, OTA, roles and factory reset —
#     the first seconds after boot — never while product.py runs.
#   - Pins and drive visibility come from settings.toml (makers' file):
#     NOKNOK_I2C_SDA/SCL/FREQ, NOKNOK_USB_DP/DM, NOKNOK_USB_DRIVE.
#
# v0.15 changes (Sue): the field-update path for the BOOTLOADER itself.
#   Until now Conductor.stage1_update() existed and was bench-proven, but
#   nothing in the field could trigger it: no brain knew what the current
#   stage-1 was, and code.py never called it. Now the registry names the
#   stage-1 index (module-I2C-bootloader/firmware/index.json), the OTA pass
#   caches the stage-1 image like any other, and _stage1_pass() brings every
#   I2C module up to it BEFORE the app pass — same-layout only (a cross-layout
#   stage-1 is refused by the module, error 8, so it is refused here first),
#   restoring the module's current app from the cache in the same transaction.
#   Each module's stage-1 version is read once (a bootloader round-trip) and
#   remembered in noknok_state.json ("bl"), so later checks are a free compare.
#   Legacy monolithic bootloaders cannot self-update and are logged once.
#
# v0.14 changes (Sue): DEV-32 pass 2.
#   - The OTA pass asks GitHub at most once per 24 h (last-check time in
#     microcontroller.nvm, no filesystem write, survives power cycles). Other
#     boots make no round-trips; the parked-module rescue still runs, from the
#     on-device cache. (On a connected boot whose check IS due, the rescue is
#     skipped if the manifest has no module_firmware, the HTTPS session fails
#     or nothing resolves; see check_and_flash_modules.) The first connected boot after provisioning also warms
#     that cache for every I2C type the product uses, so any module is
#     rescuable offline from day one — not only the ones updated that day.
#   - The layout gate refuses per MODULE, not per type: one older spare no
#     longer blocks the rest of its type. update_all() takes exclude_uids.
#   - Registry and index carry an optional "format"; a newer format than this
#     brain understands is skipped (fail closed).
#   - The customer hears about it: any refused/failed update or failed rescue
#     plays the buzzer error motif and flashes the LED Buttons red before the
#     product starts. Interim until the app can show device status over the
#     home network (DEV-32 E10 / DEV-27).
#
# v0.13 changes (Sue): the four failure modes from the 12 Sep field review
#   that turn "works on the bench" into "keeps working at home".
#   - WiFi credentials are never deleted by a failed join. A router hiccup used
#     to wipe wifi.json after three attempts and drop into AP mode, forcing a
#     full re-setup — and the product would not run until then. Now: offline,
#     the product runs anyway (no OTA, no NTP); credentials are retried on the
#     next boot; only the factory-reset gesture deletes them. Boot-time budget
#     on the OTA pass (registry 6 s, index 8 s): a slow uplink costs seconds,
#     not the sum of every timeout, and the product starts either way.
#   - product.py crash recovery, three strikes. It used to run bare: an
#     exception ended code.py at CircuitPython's "Code done running" prompt —
#     dead until a power cycle, deterministic crash = dead forever, and the
#     factory-reset gesture unreachable (DEV-7). Now: reload with backoff,
#     strike count in microcontroller.nvm (not the FAT filesystem), cleared on
#     a power-on start (supervisor.runtime.run_reason); on the third strike a
#     clean boot parks in a safe idle that still answers the knob-hold reset and
#     tries a freshly published product.py once if online.
#   - Flash writes cut to what earns them. log() used to append to log.txt on
#     every line — "waiting for setup" every 5 s — the single largest write load
#     on an unjournaled FAT filesystem that corrupts on power loss (DEV-18).
#     Now: RAM ring + serial; log.txt only with the bench marker /debug_log
#     present, or one flush on a crash. _save_state() (noknok.py) skips the
#     write when nothing changed. Events file unchanged (rare, audit).
#   - Downloaded images are integrity-checked against index.json size + crc32
#     before touching a module. The bootloader's own CRC cannot catch a short
#     download — it is computed over whatever we send. Checked if present in
#     the index; warned about if absent.
#   - Fetched images are kept as an on-device cache (/fw_<type>.bin + .json
#     sidecar with version/layout/size/crc32; moved to /data/fw_<type>.* by
#     DEV-18 / ADR-003) instead of deleted, so a module
#     parked after a power cut is rescued with no internet, from the Pico
#     itself. Same layout check; sidecar integrity re-checked on read.
#
# v0.12 changes (Sue): DEV-31 app-side hardening.
#   - Firmware resolution via the module registry. Manifests no longer pin a
#     version + .bin URL; they declare a floor ({"buzzer": {"min": "3.3.1"}}).
#     _resolve_module_firmware() fetches Ecosystem/software/modules.json to map
#     each module type to its repo, then that module's firmware/index.json for
#     the current {version, url, layout}. Firmware is backwards
#     compatible, so we install what is current rather than what the product was
#     written against; the floor only catches a product published against an
#     unreleased firmware. The version lives in the same commit as the binary,
#     which is what stops the two drifting apart (they did — see DEV-31).
#     Every failure path is a safe no-op: an unresolvable type is left out, so
#     firmware_report() sees no required version and flashes nothing.
#   - _bootloader_gate() refuses an image the module cannot run. Backwards
#     compatibility is a promise about the PROTOCOL, not about installability:
#     the Sep 2026 relink to app base 0x1400 is wire-compatible and still
#     hard-faults a module whose bootloader writes apps at 0x1000, and nothing
#     downstream would catch it (the flasher writes at an offset the bootloader
#     picks, and the CRC is over image bytes, not the link address). The index
#     declares a numeric flash `layout` and the match is EXACT, via
#     Conductor.bootloader_layout() (0 = legacy, 1 = stage-1 1.0.x @0x1000,
#     2 = stage-1 1.1.x @0x1400). A first version of this gate only told legacy
#     from stage-1; both layouts answer 0xB1, so it passed a layout-2 image to a
#     layout-1 buzzer on the bench (12 Sep) and hung it. Fails closed on a
#     missing layout or an unknown stage-1 version. The rescue path applies the
#     same check before pushing an image onto a parked module.
#   - POST /firmware/check now reports resolved:false. It runs at AP time with
#     no internet, so it cannot reach the registry and can only say what is
#     installed. The real check is the headless post-WiFi pass.
#   - Parked-module rescue (Sam's hardening D). A module stuck in its bootloader
#     at 0x7E does not answer the enumeration sweep, so before this it was simply
#     invisible: the product started with a module missing and nothing said why.
#     get_conductor() now takes an optional rescue_get_image and runs
#     Conductor.rescue_parked_module() between Conductor() and enumerate_all() —
#     the order Sam specifies, because a rescue after enumeration would be too
#     late to put the module back in the registry. Outcomes go to the durable
#     audit log as [RESCUE]. The AP-time role endpoints pass no image source and
#     skip it; only the post-WiFi path can actually fetch an image.
#   - check_and_flash_modules() reordered into decide / fetch / flash:
#       1. version check only — no network, no filesystem writes. The common
#          boot ("all modules up to date") now ends here having touched nothing.
#          It previously downloaded every manifest image on EVERY boot and wrote
#          them to flash before asking whether anything was out of date, which
#          is the per-boot-write pattern behind the FS corruption in DEV-18.
#       2. release the Conductor, then fetch every needed image to the FS.
#       3. flash from those files, then delete them.
#     The point of fetching everything first is atomicity: once the first module
#     is erased no later step needs the radio, so a WiFi drop mid-run can no
#     longer leave one module half-written and the rest untouched. Releasing the
#     Conductor first also means the downloads run with RAM and the I2C bus free.
#
# v0.11 changes (Sue): DEV-12 — wire update_all() into provisioning, both buses.
#   - get_conductor() now calls enumerate_all() (I2C + USB) instead of enumerate()
#     (I2C-only). Without this, a USB-only product's module (e.g. the desk lamp's
#     USB LEDs) never had a firmware_report() entry at all — its OTA was silently
#     skipped both at /firmware/check and at the headless flash below.
#   - check_and_flash_modules() no longer talks to module_flasher.ModuleFlasher
#     directly (I2C-only). It now calls Conductor.update_all(manifest_fw, get_image,
#     progress, logfn) from noknok.py — the bus-aware dispatcher Sam shipped
#     (commit 774c115) that routes each outdated module to the right flasher
#     (I2C ModuleFlasher / USB UsbModuleFlasher) and re-enumerates afterwards.
#     get_image() is the one piece deliberately left WiFi-specific here — it
#     downloads entry['url'] over the existing radio session — everything else
#     lives in noknok.py so it stays bench-testable (see update_demo.py).
#   - Progress is log-only for this pass (log.txt via the progress callback) —
#     there's no live channel back to the phone during the headless flash (it's
#     already off the noknok-setup AP by then). A future GET /status + mDNS
#     endpoint (see poc-private/docs/firmware-change-for-sam.md) would be a
#     separate task if live in-app progress is wanted later.
#   - Outcomes logged to log.txt (verbose, bench) + the Store event history (durable audit).
#
# v0.10 changes (Sue): module-firmware version check + OTA (DEV-1 / PoC v2 Step 5).
#   - POST /firmware/check (AP time): the app sends the manifest's module_firmware{};
#     the Pico enumerates, reads each module's installed version (GET_VERSION 0xB1
#     via noknok.py) and returns {update_needed, modules[]} so the app can show a
#     "firmware update available" notice before /connect. No flashing here.
#   - /connect now also accepts module_firmware (JSON) and persists it in wifi.json.
#
# v0.8 changes (Sam): app-driven role assignment ("PoC v1 Step 3"). Two new
#   routes on the AP HTTP server let the noknok app assign roles to physical
#   modules while the phone is still on the noknok-setup AP (before /connect):
#     POST /roles/detect  — ask the customer to interact with a module type and
#                           return the UID of the one they touched (or timeout).
#     POST /roles/save    — persist a role_id -> UID mapping to noknok_roles.json.
#   A Conductor (from noknok.py) is created + enumerated lazily on the first
#   /roles/detect call and cached, so the ~3 s enumeration happens only once.
#   The handlers are transport-agnostic (no AP-specific logic) so they could be
#   served on home WiFi later unchanged. /connect and the provisioning flow are
#   unchanged — the app calls the role endpoints BEFORE /connect.
#
# v0.5 changes (Sam):
#   - log() now timestamps every line with monotonic uptime [  12.34], and
#     after a successful WiFi connect does a one-time NTP sync so lines also
#     carry wall-clock HH:MM:SS. NTP is wrapped in try/except — never crashes boot.
#   - Instrumented + hardened the normal (STA) boot download path so we can SEE
#     where it fails: explicit branch logging, script_url source logging, and
#     2-3 retries on the direct-boot WiFi join before falling back to AP.
#   - NOTE: NTP needs adafruit_ntp.mpy in /lib. If absent, logging still works
#     (uptime only) — wall-clock is simply skipped.
#
# Boot logic (summary; the full flow incl. OTA, crash recovery and factory
# reset is drawn in the architecture repo, flows/provisioning.md):
#   1. Credentials found (/data/wifi.json, legacy root wifi.json, else the
#      Store copy) -> join home WiFi (3 tries)
#        - Joined, no product.py yet -> download script_url, compile-check,
#          write /data/product.py atomically, reload
#        - Joined, product.py present -> module OTA pass if due, run product.py
#        - Join failed -> credentials are NEVER deleted (since 0.13): with
#          product.py present the product runs offline; without it the setup
#          AP is offered, credentials kept
#   2. No credentials -> AP provisioning:
#        - Start hotspot "noknok-setup" (open network)
#        - Serve the setup form, the form routes (/connect, /roles/*)
#          and POST /rpc at 192.168.4.1 via adafruit_httpserver
#        - Captive-portal probe paths serve the setup page so the OS shows "Sign in"
#        - On /connect -> reply "Connected!" at once (before the join or any
#          save, DEV-100) -> stop AP -> try home WiFi -> on success save creds +
#          settings defaults and hard-reset (the download happens on the next
#          boot, step 1)
#        - On WiFi failure -> restart AP so the user can retry (the app is not
#          told, DEV-47)
#
# IMPORTANT (development): the CYW43 radio is NOT reset by a soft reboot
# (Ctrl+D / supervisor.reload). AP mode only works reliably after a full
# POWER CYCLE. Always unplug/replug the Pico when testing AP provisioning.
#
# Required libraries in /lib:
#   adafruit_httpserver/   (folder)
#   adafruit_requests.mpy
#   adafruit_connection_manager.mpy

# noknok FIRST, while the heap is still empty and unfragmented: compiling a
# large .py (noknok.py is ~190 KB of source) needs big contiguous blocks for the
# parse tree. Imported after the network libraries it hit a MemoryError at boot
# with 325 KB free (2 Oct 2026) — the brain never reached its WiFi. Shipping
# precompiled .mpy (tools/build_mpy.ps1, DEV-38) avoids the on-device compile.
import noknok as nk          # filesystem policy helpers (DEV-18) + settings.toml
import noknok_rpc as rpc     # Device Protocol v1: dispatcher + /rpc carrier (DEV-34)
import gc
import json
import os
import sys
import time
import supervisor
import microcontroller
import wifi
import socketpool
import ssl
import adafruit_requests
import adafruit_connection_manager
from adafruit_httpserver import Server, Request, Response, POST

CODE_VERSION = "0.37"

LOG_FILE    = nk.DATA_DIR + "/log.txt"        # bench only (marker), see below
EVENTS_KEY  = "events"     # audit history ([FW]/[CRASH]/[ROLE]/[RESET]...) lives in the
EVENTS_MAX  = 40           # Store (FRAM / nvm), never on the FAT — DEV-18. Last N lines.

# Wall-clock availability. The Pico 2W has no battery-backed RTC, so on every
# boot we only know uptime (time.monotonic). After a successful WiFi connect we
# try a one-time NTP sync; if it works, _rtc_synced flips True and log lines
# additionally carry HH:MM:SS. Until then (and if NTP fails) we log uptime only.
_rtc_synced = False

def _timestamp():
    """Build the log-line prefix.
    Always: monotonic uptime, e.g. '[  12.34]'.
    After NTP sync: also wall-clock, e.g. '[  12.34 14:03:09]'.
    Must never raise — logging has to survive any clock state."""
    try:
        up = time.monotonic()
        prefix = "[%8.2f" % up
        if _rtc_synced:
            try:
                t = time.localtime()
                prefix += " %02d:%02d:%02d" % (t.tm_hour, t.tm_min, t.tm_sec)
            except Exception:
                pass
        return prefix + "] "
    except Exception:
        return "[ ?.??] "

# Flash-write policy for the verbose log. CircuitPython's FAT filesystem is not
# journaled; a power cut during a write can corrupt it, and a corrupt filesystem
# means the customer's Pico needs a USB reflash (DEV-18). The verbose log used to
# append to flash on EVERY line — including "waiting for setup" every 5 s — which
# made it the single largest write load on the device. Now: every line goes to
# the serial console and a RAM ring; it reaches log.txt only if the bench has
# created the marker file below (and then also gets a ring flush on a crash, so
# the last lines before a failure survive a power cycle). The event history
# (rare, audit) lives in the Store, not in a file.
DEBUG_LOG_MARKER = "/debug_log"        # bench: `pico.py put` an empty file of this name
LOG_RING_LINES   = 80
_log_ring        = []
_log_to_flash    = None                # decided on first log() call

def _flash_logging_enabled():
    global _log_to_flash
    if _log_to_flash is None:
        try:
            os.stat(DEBUG_LOG_MARKER)
            _log_to_flash = True
        except OSError:
            _log_to_flash = False
    return _log_to_flash

def log(msg):
    """Print to the serial console and keep the last LOG_RING_LINES lines in
    RAM. Appends to log.txt on the Pico only when the bench debug marker exists
    (see above). Every line is timestamped (uptime, plus wall-clock once NTP
    has synced)."""
    line = _timestamp() + str(msg)
    sys.stdout.write(line + "\n")          # console output
    _log_ring.append(line)
    if len(_log_ring) > LOG_RING_LINES:
        del _log_ring[0]
    if _flash_logging_enabled():
        nk.append_line(LOG_FILE, line)         # bench only; best-effort

def flush_log_ring(reason):
    """One-shot: write the RAM ring to log.txt. Used on a crash so the lines
    leading up to it survive the reload — one write, not one per line."""
    try:
        with nk.writable():
            with open(LOG_FILE, "a") as f:
                f.write("\n" + _timestamp() + "===== RING FLUSH (%s) =====\n" % reason)
                for line in _log_ring:
                    f.write(line + "\n")
    except Exception:
        pass

def event(msg):
    """Append a categorized line to the durable event/audit log.
    Separate from the verbose boot log.txt — this is the permanent record of
    significant events (firmware updates [FW], config changes [CFG], role
    assignments [ROLE], factory resets [RESET], ...), so a single module's
    history is easy to find. Best-effort; never raises."""
    line = _timestamp() + str(msg)
    sys.stdout.write(line + "\n")
    nk.store().append(EVENTS_KEY, line, EVENTS_MAX)   # Store, never the FAT (DEV-18)

def events():
    """The audit history, oldest first (what DEV-36 shows the customer)."""
    return list(nk.store().get(EVENTS_KEY) or [])

def sync_time_ntp(pool, timeout=None):
    """One-time NTP sync to set the RTC so logs get wall-clock timestamps.
    Best-effort: any failure (no adafruit_ntp.mpy, no internet, DNS) is swallowed
    and we simply keep uptime-only logging. Call this AFTER a WiFi connect.
    `pool` is a socketpool from the radio (reuse the existing one). `timeout`
    (s) bounds the wait for the reply when a product is already running."""
    global _rtc_synced
    if _rtc_synced:
        return
    try:
        import rtc
        import adafruit_ntp
        # tz_offset stays 0 -> UTC. Keeps it simple; uptime gives relative timing.
        kw = {"socket_timeout": timeout} if timeout else {}
        ntp = adafruit_ntp.NTP(pool, tz_offset=0, cache_seconds=3600, **kw)
        rtc.RTC().datetime = ntp.datetime
        _rtc_synced = True
        log("[ntp] RTC synced (UTC) — wall-clock timestamps now enabled")
    except Exception as e:
        log(f"[ntp] sync skipped ({e}) — keeping uptime-only timestamps")

LOG_MAX_BYTES = 32000  # cap so the log can't fill the Pico flash (~32 KB)

def log_new_boot():
    """Append a boot separator to the log (history accumulates across boots).
    If the file has grown past LOG_MAX_BYTES, clear it first so it stays bounded.
    Only touches flash when flash logging is enabled (bench marker present)."""
    _log_ring.append(_timestamp() + "===== BOOT =====")
    if not _flash_logging_enabled():
        return
    try:
        nk.ensure_dir(LOG_FILE)
        with nk.writable():
            try:
                if os.stat(LOG_FILE)[6] > LOG_MAX_BYTES:   # index 6 = file size
                    with open(LOG_FILE, "w") as f:
                        f.write("(log trimmed — exceeded size cap)\n")
            except OSError:
                pass  # file doesn't exist yet
            with open(LOG_FILE, "a") as f:
                f.write("\n" + _timestamp() + "===== BOOT =====\n")
    except Exception:
        pass

# ── Constants ─────────────────────────────────────────────────────────────────

AP_SSID     = "noknok-setup"
AP_PASSWORD = ""   # Open network

# FALLBACK script, used only when the saved credentials carry no script_url
# (the app always sends the chosen product's script_url from its manifest in
# the public poc repo). Points at the trio demo in the public
# buildwithnoknok.github.io repo. There is no backend: the product comes from
# the app's catalog, not from a purchase record.
SCRIPT_URL = "https://raw.githubusercontent.com/buildwithnoknok/buildwithnoknok.github.io/main/poc/trio_demo.py"

# Field-written files live in /data (DEV-18): their directory entries share a
# block with each other, not with code.py / noknok.py / lib. Brains provisioned
# before /data existed still have them in the root — read as a fallback, never
# migrated (a migration would be one more setup-class write for no gain).
WIFI_CREDENTIALS_FILE = nk.DATA_DIR + "/wifi.json"
PRODUCT_SCRIPT_FILE   = nk.DATA_DIR + "/product.py"
_LEGACY_WIFI_FILE     = "wifi.json"
_LEGACY_PRODUCT_FILE  = "product.py"
WIFI_STORE_KEY        = "wifi"       # recovery copy in the Store (FRAM / nvm)
SWITCH_STORE_KEY      = "switch"     # pending product switch {"script_url"} (DEV-34)
WIFI_TIMEOUT_S        = 15

# Shared state: the /connect handler fills this, the main loop acts on it.
pending = {"ssid": None, "password": None, "script_url": None,
           "module_firmware": None, "product_id": None, "config_defaults": None,
           "ready": False}

# ── Role assignment: lazily-created, cached Conductor ───────────────────────────
# ── Pin configuration report (DEV-74) ──────────────────────────────────────────
# No GPIO is hard-coded: settings.toml is the only source of the I2C bus A/B and
# USB host pins (noknok.pin_config(), with a last-known-good copy in the Store).
# When it cannot give them, the brain says so everywhere it can: the console, a
# [CFG] event in the history the app reads (written only when the problem is
# new or changed, so no Store write per boot), the Pico's own LED (no module can
# signal it - they sit on the missing bus) and the `config` block of hello.
PIN_PROBLEMS_KEY = "pins_problems"
BUS_PROBLEMS_KEY = "bus_problems"
_bus_problems = []          # buses that are configured but did not come up, this boot

def _note_problems(key, problems):
    """[CFG] events for a problem list that differs from the last one recorded
    under `key` in the Store; one Store write per change, none per boot."""
    try:
        st = nk.store()
        before = st.get(key) or []
        if problems == before:
            return
        for p in problems:
            if p not in before:
                event("[CFG] " + p)
        if before and not problems:
            event("[CFG] resolved: " + "; ".join(before))
        if problems:
            st.set(key, problems)
        else:
            st.delete(key)
    except Exception as e:
        log(f"[cfg] could not record the problem ({e!r})")

def _blink_onboard_led(times=6, period=0.25):
    """Error pattern on the Pico's own LED. On a Pico 2 W board.LED is driven
    through the WiFi chip, so this is not a GPIO choice; it works when no module
    can be reached. Best-effort, ~1.5 s."""
    try:
        import board
        import digitalio
        led = digitalio.DigitalInOut(board.LED)
        led.direction = digitalio.Direction.OUTPUT
        for _ in range(times):
            led.value = True
            time.sleep(period / 2)
            led.value = False
            time.sleep(period / 2)
        led.deinit()
    except Exception as e:
        log(f"[cfg] onboard LED unavailable ({e!r})")

def _report_pin_config():
    """Log the pins in use and report every problem pin_config() found."""
    try:
        cfg = nk.pin_config()
    except Exception as e:
        log(f"[cfg] pin check failed: {e!r}")
        return
    log("[cfg] pins (%s): %s" % (cfg.get("source"), nk._pins_text(cfg)))
    problems = list(cfg.get("problems") or [])
    for p in problems:
        log("[cfg] PROBLEM: " + p)
    _note_problems(PIN_PROBLEMS_KEY, problems)
    if problems:
        _blink_onboard_led()

def _report_bus_problems(c):
    """A configured bus that did not come up (no pull-ups, both buses on one
    I2C peripheral): same treatment as a pin problem."""
    global _bus_problems
    _bus_problems = list(getattr(c, "bus_problems", None) or [])
    for p in _bus_problems:
        log("[cfg] PROBLEM: " + p)
    _note_problems(BUS_PROBLEMS_KEY, _bus_problems)

def _config_status():
    """The `config` block of hello: pins in use, where they came from, problems."""
    try:
        cfg = nk.pin_config()
    except Exception as e:
        return {"error": repr(e)}
    return {"pins": {g: (list(cfg[g]) if cfg.get(g) else None)
                     for g in ("i2c_a", "i2c_b", "usb")},
            "source": cfg.get("source"),
            "problems": list(cfg.get("problems") or []) + list(_bus_problems)}

# The role endpoints need a Conductor to talk to the I2C modules. Enumeration
# takes a few seconds, so we create + enumerate it once on first use and reuse it.
_conductor = None

def get_conductor(rescue_get_image=None):
    """Return a cached, enumerated Conductor, creating it on first use.
    The Conductor (noknok.py) self-configures its own I2C bus on the noknok
    standard pins, so no pins are passed here. Uses enumerate_all() (I2C + USB)
    rather than enumerate() so USB-only products (e.g. the desk lamp's USB LEDs
    module) are actually found for role detection AND firmware checks — with
    I2C-only, a USB module's firmware_report() entry never existed, so its OTA
    was silently skipped. enumerate_usb() no-ops cleanly if there's no USB host
    support on the build, so this is a no-op cost for I2C-only products. Returns
    None if noknok.py is missing or the I2C bus can't be brought up — callers
    degrade gracefully.

    `rescue_get_image(entry) -> bytes`, when given, enables the DEV-31 parked-
    module rescue below. It is optional because only the post-WiFi boot path can
    actually fetch an image; the AP-time role endpoints call this with no network
    to GitHub and simply skip the rescue."""
    global _conductor
    if _conductor is None:
        try:
            from noknok import Conductor
            log("[roles] Creating Conductor + enumerating modules (first use)...")
            c = Conductor()                 # pins from settings.toml (DEV-74), 100 kHz
            _report_bus_problems(c)
            if rescue_get_image is not None:
                _rescue_parked(c, rescue_get_image)
            found = c.enumerate_all()       # ~3 s — discovers I2C + USB modules
            log(f"[roles] Enumeration done — {found} module(s) found")
            if rescue_get_image is not None:
                _usb_fw(c)                  # DEV-133: pending USB updates + USB rescue
            _conductor = c
        except Exception as e:
            log(f"[roles] Conductor init failed: {e}")
            return None
    return _conductor


def _rescue_parked(c, get_image):
    """DEV-31 hardening D — recover a module stuck in its bootloader at 0x7E.

    Must run BEFORE enumerate(): a parked module never answers the enumeration
    sweep, so if we skip this nothing downstream can see it and the module looks
    simply absent. Two things park one — an update that lost power part-way, or
    stage-1 refusing an app that crashed three times running (error 7).

    Best-effort by design: a module we cannot identify is logged for a human and
    the boot continues. Never let this stop a product from starting."""
    try:
        res = c.rescue_parked_module(get_image, logfn=log)
    except Exception as e:
        log(f"[rescue] check failed (ignored): {e!r}")
        return
    if not res:
        return                                  # nothing parked — the normal case
    event(f"[RESCUE] uid={res.get('uid')} type={res.get('type')} "
          f"reason={res.get('reason')} action={res.get('action')} "
          f"detail={res.get('detail')}")
    if res.get("type") == "picohub" and res.get("action") == "reflashed":
        _need_hard_reset_for_usb("the PicoHub power manager was rescued (J2 was off)")
    if res.get("action") != "reflashed":
        # Report it loudly (DEV-97): the product starts a module short, so say
        # so plainly in the log, in the event history the app reads (above),
        # and with the buzzer/LED alert. Retried on every later boot.
        log(f"[rescue] MODULE STILL PARKED: type={res.get('type')} uid={res.get('uid')} "
            f"({res.get('detail')}) - the product starts without it; "
            f"retried on the next boot")
        _field_alerts.append("rescue %s %s" % (res.get("action"), res.get("uid")))


# ── USB modules: everything around a hard reset (DEV-133 / DEV-134) ────────────
# This host session will not see a USB module come back once it re-enumerated
# or lost VBUS (DEV-45). A hard reset gives a fresh host (the cold-boot reload in
# main() runs before any USB host exists), which always finds it. So every step
# that makes a USB module re-enumerate only asks for a hard reset here, and
# main() does it before the product starts instead of the DEV-132 soft reload.
_hard_reset_why = None

def _usb_configured():
    try:
        return nk.pins("usb") is not None
    except Exception:
        return False

def _need_hard_reset_for_usb(why):
    """Ask main() for a hard reset before the product - only if this brain has a
    USB host at all (an I2C-only brain loses nothing to a soft reload)."""
    global _hard_reset_why
    if _usb_configured() and _hard_reset_why is None:
        _hard_reset_why = why
        log(f"[usb] hard reset before the product: {why}")

def _cached_usb_image(entry):
    """Image for a USB module from the on-device cache (size/crc32-checked). USB
    images carry no flash layout (the V203 bootloader has one fixed layout).
    Hands the version back so usb_fw_pass() can check it one boot later."""
    image, meta = _cache_read(entry["type"])
    entry["version"] = meta.get("version")
    log(f"[usb] using cached {entry['type']} {meta.get('version')} ({len(image)} B)")
    return image

def _usb_fw(c):
    """Conductor.usb_fw_pass() with the cache as image source, results to the
    event log, problems to the customer alert, a hard reset when it moved a
    module. Best-effort: never stops the boot."""
    try:
        results = c.usb_fw_pass(_cached_usb_image, logfn=log)
    except Exception as e:
        log(f"[usb] firmware pass failed (ignored): {e!r}")
        return
    for r in results:
        event(f"[USBFW] {r.get('type')} uid={r.get('uid')} action={r.get('action')} "
              f"detail={r.get('detail')}")
        if r.get("action") in ("failed", "gave_up", "unknown"):
            _field_alerts.append("usb %s %s" % (r.get("action"), r.get("uid")))
    if getattr(c, "usb_needs_reset", False):
        _need_hard_reset_for_usb("a USB module was flashed or asked into its bootloader")

def _hard_reset_if_needed():
    """Hard reset if a USB step asked for one. After an online pass the nvm[5]
    marker is set first, so the run after the reset skips the pass (DEV-132)."""
    if _hard_reset_why is None:
        return
    if _ota_went_online:
        try:
            microcontroller.nvm[POST_OTA_NVM_INDEX] = POST_OTA_MAGIC
        except Exception:
            pass
    _release_conductor()
    event(f"[BOOT] hard reset before the product: {_hard_reset_why}")
    time.sleep(0.5)
    microcontroller.reset()

def _rescue_from_cache(why):
    """Parked-module rescue with the on-device cache as the only image source.
    Every connected boot that does NOT reach the online OTA pass calls this, so
    no WiFi boot ever skips the rescue (DEV-97). Before 0.20 the due-boot early
    returns below (no module_firmware, HTTPS setup failed, nothing resolved)
    ran no rescue at all: a parked module stayed parked, silently. No network,
    no filesystem write: one I2C read when nothing is parked."""
    log(f"[fw] {why} — rescue from cache only")
    get_conductor(rescue_get_image=_cached_image)

# ── HTML pages ─────────────────────────────────────────────────────────────────

HTML_SETUP = """\
<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>noknok setup</title>
<style>
body{font-family:sans-serif;max-width:360px;margin:48px auto;padding:24px;color:#222}
h1{margin:0 0 4px}p{margin:0 0 24px;color:#666;font-size:14px}
label{font-size:13px;font-weight:600;display:block;margin-bottom:4px}
input{width:100%;padding:10px;margin-bottom:16px;border:1px solid #ccc;
      border-radius:6px;box-sizing:border-box;font-size:15px}
button{width:100%;padding:12px;background:#0066ff;color:#fff;border:none;
       border-radius:6px;font-size:16px;cursor:pointer}
</style></head><body>
<h1>noknok setup</h1>
<p>Connect to your home WiFi network.</p>
<form method="POST" action="/connect">
<label>Network name</label>
<input type="text" name="ssid" placeholder="Your WiFi name" required>
<label>Password</label>
<input type="password" name="password" placeholder="WiFi password">
<button type="submit">Connect</button>
</form></body></html>"""

HTML_SUCCESS = """\
<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>noknok setup</title>
<style>body{font-family:sans-serif;max-width:360px;margin:48px auto;padding:24px;color:#222}
h1{color:#00aa44}</style></head><body>
<h1>Connecting...</h1>
<p>Your noknok device is now trying your network. If it works, it downloads its
software and starts on its own.</p>
<p>If the network "noknok-setup" appears again within a minute, the connection did
not work (usually a wrong password): join it again and retry.</p>
</body></html>"""

# ── Filesystem helpers ─────────────────────────────────────────────────────────

def load_wifi_credentials():
    """Saved {"ssid", "password", "script_url", "module_firmware"} or None.
    Three homes, first intact one wins: /data/wifi.json, the legacy root
    wifi.json, and the Store copy (FRAM: power-safe; nvm: usually there).
    If only the Store copy survived a bad power cut, the file is rewritten
    from it so the next boot is normal — a setup-class write, once."""
    for path in (WIFI_CREDENTIALS_FILE, _LEGACY_WIFI_FILE):
        creds = nk.read_json(path)
        if isinstance(creds, dict) and creds.get("ssid"):
            return creds
    creds = nk.store().get(WIFI_STORE_KEY)
    if isinstance(creds, dict) and creds.get("ssid"):
        log("[storage] wifi.json missing — restored from the Store copy")
        nk.write_json_atomic(WIFI_CREDENTIALS_FILE, creds)
        return creds
    return None

def save_wifi_credentials(ssid, password, script_url=None, module_firmware=None,
                          product_id=None):
    """Store copy first (power-safe on FRAM), then the file (atomic write into
    /data). Returns False only when NEITHER took it — e.g. the CIRCUITPY drive
    is visible to a PC (settings.toml NOKNOK_USB_DRIVE = 1) and there is no
    FRAM; the caller must not pretend provisioning worked.
    product_id (the manifest id, optional — older apps don't send it) rides
    along so the app can identify the installed product later (DEV-34)."""
    creds = {"ssid": ssid, "password": password, "script_url": script_url,
             "module_firmware": module_firmware, "product_id": product_id}
    in_store = nk.store().set(WIFI_STORE_KEY, creds)
    in_file  = nk.write_json_atomic(WIFI_CREDENTIALS_FILE, creds)
    log("[storage] credentials saved (store=%s file=%s)" % (in_store, in_file)
        if (in_store or in_file) else
        "[storage] CANNOT save credentials — filesystem read-only and no Store "
        "(drive visible to a PC? set NOKNOK_USB_DRIVE = 0 in settings.toml)")
    return in_store or in_file

def delete_wifi_credentials():
    nk.store().delete(WIFI_STORE_KEY)
    if nk.remove(WIFI_CREDENTIALS_FILE, _LEGACY_WIFI_FILE):
        log("[storage] Deleted wifi.json")

def _url_decode(s):
    """Percent-decode an application/x-www-form-urlencoded value.
    '+' -> space, %XX -> byte. Robust: leaves malformed sequences as-is."""
    if not s:
        return s
    s = s.replace("+", " ")
    out = bytearray()
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "%" and i + 2 < n:
            try:
                out.append(int(s[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(ord(c))
        i += 1
    try:
        return out.decode("utf-8")
    except Exception:
        return out.decode("latin-1")

def product_script_path():
    """Where the product script is: /data/product.py, or the legacy root
    product.py on a brain provisioned before /data existed. None if absent."""
    for path in (PRODUCT_SCRIPT_FILE, _LEGACY_PRODUCT_FILE):
        try:
            os.stat(path)
            return path
        except OSError:
            pass
    return None

def product_script_exists():
    return product_script_path() is not None

# ── WiFi ──────────────────────────────────────────────────────────────────────

def connect_wifi(ssid, password):
    """Join a WiFi network. Returns True on success.

    gc.collect() first — NOT optional. Bench-proven 17 Sep 2026 (CircuitPython
    10.3.0, Pico 2 W): right after a cold boot the compile of code.py +
    noknok.py leaves the heap full of uncollected garbage (~180 KB free,
    fragmented), the WiFi driver's allocation fails silently and every join
    times out with "Unknown failure 1" — retries, radio power-cycling and
    waiting do not help because nothing allocates while a join waits, so the
    automatic GC never runs. One collect (→ ~360 KB free) and the join takes
    3.6 s. Without this every power-on of a shipped brain came up offline."""
    import gc
    gc.collect()
    log(f"[wifi] Connecting to '{ssid}'...")
    global _wifi_error
    _wifi_error = None
    try:
        for lease in range(1, ROGUE_LEASE_TRIES + 1):
            wifi.radio.connect(ssid, password, timeout=WIFI_TIMEOUT_S)
            log(f"[wifi] Connected — IP: {wifi.radio.ipv4_address}")
            _log_link()
            if not _rogue_lease():
                break
            # DEV-113: a 192.168.4.x address with gateway 192.168.4.1 is a lease
            # from a noknok/CircuitPython setup hotspot whose DHCP server also
            # answers on its home-WiFi side: no internet, no DNS, unreachable.
            log("[wifi] address came from a setup hotspot's DHCP server on this "
                f"network (another brain in setup?) — rejoining ({lease}/{ROGUE_LEASE_TRIES})")
            if lease == ROGUE_LEASE_TRIES:
                event("[WIFI] got a 192.168.4.x address from a setup hotspot on the "
                      "home network: no internet until that device leaves setup")
                break
            wifi.radio.stop_station()
            time.sleep(2)
        return True
    except Exception as e:
        _wifi_error = "%s: %s" % (type(e).__name__, e)
        log(f"[wifi] Failed: {e}")
        return False

_wifi_error = None          # text of the last failed join (connect_wifi)
ROGUE_LEASE_TRIES = 3       # joins in a row before a setup-hotspot lease is accepted

def _log_link():
    """DEV-113: which access point and DHCP answer did this join get?
    wifi.radio.ap_info raises NotImplementedError on the Pico 2 W (CircuitPython
    10.3.1, bench 23 Oct), so the AP part is optional; gateway + DNS always."""
    try:
        line = f"[wifi] gateway {wifi.radio.ipv4_gateway}  DNS {wifi.radio.ipv4_dns}"
    except Exception as e:
        line = f"[wifi] gateway/DNS unavailable ({e!r})"
    try:
        ap = wifi.radio.ap_info
        if ap:
            line += "  AP %s ch %s rssi %s" % (
                ":".join("%02X" % b for b in ap.bssid), ap.channel, ap.rssi)
    except Exception:
        pass                                  # not on this port
    log(line)

def _rogue_lease():
    """True when the station got its address from a setup hotspot's DHCP server
    (CircuitPython/MicroPython dhcpserver: 192.168.4.16.., gateway = itself)."""
    try:
        ip, gw = str(wifi.radio.ipv4_address), str(wifi.radio.ipv4_gateway)
        return (ip.startswith("192.168.4.") and gw == "192.168.4.1"
                and not wifi.radio.ap_active)
    except Exception:
        return False

def _stop_ap():
    """Stop the setup hotspot AND its DHCP server. DEV-113: CircuitPython's
    hotspot DHCP server listens on every interface; if it outlives the hotspot,
    the brain hands 192.168.4.x addresses to devices on the home network."""
    wifi.radio.stop_ap()
    stop = getattr(wifi.radio, "stop_dhcp_ap", None)
    if stop is not None:
        try:
            stop()
        except Exception as e:
            log(f"[ap] stop_dhcp_ap: {e!r}")

def _join_reason(err):
    """Classify a CircuitPython join error for the app (DEV-100): 'auth' (wrong
    password), 'not_found' (no such network in range), 'timeout' or 'other'."""
    e = (err or "").lower()
    if "auth" in e or "password" in e:
        return "auth"
    if "no network" in e or "not found" in e:
        return "not_found"
    if "timeout" in e or "timed out" in e:
        return "timeout"
    return "other"

# ── Script download ────────────────────────────────────────────────────────────

DOWNLOAD_CHUNK = 1024

def _remove_quiet(path):
    try:
        with nk.writable():
            os.remove(path)
    except OSError:
        pass

def _stream_to_tmp(response, tmp):
    """Write the HTTP body to `tmp` in DOWNLOAD_CHUNK pieces (DEV-139), never
    holding it whole. Returns (ok, bytes_written, content_length_or_None, err);
    err is "readonly" when the file cannot be opened for writing, else the
    network error text. Always closes the response."""
    expected = None
    try:
        expected = int(response.headers.get("content-length"))
    except Exception:
        pass
    got = 0
    opened = False
    try:
        nk.ensure_dir(tmp)
        with nk.writable():
            with open(tmp, "wb") as fh:
                opened = True
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK):
                    fh.write(chunk)
                    got += len(chunk)
                fh.flush()
            try:
                os.sync()
            except Exception:
                pass
        return True, got, expected, None
    except OSError as e:
        if not opened:
            return False, got, expected, "readonly"
        _remove_quiet(tmp)
        return False, got, expected, "download interrupted: %r" % (e,)
    finally:
        try:
            response.close()
        except Exception:
            pass

def download_and_save_script(script_url=None):
    """Download product script from GitHub and save as product.py.
    Uses adafruit_connection_manager so DNS and the SSL context are set up
    correctly for the radio. Retries a few times since DNS can need a moment
    to settle after a fresh WiFi join."""

    # Resolve the URL and log WHERE it came from. This is the key diagnostic:
    # it tells us whether the app-supplied script_url actually arrived in
    # wifi.json, or whether we silently fell back to the hardcoded PoC URL.
    if script_url:
        url = script_url
        log(f"[download] script_url source: wifi.json (app-supplied)")
    else:
        url = SCRIPT_URL
        log(f"[download] script_url source: fallback SCRIPT_URL (none in wifi.json)")
    log(f"[download] resolved url: {url}")

    # Give the network stack a moment to settle DNS after connecting
    time.sleep(2)
    log(f"[wifi] DNS server: {wifi.radio.ipv4_dns}  gateway: {wifi.radio.ipv4_gateway}")

    # Build the session via the connection manager (correct DNS + SSL setup)
    pool    = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
    context = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
    session = adafruit_requests.Session(pool, context)

    tmp = PRODUCT_SCRIPT_FILE + ".tmp"
    for attempt in range(1, 4):  # up to 3 tries
        log(f"[download] Attempt {attempt}: fetching {url}")
        try:
            gc.collect()
            response = session.get(url, timeout=30)

            if response.status_code != 200:
                log(f"[download] HTTP {response.status_code}")
                response.close()
                return False

            # DEV-139: stream to <file>.tmp in 1 KB pieces. response.text needed
            # the whole script TWICE in one block each (bytes + str, 36 KB for
            # Smart Lamp Maxi); after setup the heap is fragmented and that
            # failed on every boot ("allocating 36027 bytes").
            ok, got, expected, err = _stream_to_tmp(response, tmp)
            if not ok:
                if err == "readonly":
                    log(f"[download] CANNOT save {PRODUCT_SCRIPT_FILE} — filesystem "
                        "read-only (drive visible to a PC? see settings.toml)")
                    return False
                raise OSError(err)                      # network error: retry
            if expected is not None and got != expected:
                _remove_quiet(tmp)
                raise OSError("truncated: %d of %d bytes" % (got, expected))

            # Compile before it replaces the running copy: a half-served
            # download must never take a working product down (DEV-18). Read
            # back from the file; if memory is too tight for that, the size
            # check above stands in and run_product()'s three strikes judge it.
            gc.collect()
            try:
                with open(tmp, "r") as fh:
                    compile(fh.read(), PRODUCT_SCRIPT_FILE, "exec")
            except SyntaxError as e:
                log(f"[download] REFUSED — {PRODUCT_SCRIPT_FILE} does not compile: {e}")
                _remove_quiet(tmp)
                return False
            except MemoryError:
                log("[download] compile check skipped (not enough memory now) — "
                    "size verified, the product start will judge it (DEV-139)")
            except NameError:
                pass   # build without compile(); exec() will judge it
            gc.collect()

            try:
                with nk.writable():
                    os.rename(tmp, PRODUCT_SCRIPT_FILE)   # replaces the old copy
            except OSError:
                log(f"[download] CANNOT save {PRODUCT_SCRIPT_FILE} — filesystem "
                    "read-only (drive visible to a PC? see settings.toml)")
                return False

            log(f"[download] SUCCESS — saved {PRODUCT_SCRIPT_FILE} ({got} bytes) from {url}")
            return True

        except Exception as e:
            log(f"[download] Attempt {attempt} error: {e}")
            time.sleep(2)  # wait and retry — usually DNS settling

    log(f"[download] FAILED — all attempts exhausted for {url}")
    return False

# ── HTTP routes ────────────────────────────────────────────────────────────────

# ── Message handlers — the ops (DEV-34) ───────────────────────────────────────
# Each takes plain Python values and returns the reply dict; the HTTP routes
# below and the /rpc dispatcher both call them, so the app gets the same
# answer whichever door it uses. None of them touches the network as a client
# (the DEV-32 rule: no download after a Conductor has existed).

def _device_state():
    if not load_wifi_credentials():
        return "unprovisioned"
    if product_script_exists() and _crash_count() >= CRASH_MAX:
        return "parked"
    return "provisioned"

def _h_hello(args, msg):
    """Who am I, what am I running, can you reach me later. Open op.
    DEV-141: args.ack = the join_id the app just got back from provision ->
    the reply arrived, the setup loop may drop the hotspot now."""
    ack = args.get("ack") if isinstance(args, dict) else None
    if ack and pending.get("ready") and ack == pending.get("join_id"):
        pending["acked"] = True
    creds = load_wifi_credentials() or {}
    url = creds.get("script_url") or ""
    try:
        import noknok_usb
        usb_v = getattr(noknok_usb, "__version__", None)
    except Exception:
        usb_v = None
    return {
        "device": "".join("%02x" % b for b in microcontroller.cpu.uid),
        "name": rpc.device_name(),
        "state": _device_state(),
        "product": {"id": creds.get("product_id"),
                    "script": url.rsplit("/", 1)[-1] if url else None,
                    "script_url": url or None},
        "versions": {"code": CODE_VERSION,
                     "noknok": getattr(nk, "__version__", None),
                     "noknok_usb": usb_v,
                     "rpc": rpc.__version__,
                     "circuitpython": sys.version.split(" on ")[0]},
        "online": bool(wifi.radio.connected),
        "ap": bool(wifi.radio.ap_active),
        "config": _config_status(),     # DEV-74: pins in use + problems (app: "setup incomplete")
        # DEV-100: why the last setup join failed (RAM). DEV-141: received = the
        # join_id of the last accepted provision (app: "did it get my request?").
        "setup": {"last_join": _last_join, "received": _received_join},
        "carrier": "wifi",
        "uptime": round(time.monotonic(), 1),
        "ops": rpc.dispatcher().ops(),
    }

def _h_status(args, msg):
    """Phase-1 subset of `status` (DEV-36 adds per-module firmware state):
    event history, crash strikes, storage mode, memory. `since` (int) skips
    the first N events so the app can page."""
    import gc
    ev = events()
    since = int(args.get("since") or 0)
    c = nk.conductor()
    modules = []
    power = None
    if c is not None:
        for m in c._registry.values():
            if m is None:
                continue                 # a module type without a driver
            modules.append({"type": type(m).__name__.replace("Noknok", "").lower(),
                            "uid": getattr(m, "_uid_hex", None) or getattr(m, "serial", None),
                            "fw": getattr(m, "firmware_version", None),
                            "bus": getattr(m, "i2c_bus", None) or "usb"})   # "A"/"B"/"usb" (DEV-74)
        hub = getattr(c, "picohub", None)
        if hub is not None:              # DEV-123: the PicoHub's power manager, one I2C read
            try:
                power = hub.status()
            except Exception as e:
                power = {"error": str(e)}
    return {
        "state": _device_state(),
        "strikes": _crash_count(),
        "store": nk.store().backend,
        "drive_visible": nk.usb_drive_visible(),
        "mem_free": gc.mem_free(),
        "ip": str(wifi.radio.ipv4_address) if wifi.radio.connected else None,
        "link": rpc.link_stats(),
        "last_bad_request": rpc._last_bad,
        "modules": modules,
        "power": power,                  # PicoHub power status or null (DEV-123)
        "events": ev[since:],
        "events_total": len(ev),
    }

def _h_reboot(args, msg):
    """Reply first, reset after the response has gone out (deferred)."""
    log("[rpc] reboot requested by the app")
    def _reboot():
        nk.flush_settings()              # the last few seconds of changes
        time.sleep(0.5)                  # let the reply leave the radio first
        microcontroller.reset()
    rpc.defer(_reboot)
    return {"rebooting": True}

def _h_factory_reset(args, msg):
    """Same wipe as the knob-hold gesture (noknok.factory_reset), deferred so
    the app gets its reply before the brain drops off the network."""
    log("[rpc] factory reset requested by the app")
    event("[RESET] factory reset via app")
    rpc.defer(lambda: nk.factory_reset(delay=0.5))
    return {"resetting": True}

def _do_roles_assign(role_id, module_type, exclude):
    """Detect which module the customer touches and (if role_id) save the
    role in one go. Blocks up to ~20 s. Returns the reply dict."""
    log(f"[roles] assign role_id='{role_id}' type='{module_type}' "
        f"exclude={len(exclude)} module(s)")
    c = get_conductor()
    if c is None:                       # no bus / noknok.py — degrade, never 500
        return {"timeout": True}
    try:
        uid = c.detect_interaction(module_type, timeout=20, exclude=exclude)
    except Exception as e:
        log(f"[roles] detect_interaction error: {e}")
        uid = None
    if not uid:
        log(f"[roles] assign timed out for type='{module_type}'")
        return {"timeout": True}
    saved = False
    if role_id:
        try:
            saved = bool(c.append_role(role_id, uid))
        except Exception as e:
            log(f"[roles] append_role error: {e}")
    log(f"[roles] assigned role_id='{role_id}' uid={uid} saved={saved}")
    return {"uid": uid, "type": module_type, "saved": saved}

def _settings_for(args):
    scope = args.get("scope") or "product"
    if scope not in ("product", "device"):
        raise rpc.RpcError("bad scope", scope)
    return scope, nk.settings(scope)

def _h_settings_get(args, msg):
    """Current values (+ defaults + seq) — the app calls this before rendering
    its page and polls it to pick up knob-driven changes (compare `seq`)."""
    scope, s = _settings_for(args)
    out = s.snapshot()
    out["scope"] = scope
    return out

def _h_settings_set(args, msg):
    """Merge values from the app. The product's on_change callback runs at
    its next module read; the Store write follows after 5 s of quiet."""
    scope, s = _settings_for(args)
    values = args.get("values")
    if not isinstance(values, dict):
        raise rpc.RpcError("values must be an object")
    changed, rejected = s.apply_remote(values)
    log("[rpc] settings.set %s: changed=%s rejected=%s" % (scope, changed, rejected))
    out = {"scope": scope, "changed": changed, "rejected": rejected,
           "values": s.all(), "seq": s.seq}
    if rejected and not changed:
        out["ok"] = False
        out["error"] = "rejected"
    return out

def _h_settings_reset(args, msg):
    scope, s = _settings_for(args)
    values = s.reset()                  # queues the differences for on_change
    log("[rpc] settings.reset %s" % scope)
    return {"scope": scope, "values": values, "seq": s.seq}

# ── Setup join feedback (DEV-100) ──────────────────────────────────────────────
# The phone loses the hotspot while the brain tries the home WiFi, so the brain
# cannot answer "it worked". What it can do: say up front when the network is
# not even visible (scan pre-check), and remember why a join failed, so that
# when the hotspot comes back, hello -> setup.last_join tells the app. RAM only
# (ADR-003): a power cut mid-join forgets it, and the app then says "restarted".
# join_id ties a report to the request that caused it, so the app never takes
# the seconds before the join (hotspot still up) for a failure.
_JOIN_BOOT = "%04x" % (int.from_bytes(os.urandom(2), "little"),)
_join_seq  = 0
_last_join = None           # {"join_id", "ssid", "ok", "reason", "attempts", "detail"}
_received_join = None       # DEV-141: join_id of the last provision this boot accepted

# DEV-141: the provision reply must reach the phone BEFORE the hotspot drops.
# The old fixed 1 s was too short: one lost radio frame and lwIP retransmits
# only after ~3 s, by which time the AP is gone. On a successful join the brain
# then resets and the reply is lost for good (app: "Something went wrong").
# So the brain keeps serving for up to REPLY_GRACE_S and stops early once the
# app proves it has the reply (hello with args.ack = that join_id).
REPLY_GRACE_S = 5

def _clean_join_id(v):
    """An app-chosen join_id (DEV-141), or None if absent/unsafe. Letters,
    digits and '-', at most 32 chars: it is echoed in hello and logs."""
    if not isinstance(v, str) or not 0 < len(v) <= 32:
        return None
    for ch in v:
        if not (ch.isalpha() or ch.isdigit() or ch == "-"):
            return None
    return v

# DEV-130: NEVER scan while the hotspot runs. On CircuitPython 10.3.1 / Pico 2 W
# a scan with the AP up can block forever INSIDE one next() of the scan iterator
# (C code waits while the radio says "scan active"), which froze the whole web
# server. No Python time limit can break that, so the rule is structural: the
# brain scans once right BEFORE each start_ap() (plain station mode, the normal
# path) and wifi.scan / the provision pre-check read that list while the AP runs.
_scan_cache = None          # list from _scan_networks(), or None (no scan / failed)
_scan_at    = None          # time.monotonic() of that scan

SCAN_BUDGET_S = 10          # stop collecting after this long (results that trickle)
SCAN_PASSES   = 2           # DEV-150: one pass missed a strong home network (1 of 2 boots)
_refused_once = set()       # DEV-150: names refused as not visible, this boot (RAM only)

def _scan_networks():
    """Networks the radio can see: [{"ssid", "rssi", "open"}], strongest first,
    without our own hotspot, at most 25. None if scanning is not possible right
    now - callers then skip the check instead of refusing anything. Refuses
    outright while the hotspot runs (DEV-130)."""
    if wifi.radio.ap_active:
        log("[ap] WiFi scan refused while the hotspot runs (DEV-130)")
        return None
    nets = {}
    deadline = time.monotonic() + SCAN_BUDGET_S
    try:
        for n in wifi.radio.start_scanning_networks():
            if time.monotonic() > deadline:
                log("[ap] WiFi scan took too long - using what was found so far")
                break
            name = n.ssid
            if not name or name == AP_SSID:
                continue
            if name not in nets or n.rssi > nets[name]["rssi"]:
                try:
                    is_open = wifi.AuthMode.OPEN in n.authmode
                except Exception:
                    is_open = False
                nets[name] = {"ssid": name, "rssi": n.rssi, "open": bool(is_open)}
    except Exception as e:
        log(f"[ap] WiFi scan not possible now ({e!r}) - skipping the check")
        nets = None
    finally:
        try:
            wifi.radio.stop_scanning_networks()
        except Exception:
            pass
    if nets is None:
        return None
    return sorted(nets.values(), key=lambda x: -x["rssi"])[:25]

def _refresh_scan_cache():
    """Scan now (hotspot must be down) and keep the result for the AP session.
    DEV-150: SCAN_PASSES passes, merged (strongest reading per name): a single
    pass can miss a network whose beacon it did not catch, and this list is all
    the setup session gets (no scan with the AP up, DEV-130)."""
    global _scan_cache, _scan_at
    merged = None
    for _ in range(SCAN_PASSES):
        nets = _scan_networks()
        if nets is None:
            continue
        merged = merged or {}
        for n in nets:
            if n["ssid"] not in merged or n["rssi"] > merged[n["ssid"]]["rssi"]:
                merged[n["ssid"]] = n
    _scan_cache = None if merged is None else         sorted(merged.values(), key=lambda x: -x["rssi"])[:25]
    _scan_at = time.monotonic()
    if _scan_cache is not None:
        log(f"[ap] {len(_scan_cache)} WiFi network(s) visible before the hotspot starts "
            f"({SCAN_PASSES} scans)")

def _scan_before_ap():
    """Called before every start_ap(). DEV-130: never scan with the AP up.
    DEV-136: and never right after a failed join either - that station-mode
    scan hung for good after a router "Authentication failure" (2 of 2). So:
    ONE scan per boot, on a fresh radio before the first start_ap(); every
    restart of the hotspot reuses that list."""
    if _scan_at is None:
        _refresh_scan_cache()
    else:
        log("[ap] reusing the network list from boot (no scan after a failed join, DEV-136)")

def _visible_networks():
    """(networks or None, live). With the hotspot up: the list scanned before it
    started (live False). Without it (home WiFi): a fresh scan (live True)."""
    if wifi.radio.ap_active:
        return _scan_cache, False
    return _scan_networks(), True

def _h_wifi_scan(args, msg):
    """wifi.scan op (DEV-100): the networks the device itself can see, so the
    app offers a list instead of a typed name. On the hotspot this is the list
    from just before the hotspot started (DEV-130): live false + age_s, so the
    app knows that "search again" cannot bring a newer one."""
    nets, live = _visible_networks()
    if nets is None:
        return {"available": False, "networks": [], "live": live}
    r = {"available": True, "networks": nets, "live": live}
    if not live and _scan_at is not None:
        r["age_s"] = int(time.monotonic() - _scan_at)
    return r

def _do_provision(ssid, password, script_url, module_firmware, product_id,
                  config_defaults=None, force=False, join_id=None):
    """Two situations, one op:
    - On the setup AP: hand the credentials to run_ap_provisioning()'s loop,
      which stops the AP, verifies the join, saves and hard-resets (as today).
    - On home WiFi (product running): a product switch. ssid/password may be
      omitted to keep the current network. Save, drop product.py, hard-reset;
      the boot path downloads the new script before any Conductor exists."""
    global _join_seq, _received_join
    if wifi.radio.ap_active:
        if not ssid:
            return {"ok": False, "error": "ssid required"}
        # DEV-100: refuse at once, hotspot kept, when the network is not even
        # visible (typo, out of range). force=True skips it (hidden network).
        # DEV-150: the list is one boot-time scan and can miss a network that
        # IS there, and it cannot be refreshed with the hotspot up (DEV-130).
        # So only the FIRST request for a name is refused; the same name asked
        # again in this boot is tried for real (a typo then fails as not_found).
        if not force:
            nets = _scan_cache          # never a live scan with the AP up (DEV-130)
            if nets is not None and ssid not in [n["ssid"] for n in nets]:
                if ssid not in _refused_once:
                    _refused_once.add(ssid)
                    log(f"[ap] '{ssid}' is not visible from here - not trying it "
                        f"(asking again tries it anyway)")
                    return {"accepted": False, "reason": "network_not_found", "networks": nets}
                log(f"[ap] '{ssid}' asked again - trying it although the scan did not "
                    f"see it (DEV-150)")
        # DEV-141: the app may choose the join_id, so it can still follow the
        # join when this reply is lost on the way back.
        join_id = _clean_join_id(join_id)
        if join_id is None:
            _join_seq += 1
            join_id = "%s-%d" % (_JOIN_BOOT, _join_seq)
        _received_join = join_id
        pending["acked"]           = False
        pending["join_id"]         = join_id
        pending["ssid"]            = ssid
        pending["password"]        = password or ""
        pending["script_url"]      = script_url or ""
        pending["module_firmware"] = module_firmware
        pending["product_id"]      = product_id
        pending["config_defaults"] = config_defaults
        pending["ready"]           = True
        log(f"[ap] Credentials received for '{ssid}' "
            f"(module_firmware: {'yes' if module_firmware else 'none'}, "
            f"product_id: {product_id or 'none'})")
        return {"accepted": True, "mode": "setup", "join_id": join_id,
                "uptime": round(time.monotonic(), 1)}
    creds = load_wifi_credentials() or {}
    if ssid and ssid != creds.get("ssid"):
        return {"ok": False, "error": "network change needs setup mode",
                "detail": "a product switch keeps the current WiFi; use the setup AP to change it"}
    if not script_url or not str(script_url).startswith("http"):
        return {"ok": False, "error": "script_url required"}
    # Nothing is saved yet. The request is parked in the Store; the boot path
    # downloads + compiles the new script BEFORE any Conductor exists (DEV-32
    # rule) and only on success replaces product.py, saves the new product
    # in the credentials and installs its defaults. A bad URL or a dead uplink
    # leaves the customer with the product, settings and firmware they had.
    req = {"script_url": script_url, "module_firmware": module_firmware,
           "product_id": product_id, "config_defaults": config_defaults}
    if not nk.store().set(SWITCH_STORE_KEY, req):
        return {"ok": False, "error": "cannot save (store write failed)"}
    event(f"[CFG] product switch requested -> {product_id or script_url.rsplit('/', 1)[-1]}")
    def _switch():
        nk.flush_settings()
        time.sleep(0.5)                  # let the reply leave the radio first
        _set_crash_count(0)
        microcontroller.reset()
    rpc.defer(_switch)
    return {"accepted": True, "mode": "switch", "rebooting": True}

def _install_settings(config_defaults):
    """After the credentials are saved: write the product's defaults so device
    and app agree from first boot (same product keeps values, a different one
    starts fresh). Best-effort; an older app sends none and the product's own
    c.settings.defaults() fills in."""
    try:
        ok = nk.install_defaults(nk.product_tag(), config_defaults or {})
        log("[settings] defaults installed for %s (%d key(s), ok=%s)"
            % (nk.product_tag(), len(config_defaults or {}), ok))
    except Exception as e:
        log(f"[settings] install failed (ignored): {e!r}")

def register_ops():
    """Register every op on the brain's dispatcher (idempotent)."""
    d = rpc.dispatcher()
    d.register("hello", _h_hello)
    d.register("status", _h_status)
    d.register("reboot", _h_reboot)
    d.register("factory_reset", _h_factory_reset)
    d.register("settings.get", _h_settings_get)
    d.register("settings.set", _h_settings_set)
    d.register("settings.reset", _h_settings_reset)
    d.register("roles.assign", lambda a, m: _do_roles_assign(
        a.get("role_id"), a.get("module_type") or "", list(a.get("exclude") or [])))
    d.register("provision", lambda a, m: _do_provision(
        a.get("ssid"), a.get("password"), a.get("script_url"),
        a.get("module_firmware"), a.get("product_id"), a.get("config_defaults"),
        bool(a.get("force")), a.get("join_id")))
    d.register("wifi.scan", _h_wifi_scan)          # DEV-100

def _json(request, obj):
    return Response(request, json.dumps(obj), content_type="application/json")


def register_routes(server):
    """Attach the legacy HTTP routes to the given adafruit_httpserver Server.
    They are adapters: form fields in, the same handler functions as /rpc."""

    @server.route("/")
    def _root(request: Request):
        log("[http] GET / — serving setup page")
        return Response(request, HTML_SETUP, content_type="text/html")

    @server.route("/connect", POST)
    def _connect(request: Request):
        form = request.form_data
        ssid = _url_decode(form.get("ssid") or "").strip() if form else ""
        pw   = _url_decode(form.get("password") or "") if form else ""
        url  = _url_decode(form.get("script_url") or "").strip() if form else ""
        mf_raw = (_url_decode(form.get("module_firmware") or "").strip()
                  if form else "")
        pid  = _url_decode(form.get("product_id") or "").strip() if form else ""
        cd_raw = (_url_decode(form.get("config_defaults") or "").strip()
                  if form else "")

        if not ssid:
            # No network name entered — show the form again
            return Response(request, HTML_SETUP, content_type="text/html")

        # Optional: the manifest's module_firmware{} block (JSON string). Persisted
        # so the post-WiFi boot can compare installed vs required and flash any
        # outdated module headless. Absent (older app) -> OTA simply skipped.
        module_firmware = None
        if mf_raw:
            try:
                module_firmware = json.loads(mf_raw)
            except Exception as e:
                log(f"[ap] module_firmware parse failed ({e}) — ignoring")

        config_defaults = None
        if cd_raw:
            try:
                config_defaults = json.loads(cd_raw)
            except Exception as e:
                log(f"[ap] config_defaults parse failed ({e}) — ignoring")

        # Hand over to the provisioning loop (adapter over the `provision` op);
        # it acts after the page has been delivered to the browser. A network
        # the device cannot see is refused at once, hotspot kept (DEV-100);
        # "force" (any value) skips that check for a hidden network.
        res = _do_provision(ssid, pw, url, module_firmware, pid or None, config_defaults,
                            bool(form.get("force")) if form else False)
        if res.get("accepted") is False:
            msg = ('<p style="color:#c00">Your device cannot see the network "%s". '
                   'Check the name, or move the device closer to the router.</p>'
                   % ssid.replace("<", "&lt;"))
            return Response(request, HTML_SETUP.replace("<form", msg + "<form", 1),
                            content_type="text/html")
        return Response(request, HTML_SUCCESS, content_type="text/html")

    # ── Role assignment endpoints (v0.8) ─────────────────────────────────────
    # Called by the noknok app BEFORE /connect, while the phone is on the
    # noknok-setup AP. Transport-agnostic: no AP-specific logic lives here.

    @server.route("/roles/detect", POST)
    def _roles_detect(request: Request):
        """Ask the customer to interact with a module of a given type and return
        the UID of the one they touched.
        Form fields:
          module_type : "knob", "led_button", or "buzzer"
          exclude     : optional comma-separated uid_hex of already-assigned modules
        Response JSON: {"uid": "<hex>", "type": "<module_type>"} on detection,
                       or {"timeout": true} if nobody interacted in time.
        Blocks up to ~20 s — acceptable for a single-client setup interaction."""
        form = request.form_data
        module_type = (_url_decode(form.get("module_type") or "").strip()
                       if form else "")
        exclude_raw = (_url_decode(form.get("exclude") or "").strip()
                       if form else "")
        exclude = [u.strip() for u in exclude_raw.split(",") if u.strip()] \
            if exclude_raw else []

        # Detect only (no role_id) — the legacy two-step flow.
        res = _do_roles_assign(None, module_type, exclude)
        res.pop("saved", None)
        return _json(request, res)

    @server.route("/roles/save", POST)
    def _roles_save(request: Request):
        """Persist a role_id -> UID mapping to noknok_roles.json.
        Form fields: role_id, uid.
        Response JSON: {"ok": true} (or {"ok": false} if the write failed)."""
        form = request.form_data
        role_id = (_url_decode(form.get("role_id") or "").strip()
                   if form else "")
        uid = (_url_decode(form.get("uid") or "").strip()
               if form else "")

        log(f"[roles] /roles/save role_id='{role_id}' uid={uid}")

        if not role_id or not uid:
            return Response(request, json.dumps({"ok": False}),
                            content_type="application/json")

        c = get_conductor()
        ok = False
        if c is not None:
            try:
                ok = bool(c.append_role(role_id, uid))
            except Exception as e:
                log(f"[roles] append_role error: {e}")
                ok = False

        return Response(request, json.dumps({"ok": ok}),
                        content_type="application/json")

    @server.route("/roles/assign", POST)
    def _roles_assign(request: Request):
        """Detect which module the customer touches AND save the role in ONE
        round-trip. Avoids a fragile second request (the old /roles/save) right
        after the long blocking detect, which could fail against a single-client
        server that just came out of a multi-second block.
        Form fields:
          role_id     : role to assign (e.g. "ok_button")
          module_type : "knob", "led_button", or "buzzer"
          exclude     : optional comma-separated uid_hex of already-assigned modules
        Response JSON: {"uid": "<hex>", "saved": true} on success,
                       {"uid": "<hex>", "saved": false} if detected but write failed,
                       or {"timeout": true} if nobody interacted in time."""
        form = request.form_data
        role_id = (_url_decode(form.get("role_id") or "").strip()
                   if form else "")
        module_type = (_url_decode(form.get("module_type") or "").strip()
                       if form else "")
        exclude_raw = (_url_decode(form.get("exclude") or "").strip()
                       if form else "")
        exclude = [u.strip() for u in exclude_raw.split(",") if u.strip()] \
            if exclude_raw else []

        return _json(request, _do_roles_assign(role_id, module_type, exclude))

    # Captive-portal probe paths: serving the setup page (instead of the
    # expected 204/empty) makes iOS/Android/Windows show a "Sign in to
    # network" prompt that opens our page automatically.
    @server.route("/hotspot-detect.html")        # iOS / macOS
    @server.route("/library/test/success.html")  # iOS fallback
    @server.route("/generate_204")               # Android
    @server.route("/gen_204")                    # Android
    @server.route("/ncsi.txt")                   # Windows
    @server.route("/connecttest.txt")            # Windows
    @server.route("/canonical.html")             # Firefox
    @server.route("/redirect")                   # generic
    def _captive(request: Request):
        log(f"[http] captive probe {request.path} — serving setup page")
        return Response(request, HTML_SETUP, content_type="text/html")

# ── AP provisioning ────────────────────────────────────────────────────────────

def _join_failed(ssid, reason, attempts, detail):
    """Remember a failed setup join for hello (RAM only) and tell the customer
    on the device itself: buzzer/LED Buttons if present, and the Pico's own LED
    (DEV-100). The hotspot comes back right after."""
    global _last_join
    _last_join = {"join_id": pending.get("join_id"), "ssid": ssid, "ok": False,
                  "reason": reason, "attempts": attempts, "detail": detail}
    _field_alerts.append("wifi join failed: %s" % reason)
    alert_customer()
    _blink_onboard_led()

def run_ap_provisioning():
    """
    Start a WiFi hotspot + HTTP server and wait for credentials.
    Retries (restarts the AP) if the WiFi join fails.
    """
    while True:  # outer loop lets us rebuild the AP after a failed WiFi attempt
        _scan_before_ap()
        wifi.radio.start_ap(ssid=AP_SSID, password=AP_PASSWORD)
        ap_ip = str(wifi.radio.ipv4_address_ap)
        log(f"[ap] Hotspot started: '{AP_SSID}' — http://{ap_ip}")

        pool   = socketpool.SocketPool(wifi.radio)
        # One listener on port 80 (plain http://192.168.4.1 must work): the
        # /rpc carrier plus the legacy routes and captive-portal pages on the
        # same adafruit_httpserver Server.
        register_ops()
        server = rpc.start_http(pool, port=80, logfn=log).server
        register_routes(server)
        log(f"[ap] HTTP server listening — open http://{ap_ip}  (/rpc + legacy routes)")

        # Reset state and serve requests until credentials arrive
        pending["ready"] = False
        last_beat = time.monotonic()
        while not pending["ready"]:
            rpc.service(force=True)          # poll + deferred work (reboot etc.)
            now = time.monotonic()
            if now - last_beat > 5:
                log("[ap] waiting for setup… (server alive)")
                last_beat = now
            time.sleep(0.01)

        # Credentials received. DEV-141: keep the hotspot and the server up
        # until the app acks the reply (hello args.ack) or REPLY_GRACE_S runs
        # out (browser form, older app), so a lost frame can be retransmitted.
        end = time.monotonic() + REPLY_GRACE_S
        while time.monotonic() < end and not pending.get("acked"):
            rpc.service(force=True)
            time.sleep(0.01)
        if pending.get("acked"):
            log("[ap] App confirmed the reply — dropping the hotspot")
            for _ in range(20):              # ~0.2 s: let the ack's own reply leave
                rpc.service(force=True)
                time.sleep(0.01)
        else:
            log(f"[ap] No ack from the app within {REPLY_GRACE_S} s — continuing")
        ssid = pending["ssid"]
        pw   = pending["password"]
        su   = pending["script_url"]
        mf   = pending["module_firmware"]
        pid  = pending["product_id"]

        rpc.stop_http()                          # listener dies with the AP
        _stop_ap()
        log("[ap] Hotspot stopped — attempting WiFi join")

        # The Pico W radio often fails the FIRST join right after AP mode with
        # "Unknown failure 205", then succeeds on a retry. Try a few times
        # before giving up — otherwise a transient blip kicks the user back to
        # re-entering credentials in the app.
        joined = False
        reason = None
        attempts = 0
        for attempt in range(1, 4):
            attempts = attempt
            log(f"[ap] WiFi join attempt {attempt}/3")
            if connect_wifi(ssid, pw):
                joined = True
                break
            reason = _join_reason(_wifi_error)
            if reason == "auth":
                break               # a wrong password does not get better (DEV-100)
            if attempt < 3:
                time.sleep(3)

        if joined:
            # Credentials verified. Save them, then do a FULL hardware reset.
            # We do NOT download here: this radio was just in AP mode, and the
            # AP->STA transition (without a chip reset) leaves DNS broken.
            # A hardware reset brings the radio up clean in STA-only mode, and
            # main() will then connect + download on the fresh boot.
            if save_wifi_credentials(ssid, pw, su, mf, pid):
                _install_settings(pending["config_defaults"])
                log("[boot] Credentials saved — hardware reset into WiFi mode")
                time.sleep(2)  # let the success page flush to the browser
                microcontroller.reset()
                return
            # Filesystem read-only (drive visible to a PC): the app already saw
            # the success page, so say it loudly here and offer setup again
            # rather than reboot into a brain that has nothing saved.
            event("[CFG] provisioning NOT saved — filesystem read-only "
                  "(settings.toml NOKNOK_USB_DRIVE = 1?)")
            _join_failed(ssid, "not_saved", attempts, "filesystem read-only")
            pending["ready"] = False
            time.sleep(1)
            # loop back to top -> start_ap again
        else:
            # WiFi join failed after all retries — restart the AP so the user
            # can retry. We do NOT supervisor.reload() here: a soft reload leaves
            # the CYW43 radio in a state where AP mode no longer works.
            log(f"[ap] WiFi join failed ({reason}, {attempts} attempt(s)) — "
                "restarting hotspot for retry")
            _join_failed(ssid, reason, attempts, _wifi_error)
            pending["ready"] = False
            time.sleep(1)
            # loop back to top -> start_ap again

# ── Module firmware OTA (v0.10) ─────────────────────────────────────────────────

def _release_conductor():
    """Free the cached Conductor's I2C buses (A and B) so product.py can create
    its own. busio.I2C can only own the pins once per process — without this, the
    product script's Conductor() would collide with the one code.py created here."""
    global _conductor
    if _conductor is not None:
        try:
            _conductor.deinit()
        except Exception:
            pass
        _conductor = None

# Official module registry: module type -> where that module publishes its
# firmware index. One fetch, and the only place a module repo's location is
# written down — adding a module type is a line in that file, not a Pico update.
MODULE_REGISTRY_URL = ("https://raw.githubusercontent.com/buildwithnoknok/"
                       "Ecosystem/main/software/modules.json")


def _download_json(session, url, timeout=20):
    """Fetch and parse a small JSON document. Raises on anything unexpected."""
    resp = session.get(url, timeout=timeout)
    try:
        if resp.status_code != 200:
            raise ValueError("HTTP %d" % resp.status_code)
        return resp.json()
    finally:
        resp.close()


def _resolve_module_firmware(session, module_firmware):
    """Turn the manifest's floors into concrete firmware to install.

    In:  {"buzzer": {"min": "3.3.1"}}                    (the product manifest)
    Out: {"buzzer": {"version": "3.5.0", "url": "...", "layout": 2}}
                                                          (what to actually flash)

    Module firmware is backwards compatible, so a product does not pin a version
    — it states the oldest it works against and takes whatever the module
    currently publishes. The current version lives in the module's own repo next
    to the binary (firmware/index.json), which is what stops the version and the
    bytes drifting apart. See Ecosystem/software/firmware-index.md.

    Every failure here is a safe no-op: a type we cannot resolve is simply left
    out, so firmware_report() sees no required version and flashes nothing."""
    # Boot-time budget. This runs on every connected boot, before the product
    # starts, so a slow or absent uplink must cost seconds, not the sum of every
    # timeout. If the registry itself is not reachable quickly, the whole pass is
    # skipped and the product starts; the next boot tries again.
    try:
        registry_doc = _download_json(session, MODULE_REGISTRY_URL,
                                      timeout=REGISTRY_TIMEOUT_S) or {}
    except Exception as e:
        log(f"[fw] module registry unavailable ({e!r}) — no firmware will be installed")
        return {}, None
    if int(registry_doc.get("format", 1)) > INDEX_FORMAT:
        log(f"[fw] registry format {registry_doc.get('format')} is newer than this brain "
            f"understands ({INDEX_FORMAT}) — no firmware will be installed")
        return {}, None
    registry = registry_doc.get("modules", {})
    stage1   = _resolve_stage1(session, registry_doc)

    resolved = {}
    for mtype, spec in (module_firmware or {}).items():
        minimum = spec.get("min") if isinstance(spec, dict) else None
        entry   = registry.get(mtype)
        if not entry or not entry.get("index"):
            log(f"[fw] {mtype}: not in the module registry — skipping")
            continue
        try:
            index = _download_json(session, entry["index"], timeout=INDEX_TIMEOUT_S) or {}
        except Exception as e:
            log(f"[fw] {mtype}: index fetch failed ({e!r}) — skipping")
            continue

        if int(index.get("format", 1)) > INDEX_FORMAT:
            log(f"[fw] {mtype}: index format {index.get('format')} is newer than this "
                f"brain understands — skipping")
            continue
        version = index.get("version")
        url     = index.get("url")
        if not version or not url:
            log(f"[fw] {mtype}: index is missing version/url — skipping")
            continue

        # The floor guards one case: a product published against a firmware
        # feature that has not actually shipped. It should never fire.
        if minimum and _semver_lt(version, minimum):
            log(f"[fw] {mtype}: published {version} is older than the product's "
                f"minimum {minimum} — skipping")
            continue

        resolved[mtype] = {"version": version, "url": url,
                           "layout": index.get("layout"),
                           "size":   index.get("size"),
                           "crc32":  index.get("crc32")}
        if index.get("size") is None or index.get("crc32") is None:
            log(f"[fw] {mtype}: index has no size/crc32 — download will not be "
                f"integrity-checked (add them to firmware/index.json)")
        log(f"[fw] {mtype}: current is {version} (min {minimum or 'none'}, "
            f"layout {index.get('layout', '-')})")
    return resolved, stage1


def _resolve_stage1(session, registry_doc):
    """The current stage-1 bootloader, from the registry's `bootloader.stage1`
    entry, or None. Same index shape as a module: {version, url, layout, size,
    crc32}. All five are required here — a bootloader update destroys the app
    region and there is no rollback, so nothing about it is guessed."""
    entry = (registry_doc.get("bootloader") or {}).get("stage1")
    if not entry or not entry.get("index"):
        return None
    try:
        idx = _download_json(session, entry["index"], timeout=INDEX_TIMEOUT_S) or {}
    except Exception as e:
        log(f"[bl] stage-1 index fetch failed ({e!r}) — bootloader updates skipped")
        return None
    if int(idx.get("format", 1)) > INDEX_FORMAT:
        log("[bl] stage-1 index format is newer than this brain understands — skipped")
        return None
    for k in ("version", "url", "layout", "size", "crc32"):
        if idx.get(k) is None:
            log(f"[bl] stage-1 index is missing {k} — bootloader updates skipped")
            return None
    log(f"[bl] current stage-1 is {idx['version']} (layout {idx['layout']})")
    return {"version": idx["version"], "url": idx["url"], "layout": int(idx["layout"]),
            "size": idx["size"], "crc32": idx["crc32"]}


def _semver3(s):
    try:
        return tuple(int(x) for x in str(s).split("."))[:3]
    except (ValueError, AttributeError):
        return None


# Module types whose bootloader is another chip family than the stage-1 published in
# the registry (the CH32V003 one): their stage-1 is never touched by _stage1_pass().
# The PicoHub's CH32X035 stage-1 (layout 3) has its own version line; an over-the-air
# X035 stage-1 rollout would need its own registry entry (not wired yet, DEV-124).
STAGE1_OWN_LINE = {"picohub": "CH32X035"}

def _stage1_pass(c, s1):
    """Bring every I2C module's stage-1 bootloader up to the published version.

    This is what makes DEV-31's promise real in the field: a bootloader bug
    found after the batch ships is fixed over the air, not with a clamp.

    Rules, all from the runbook (Ecosystem/software/bootloader-update.md):
      - same transfer as an app update, then the app is pushed back — so the
        module's CURRENT app image must be in the cache, for the SAME layout;
      - a cross-layout stage-1 is refused by the module itself (error 8), so
        it is refused here first rather than transferred and rejected;
      - a legacy monolithic bootloader (no 0xB1) cannot self-update — SWD only.
    Reading a module's stage-1 version costs a bootloader round-trip and a
    re-enumeration, so it is done once per module and remembered as "bl" in
    the Conductor's saved state (the Store, not a file); after that a newer
    published stage-1 is a free compare."""
    if not s1:
        return
    want = _semver3(s1["version"])
    try:
        s1_image, s1_meta = _cache_read("stage1")
    except ValueError as e:
        log(f"[bl] no usable stage-1 image in the cache ({e}) — skipped")
        return
    if s1_meta.get("version") != s1["version"]:
        log(f"[bl] cache holds stage-1 {s1_meta.get('version')}, current is "
            f"{s1['version']} (fetch failed?) — skipped")
        return

    targets = []
    for list_attr, mf_key in c._FW_GROUPS:
        for m in getattr(c, list_attr, []):
            targets.append((mf_key, getattr(m, "_uid_hex", None)))

    for mf_key, uid in targets:
        m = c.by_uid(uid) if uid else None
        if m is None:
            continue
        if mf_key in STAGE1_OWN_LINE:
            # Another chip family: its stage-1 has its own versions and layout, so the
            # published (CH32V003) stage-1 is not for it. Without this the pass would
            # compare unrelated version numbers and raise a field alert every day.
            log(f"[bl] {mf_key} {uid}: {STAGE1_OWN_LINE[mf_key]} bootloader, own stage-1 line — skipped")
            continue
        entry = {"bus": "i2c", "address": m.address, "uid": uid, "type": mf_key}
        if not hasattr(m, "bootloader"):
            log(f"[bl] {mf_key} {uid}: reading stage-1 version (once)")
            try:
                c.bootloader_version(entry)         # sets m.bootloader, saves state
            except Exception as e:
                log(f"[bl] {mf_key} {uid}: could not read bootloader ({e!r})")
                continue
            m = c.by_uid(uid)
            if m is None:
                continue
        bl = getattr(m, "bootloader", None)
        if bl is None:
            log(f"[bl] {mf_key} {uid}: legacy monolithic bootloader — cannot self-update (SWD)")
            continue
        have = (bl[1], bl[2], bl[3])
        layout = bl[4] if len(bl) > 4 and bl[4] else None
        if want is None or have >= want:
            continue
        if layout != s1["layout"]:
            why = "module stage-1 layout %s, published stage-1 is layout %s" % (layout, s1["layout"])
            log(f"[bl] {mf_key} {uid}: REFUSED — {why}")
            event(f"[BL]    {mf_key:<11} uid={uid} refused: {why}")
            _field_alerts.append("stage-1 refused %s" % uid)
            continue
        try:
            app_image, app_meta = _cache_read(mf_key)
        except ValueError as e:
            log(f"[bl] {mf_key} {uid}: no cached app to restore after the update ({e}) — skipped")
            _field_alerts.append("stage-1 no app %s" % uid)
            continue
        if app_meta.get("layout") != s1["layout"]:
            log(f"[bl] {mf_key} {uid}: cached app is layout {app_meta.get('layout')}, "
                f"stage-1 is layout {s1['layout']} — skipped")
            continue

        m = c.by_uid(uid)
        entry = {"bus": "i2c", "address": m.address, "uid": uid, "type": mf_key}
        log(f"[bl] {mf_key} {uid}: stage-1 {'.'.join(map(str, have))} -> {s1['version']}, "
            f"then restoring app {app_meta.get('version')}")
        try:
            res = c.stage1_update(entry, s1_image, app_image)
            after = res.get("after")
            ok = bool(after) and (after[1], after[2], after[3]) == want and res.get("app_restored")
        except Exception as e:
            after, ok = None, False
            log(f"[bl] {mf_key} {uid}: stage-1 update FAILED ({e!r})")
        # stage1_update() itself remembers the new "bl" since noknok.py 1.20 (DEV-106).
        if ok:
            event(f"[BL]    {mf_key:<11} uid={uid}  stage-1 {'.'.join(map(str, have))} -> "
                  f"{s1['version']}  OK (app {app_meta.get('version')} restored)")
        else:
            event(f"[BL]    {mf_key:<11} uid={uid}  stage-1 {'.'.join(map(str, have))} -> "
                  f"{s1['version']}  FAIL (after={after})")
            _field_alerts.append("stage-1 update failed %s" % uid)


def _semver_lt(a, b):
    """True if semver string `a` is older than `b`. Unparseable sorts as older,
    which keeps the caller on the cautious side."""
    def parts(s):
        try:
            return tuple(int(x) for x in str(s).split("."))
        except (ValueError, AttributeError):
            return None
    pa, pb = parts(a), parts(b)
    if pa is None:
        return True
    if pb is None:
        return False
    return pa < pb


def _bootloader_gate(c, entries, layout):
    """DEV-31: refuse an image the module's bootloader cannot actually run.

    Backwards compatibility is a promise about the PROTOCOL, not about
    installability. An app image is linked for one flash layout (where the app
    base is); the bootloader writes at the base IT knows; the CRC is over image
    bytes, not the link address — so a wrong-layout image passes every check
    and then hangs the module. Nothing downstream would catch it.

    The match is EXACT. "Newer bootloader" is not "compatible": layout 1 and
    layout 2 both answer 0xB1 and are mutually unrunnable. That coarser
    legacy-vs-stage-1 check is exactly what hung the bench buzzer on 12 Sep.

    Fails closed: an index with no layout, or a stage-1 version this library
    does not know, is refused for I2C modules rather than guessed at. USB
    modules are skipped (no stage-0 port yet, V203 pending).

    Gates PER MODULE. Returns a list of (uid, reason) for the modules that
    must not receive this image; the rest of the type still updates. A first
    version refused the whole type if any one module disagreed — which meant
    one older spare LED Button would have blocked nine good ones for ever."""
    refused = []
    for e in entries:
        if e.get("bus") != "i2c":
            continue
        uid = e.get("uid")
        if layout is None:
            refused.append((uid, "index declares no layout for an I2C module"))
            continue
        try:
            m = c.by_uid(uid) if uid else None
            if m is not None and hasattr(m, "bootloader"):
                actual = c.layout_of(m.bootloader)     # remembered — no round-trip
            else:
                actual = c.bootloader_layout(e)        # first time: read and remember
        except Exception as ex:
            refused.append((uid, "could not read bootloader: %r" % (ex,)))
            continue
        if actual is None:
            refused.append((uid, "runs a stage-1 this Conductor does not know"))
        elif actual != layout:
            refused.append((uid, "is flash layout %d, image is layout %d" % (actual, layout)))
    return refused


# Boot-time budget for the OTA pass (see _resolve_module_firmware). The registry
# is the first fetch: if it is not back in this many seconds the uplink is not
# usable for updates right now and the product should just start.
REGISTRY_TIMEOUT_S = 6
INDEX_TIMEOUT_S    = 8

# Index / registry format the resolver understands. A file declaring a higher
# format is skipped (fail closed) so a future schema change cannot be
# misread by an older brain; absent = 1.
INDEX_FORMAT = 1

# ── How often the OTA pass actually asks GitHub ────────────────────────────────
# Resolving means registry + one index per module type — N+1 round-trips before
# the product starts. Doing that on every boot is wasteful and, on a slow
# uplink, slow. So: at most once per OTA_CHECK_INTERVAL_S, with the time of the
# last completed check kept in microcontroller.nvm (no filesystem write, and it
# survives a power cycle). Needs a synced clock; without NTP the check simply
# runs, which is the safe direction. The very first connected boot after
# provisioning is always due — and it also warms the rescue cache for every
# type the product uses, so a module parked later is rescuable from day one
# without the internet, not only the types that happened to need an update.
OTA_CHECK_INTERVAL_S = 24 * 3600
OTA_NVM_INDEX        = 1               # 4 bytes, little-endian unix time

def _last_ota_check():
    try:
        b = microcontroller.nvm[OTA_NVM_INDEX:OTA_NVM_INDEX + 4]
        t = int.from_bytes(bytes(b), "little")
        return t if 1_600_000_000 < t < 4_000_000_000 else None
    except Exception:
        return None

def _mark_ota_checked():
    if not _rtc_synced:
        return
    try:
        microcontroller.nvm[OTA_NVM_INDEX:OTA_NVM_INDEX + 4] = \
            int(time.time()).to_bytes(4, "little")
    except Exception:
        pass

def _ota_check_due():
    """True unless a completed check is on record less than the interval ago."""
    if not _rtc_synced:
        return True
    last = _last_ota_check()
    if last is None:
        return True
    return (time.time() - last) >= OTA_CHECK_INTERVAL_S

# ── A clean heap for the product after an online OTA pass (DEV-132) ────────────
# The online pass (TLS session, registry/index fetches, downloads, flash
# buffers) leaves the heap fragmented: the 36 KB Smart Lamp Maxi then failed
# 9-20 s after it started with "MemoryError ... allocating 17920 bytes" on 5 of
# 6 boots whose check was due (0 of ~15 quiet boots); free memory 35 KB vs
# 75-100 KB. gc.collect() cannot defragment, so after a pass that went online
# the brain reloads once (fresh VM, same WiFi) and the product starts on the
# quiet path. A marker byte in nvm makes that reloaded run skip the pass, so a
# check that stays due (GitHub unreachable) can never loop.
POST_OTA_NVM_INDEX = 5               # one byte; nvm[0]=crash, [1:5]=OTA time, [64:]=Store
POST_OTA_MAGIC     = 0xA5
_ota_went_online   = False           # check_and_flash_modules() passed the due check
_skip_ota_once     = False           # this run IS the reload after a pass

def _take_post_ota_marker():
    """True (and clears it) when the previous run reloaded after its OTA pass."""
    try:
        if microcontroller.nvm[POST_OTA_NVM_INDEX] == POST_OTA_MAGIC:
            microcontroller.nvm[POST_OTA_NVM_INDEX] = 0
            return True
    except Exception:
        pass
    return False

def _reload_for_clean_heap():
    """After an online OTA pass: set the marker and reload. No marker, no reload."""
    if not _ota_went_online:
        return
    try:
        microcontroller.nvm[POST_OTA_NVM_INDEX] = POST_OTA_MAGIC
    except Exception as e:
        log(f"[boot] cannot set the post-OTA marker ({e!r}) — starting without the reload")
        return
    log("[boot] OTA pass done — reloading once for a clean heap before the product (DEV-132)")
    supervisor.reload()

# ── Telling the customer something went wrong ──────────────────────────────────
# Every OTA / rescue outcome is otherwise log-only, and a customer never reads
# a log. Until the app can show device status over the home network, the
# modules themselves are the only channel we have: on any refused or failed
# update or rescue, the buzzer plays its error motif and the LED Buttons flash
# red for a moment, before the product starts. Best-effort, offline-capable,
# unmistakable next to the product's own startup sounds.
_field_alerts = []

def alert_customer():
    if not _field_alerts:
        return
    log(f"[alert] {len(_field_alerts)} problem(s) this boot: {_field_alerts}")
    try:
        c = get_conductor()
        if c is None:
            return
        for m in getattr(c, "ledbutton", []):
            try:
                m.set_color(255, 0, 0)
            except Exception:
                pass
        if getattr(c, "buzzer", []):
            try:
                c.buzzer[0].tune(4)          # BEEP_ERROR
            except Exception:
                pass
        time.sleep(1.5)
        for m in getattr(c, "ledbutton", []):
            try:
                m.led_off()
            except Exception:
                pass
    except Exception as e:
        log(f"[alert] cue failed (ignored): {e!r}")
    finally:
        _release_conductor()

# ── On-device firmware cache ────────────────────────────────────────────────────
# Every image the OTA pass downloads is kept (one per module type, overwritten
# by the next fetch of that type) with a sidecar describing it. That is the
# rescue source when there is no internet: a module parked after a power cut
# gets its last-known-good app back from the Pico itself. Costs nothing extra —
# the fetch already wrote the file; we just stop deleting it. The sidecar's own
# size/crc32 are re-checked on read, so a cache file left half-written by a
# power cut during the fetch is rejected rather than flashed.

def _cache_paths(mtype):
    return "%s/fw_%s.bin" % (nk.DATA_DIR, mtype), "%s/fw_%s.json" % (nk.DATA_DIR, mtype)

def _cache_image(mtype, image, spec):
    """Store a verified image + sidecar. Returns the .bin path. Sidecar carries
    what rescue needs to trust it later: version, layout, size, crc32."""
    from module_flasher import crc32 as _crc
    bin_path, meta_path = _cache_paths(mtype)
    meta = {"version": spec.get("version"), "layout": spec.get("layout"),
            "size": len(image), "crc32": "%08x" % _crc(image)}
    # Both atomic, image first (DEV-18): a power cut between the two leaves an
    # old sidecar next to a new image, which the size/crc32 check on read
    # rejects — never a half-written file under the real name.
    with nk.writable():                         # one window for the pair
        nk.write_atomic(bin_path, image)
        nk.write_atomic(meta_path, json.dumps(meta))
    return bin_path

def _cache_meta(mtype):
    """The sidecar for a type, or None. Cheap: no image read."""
    _, meta_path = _cache_paths(mtype)
    try:
        with open(meta_path, "r") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None

def _cache_read(mtype):
    """(image, meta) from the cache, integrity-checked against the sidecar's
    own size/crc32 — so a file left half-written by a power cut during the
    fetch is rejected, never flashed. Raises with a clear reason."""
    bin_path, meta_path = _cache_paths(mtype)
    try:
        with open(meta_path, "r") as fh:
            meta = json.load(fh)
        with open(bin_path, "rb") as fh:
            image = fh.read()
    except (OSError, ValueError):
        raise ValueError("no cached image for %r" % mtype)
    _verify_image(image, meta.get("size"), meta.get("crc32"))
    return image, meta

def _cached_image(entry):
    """Rescue image for a parked module: the cache for entry['type'], layout-
    checked against the module's own bootloader layout. A wrong-layout image
    would just hang it again."""
    image, meta = _cache_read(entry["type"])
    have, need = entry.get("bootloader_layout"), meta.get("layout")
    if need is None or have is None or have != need:
        raise ValueError("cached image layout %s != module layout %s" % (need, have))
    log(f"[rescue] using cached {entry['type']} {meta.get('version')} ({len(image)} B)")
    return image

def rescue_offline():
    """Recover a parked module with no internet: run the rescue with the cache
    as the only image source. Used on the offline boot path (A1). Releases the
    Conductor afterwards so product.py can create its own."""
    try:
        get_conductor(rescue_get_image=_cached_image)
    except Exception as e:
        log(f"[rescue] offline rescue error (ignored): {e!r}")
    _release_conductor()


# ── Product crash recovery ──────────────────────────────────────────────────────
# Three strikes, mirroring stage-1's rule for a module app. A crashing
# product.py is restarted by a soft reload (which also releases the I2C pins it
# held); the strike count lives in microcontroller.nvm — a tiny flash region
# separate from the FAT filesystem, so it involves none of the DEV-18 risk —
# and it is cleared on a power-on / hard reset, so a power cycle always grants
# a fresh three tries. On the third strike the device reloads once more and
# the clean boot goes straight to a safe idle that still answers the factory-
# reset gesture (DEV-7), rather than a dead "Code done running" prompt the
# customer cannot see.
CRASH_NVM_INDEX  = 0            # one byte
CRASH_MAX        = 3
CRASH_MAGIC      = 0xC0         # high bits mark "this byte is ours"

def _crash_count():
    try:
        b = microcontroller.nvm[CRASH_NVM_INDEX]
        return (b & 0x0F) if (b & 0xF0) == CRASH_MAGIC else 0
    except Exception:
        return 0

def _set_crash_count(n):
    try:
        microcontroller.nvm[CRASH_NVM_INDEX] = CRASH_MAGIC | (n & 0x0F)
    except Exception:
        pass

# ── Safe-mode restarts (DEV-154) ───────────────────────────────────────────────
# safemode.py (see that file) restarts a brain that CircuitPython put into safe
# mode, at most SM_MAX times in a row, counting in nvm[6] and leaving the reason in
# nvm[7:23]. code.py turns the reason into a [SAFE] event (once) and sets the count
# back to 0 after a real power-on (someone replugged it) or after SM_STABLE_S of the
# product running - so rare faults months apart never add up to a dark lamp.
SM_NVM_INDEX  = 6
SM_MAGIC      = 0xA0
SM_MAX        = 2
SM_REASON_LEN = 16
SM_STABLE_S   = 600
_sm_clear_at  = None            # monotonic time to clear the count while the product runs

def _sm_count():
    try:
        b = microcontroller.nvm[SM_NVM_INDEX]
        return (b & 0x0F) if (b & 0xF0) == SM_MAGIC else 0
    except Exception:
        return 0

def _sm_clear():
    """Count back to 0 (one nvm write, only when it is not 0 already)."""
    try:
        if microcontroller.nvm[SM_NVM_INDEX] != SM_MAGIC:
            microcontroller.nvm[SM_NVM_INDEX] = SM_MAGIC
    except Exception:
        pass

def _sm_report():
    """[SAFE] event for a restart safemode.py made, then forget the reason."""
    try:
        lo = SM_NVM_INDEX + 1
        raw = bytes(microcontroller.nvm[lo:lo + SM_REASON_LEN])
        if raw == bytes(SM_REASON_LEN) or raw == b"\xff" * SM_REASON_LEN:
            return
        reason = raw.rstrip(b"\x00").decode("ascii", "replace")
        microcontroller.nvm[lo:lo + SM_REASON_LEN] = bytes(SM_REASON_LEN)
        event("[SAFE] CircuitPython safe mode (%s) - restarted by safemode.py, %d of %d in a row"
              % (reason, _sm_count(), SM_MAX))
    except Exception as e:
        log(f"[safe] report skipped ({e!r})")

def _service():
    """The drivers' servicing slot (noknok.set_service_hook): the app channel, and
    once SM_STABLE_S after the product started, the safe-mode count back to 0."""
    global _sm_clear_at
    rpc.service()
    if _sm_clear_at is not None and time.monotonic() >= _sm_clear_at:
        _sm_clear_at = None
        _sm_clear()
        log("[safe] product ran %d s - safe-mode restart count back to 0" % SM_STABLE_S)

def _is_fresh_start():
    """True on a power-on / hard reset, False after supervisor.reload().
    supervisor.runtime.run_reason is the right signal: microcontroller.cpu.
    reset_reason does not change across a VM reload, so keying on it would clear
    the strike count on every restart and the counter would never reach three."""
    try:
        return supervisor.runtime.run_reason == supervisor.RunReason.STARTUP
    except Exception:
        return False


def _download_image(session, url, timeout=30, size=None, crc32=None):
    """Download one module's offset-linked app .bin over WiFi (BINARY, not .text).
    Returns bytes. Raises on any connectivity/HTTP/size failure — update_all()
    (noknok.py) catches it, marks that module FAILED and moves on to the rest.

    `size` / `crc32` come from the module's index.json and are checked before
    the bytes go anywhere near a module. The bootloader's own CRC cannot catch
    a truncated or corrupted download — it is computed over whatever we send —
    so without this a short download would be flashed, pass verification, hang
    the module, get parked, and be downloaded again."""
    resp = session.get(url, timeout=timeout)
    try:
        if resp.status_code != 200:
            raise ValueError("HTTP %d" % resp.status_code)
        image = resp.content
    finally:
        resp.close()
    if len(image) < 256:  # a real app image is KBs; this size means an error
        raise ValueError("image too small (%d bytes) — not a firmware binary" % len(image))
    _verify_image(image, size, crc32)
    return image


def _verify_image(image, size, crc32):
    """Raise unless `image` matches the declared size and CRC32 (zlib). Either
    may be None (older index.json) — then that check is skipped."""
    if size is not None and len(image) != int(size):
        raise ValueError("image size %d != declared %d" % (len(image), int(size)))
    if crc32 is not None:
        from module_flasher import crc32 as _crc
        got = _crc(image)
        want = int(crc32, 16) if isinstance(crc32, str) else int(crc32)
        if got != want:
            raise ValueError("image crc32 %08x != declared %08x" % (got, want))

# Firmware of the board the brain sits on, resolved and updated on every OTA pass
# whatever the product declares (manifest key -> floor spec, like module_firmware).
CARRIER_FIRMWARE = {"picohub": {}}

def check_and_flash_modules(module_firmware):
    """Bring every connected module (I2C + USB) up to the manifest's
    module_firmware{} versions before product.py runs. Headless (Pico already
    on WiFi). Routes through the Conductor's bus-aware update_all() (noknok.py,
    DEV-12) so both buses go through ONE call — update_module() inside it picks
    module_flasher.ModuleFlasher (I2C) or noknok_usb.UsbModuleFlasher (USB), and
    it re-enumerates afterwards so module instances/addresses are fresh. The only
    WiFi-specific piece is get_image() below, injected per DEV-12's design so the
    dispatcher itself stays network-agnostic and bench-testable (update_demo.py
    injects a local-file get_image instead).

    Runs in three passes — decide, fetch, flash — so that the common boot costs
    nothing and an interrupted one cannot leave the bus half-updated. See the
    comments at each pass for why that order matters.

    Best-effort + crash-safe: any failure is logged and swallowed, and none
    blocks the rest of the boot. A failed I2C flash leaves that module safe in
    its bootloader at 0x7E; the rescue pass (DEV-31) picks it up on the next
    boot, whatever that boot does: offline boots, "not due" boots and the
    due-boot early returns below (no module_firmware, HTTPS setup failed,
    nothing resolved) all run it from the on-device cache (DEV-97); the full
    online pass runs it with a freshly refreshed cache. If it cannot help (no
    cached image, wrong layout, unknown module), _rescue_parked() reports it
    loudly and the next boot tries again. A USB module is updated around a hard
    reset (DEV-133): asked into its bootloader here, flashed from the cache by
    usb_fw_pass() on the next boot (which also rescues any module found at PID
    4E42), verified on the boot after."""
    global _ota_went_online
    if _skip_ota_once:
        # The reload right after today's pass (DEV-132): no network work again.
        _rescue_from_cache("first run after the post-OTA reload")
        return
    # The carrier's firmware is not the product's business: always in (DEV-124).
    module_firmware = dict(module_firmware or {})
    for mtype, spec in CARRIER_FIRMWARE.items():
        module_firmware.setdefault(mtype, spec)
    if not module_firmware:
        # Nothing to update, but a parked module must still be rescued.
        _rescue_from_cache("product declares no module_firmware")
        return

    # HTTPS session (correct DNS/SSL for the radio), used for both the rescue
    # image and the pre-flash fetch below.
    try:
        pool    = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
        context = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
        session = adafruit_requests.Session(pool, context)
    except Exception as e:
        log(f"[fw] OTA setup failed: {e}")
        _rescue_from_cache("no HTTPS session")
        return

    # Not due yet? Then no GitHub round-trips at all this boot. The parked-
    # module rescue still runs — it is one I2C read — with the on-device cache
    # as its image source, exactly as on an offline boot.
    if not _ota_check_due():
        _rescue_from_cache("update check not due (last < 24 h ago)")
        return
    _ota_went_online = True          # from here on the heap gets churned (DEV-132)
    # Resolve the manifest's floors into concrete versions + URLs via the module
    # registry. Two small JSON fetches; everything downstream then works on the
    # {version, url} shape firmware_report() has always taken.
    resolved, stage1 = _resolve_module_firmware(session, module_firmware)
    if not resolved:
        # GitHub unreachable or the registry/index unusable: no update this
        # boot, but the cache can still rescue a parked module.
        _rescue_from_cache("nothing resolved (registry unreachable?)")
        return

    # ── Refresh the on-device cache, BEFORE any Conductor exists ──────────────
    # Every image this product could need, downloaded while no Conductor has
    # ever been created in this process. That is the one condition under which
    # HTTPS downloads on this board have always been reliable; a download made
    # after a Conductor existed — even one that has since been released — can
    # hang without timing out (seen 12 Sep: the boot stopped at the fetch and
    # never reached the product). So all network work happens here, and the
    # flash step later reads from these files and never touches the radio.
    # Cost: one download per type per published firmware version, at most once
    # a day. The first check after provisioning fills the cache for every type,
    # which is also what makes offline rescue work from day one.
    to_cache = list(resolved.items())
    if stage1:
        to_cache.append(("stage1", stage1))     # the bootloader is cached like any image
    for mtype, spec in to_cache:
        cached = _cache_meta(mtype)
        if cached and cached.get("version") == spec.get("version") \
                  and cached.get("crc32") == spec.get("crc32"):
            continue
        url = spec.get("url")
        if not url:
            continue
        try:
            image = _download_image(session, url, size=spec.get("size"),
                                    crc32=spec.get("crc32"))
            _cache_image(mtype, image, spec)
            log(f"[fw] cached {mtype} {spec.get('version')}: {len(image)} bytes")
        except Exception as e:
            log(f"[fw] fetch FAILED {mtype}: {e!r}")
            _field_alerts.append("fetch failed %s" % mtype)

    def rescue_image(entry):
        """A parked module is already in its bootloader, so its layout is known
        for free; the cache (just refreshed) is layout-checked against it."""
        return _cached_image(entry)

    # ── Pass 1: decide. No image downloads, no filesystem writes. ─────────────
    # The overwhelmingly common boot is "everything already current", and that
    # path must not write to flash at all: per-boot writes against the rw-
    # remounted CircuitPython filesystem are what corrupts it on power loss
    # (DEV-18). So we ask the modules their versions first and, in the normal
    # case, stop right here having written nothing.
    c = get_conductor(rescue_get_image=rescue_image)
    if c is None:
        log("[fw] no Conductor available — skipping firmware check")
        return

    # Bootloader first. A stage-1 update restores the module's current app as
    # part of the same transaction, so a module updated here comes out with
    # both current and drops out of the app pass below.
    try:
        _stage1_pass(c, stage1)
    except Exception as e:
        log(f"[bl] stage-1 pass error (ignored): {e!r}")

    todo = [r for r in c.log_firmware_report(resolved, logfn=log)
            if r["needs_update"]]
    if not todo:
        log("[fw] all modules up to date")
        _mark_ota_checked()
        return

    # Gate each outdated MODULE on whether its bootloader can run the new image.
    # Refused UIDs are handed to update_all() as an exclusion list; the type
    # stays resolved so its other modules still update.
    types = []
    for r in todo:
        if r["type"] not in types:
            types.append(r["type"])
    refused_uids = set()
    for mtype in types:
        for uid, why in _bootloader_gate(c, [r for r in todo if r["type"] == mtype],
                                         resolved[mtype].get("layout")):
            log(f"[fw] {mtype} {uid}: REFUSED — {why}")
            event(f"[FW]    {mtype:<11} uid={uid} refused: {why}")
            _field_alerts.append("refused %s %s" % (mtype, uid))
            refused_uids.add(uid)

    todo = [r for r in todo if r.get("uid") not in refused_uids]
    if not todo:
        log("[fw] every pending update was refused — nothing flashed")
        _mark_ota_checked()
        return
    log(f"[fw] {len(todo)} module(s) need an update — flashing from the cache")

    # ── Flash from the cache. The radio is not involved from here on. ─────────
    # Every image was fetched and verified before the Conductor existed, so a
    # WiFi drop now cannot leave one module half-written and the rest untouched.
    # The files stay afterwards — they ARE the rescue cache.
    def get_image(entry):
        spec = resolved.get(entry["type"]) or {}
        image, meta = _cache_read(entry["type"])
        if meta.get("version") != spec.get("version"):
            raise ValueError("cache holds %s, current is %s (fetch failed?)"
                             % (meta.get("version"), spec.get("version")))
        log(f"[fw] {entry['type']}: flashing {len(image)} bytes from the cache")
        return image

    def progress(done, total):
        if total and (done == total or done % 512 == 0):
            log(f"[fw] flashing {done}/{total} ({100 * done // total}%)")

    results = c.update_all(resolved, get_image, progress=progress, logfn=log,
                           exclude_uids=refused_uids)

    # update_all() already re-enumerated internally (both buses); pull a fresh
    # report to log the DEFINITIVE per-module outcome to the durable audit log —
    # the actually-confirmed installed version, not just "the flash call
    # returned OK" (a flash can succeed but a module can still misreport).
    verify = {v["uid"]: v for v in c.firmware_report(resolved)}
    for r in results:
        v   = verify.get(r["uid"])
        now = v["installed"] if v else None
        if r.get("pending"):
            # USB (DEV-133): asked into its bootloader; flashed after the hard
            # reset by usb_fw_pass(), verified on the boot after that.
            event(f"[FW]    {r['type']:<11} uid={r['uid']} bus={r['bus']}  "
                  f"{r['installed']} -> {r['required']}  PENDING (hard reset, then flash)")
            _need_hard_reset_for_usb("USB update of %s pending" % r["type"])
            continue
        if r["updated"] and r["type"] == "picohub":
            _need_hard_reset_for_usb("the PicoHub power manager was updated (J2 was off)")
        if r["updated"] and now == r["required"]:
            event(f"[FW]    {r['type']:<11} uid={r['uid']} bus={r['bus']}  "
                  f"{r['installed']} -> {r['required']}  OK (verified {now})")
        else:
            reason = r["error"] or ("reports %s" % now)
            event(f"[FW]    {r['type']:<11} uid={r['uid']} bus={r['bus']}  "
                  f"{r['installed']} -> {r['required']}  FAIL ({reason})")
            _field_alerts.append("update failed %s %s" % (r["type"], r["uid"]))
    _mark_ota_checked()

# ── Factory reset by boot-hold (v0.18) ────────────────────────────────────────
# "Hold any button while plugging in" — the one reset gesture every product
# shares, so product scripts need not reserve one. It needs enumerated modules
# (a module is not addressable before enumeration), hence a Conductor, and a
# Conductor before a download breaks the DEV-32 rule — so this runs only on
# the power-on run, which reloads into a fresh process right after (see the
# cold-boot workaround in main()). That fresh process has never had a Conductor.
BOOT_HOLD_S      = 3.0          # hold this long AFTER the modules were found
BOOT_HOLD_POLL_S = 0.05

def _boot_hold_reset_check():
    """Wipe + reboot into the setup AP if a button is held from power-on.
    Best-effort: any error means a normal boot, never a stuck one."""
    if not (load_wifi_credentials() or product_script_exists()):
        return                      # factory-fresh: nothing to reset, save the 3 s
    c = None
    try:
        from noknok import Conductor
        c = Conductor()
        if c.enumerate() == 0:      # I2C only: USB modules have nothing to press
            return
        pressables = list(c.ledbutton) + list(c.knob)
        if not pressables:
            return

        def held():
            for m in pressables:
                st = m.read()
                if st is not None and st.pressed:
                    return True
            return False

        if not held():
            return                  # the normal power-on

        # Seen: feedback on everything that can give it, then keep watching.
        print("[reset] button held at power-on — keep holding %.0f s to factory-reset"
              % BOOT_HOLD_S)
        for b in c.ledbutton:
            b.set_color(255, 255, 255)
        for z in c.buzzer:
            z.play(880, 60, 60)
        deadline = time.monotonic() + BOOT_HOLD_S
        while time.monotonic() < deadline:
            time.sleep(BOOT_HOLD_POLL_S)
            if not held():
                print("[reset] released — normal boot")
                for b in c.ledbutton:
                    b.led_off()
                return

        # Confirm, then wipe. factory_reset() hard-resets; we never return.
        # The sequence ends DARK on purpose: a reset does not power-cycle the
        # modules, so whatever the LEDs show last is what the customer keeps
        # seeing through the reboot — "went dark" reads as "done, let go",
        # "stayed white" read as "did it take?" (bench, 18 Sep).
        for _ in range(3):
            for b in c.ledbutton:
                b.led_off()
            time.sleep(0.15)
            for b in c.ledbutton:
                b.set_color(255, 255, 255)
            time.sleep(0.15)
        for b in c.ledbutton:
            b.led_off()
        for z in c.buzzer:
            z.play(1320, 120, 80)             # two rising notes = accepted
            time.sleep(0.15)
            z.play(1760, 200, 80)
        event("[RESET] factory reset via boot-hold")
        nk.factory_reset(delay=0.4)
    except Exception as e:
        print(f"[reset] boot-hold check skipped: {e!r}")
    finally:
        # Free the pins; the reload that follows starts a fresh VM anyway.
        try:
            if c is not None:
                c.deinit()
        except Exception:
            pass


# ── Main flow ──────────────────────────────────────────────────────────────────

def run_product(connected):
    """Run product.py with three-strikes crash recovery (see the crash-recovery
    block above). Never returns on the happy path — a product loops forever."""
    global _sm_clear_at
    strikes = _crash_count()
    if _sm_count():
        _sm_clear_at = time.monotonic() + SM_STABLE_S    # DEV-154: stable run clears it
    log(f"[boot] product.py present — running {product_script_path()}"
        + (f" (strike {strikes}/{CRASH_MAX})" if strikes else ""))
    try:
        exec(open(product_script_path()).read(), {"__name__": "__main__"})
        # A product that returns is a product that stopped: treat like a crash
        # so a script that falls off the end gets the same three tries.
        raise RuntimeError("product.py returned")
    except KeyboardInterrupt:
        raise                                   # bench Ctrl-C: not a crash
    except (Exception, SystemExit) as e:
        strikes += 1
        _set_crash_count(strikes)
        log(f"[crash] product.py {type(e).__name__}: {e}  (strike {strikes}/{CRASH_MAX})")
        try:
            import traceback
            traceback.print_exception(e)     # full trace to the serial console
        except Exception:
            pass
        event(f"[CRASH] product.py {type(e).__name__}: {str(e)[:120]}  "
              f"strike {strikes}/{CRASH_MAX}")
        if _flash_logging_enabled():
            flush_log_ring("product crash")  # bench only: a runtime FAT write (DEV-18)
        nk.flush_settings()                  # keep the customer's last changes
        # Always reload — a clean VM releases the I2C pins the product held. If
        # this was the third strike, main() sees the count and parks instead of
        # running the product again.
        log(f"[crash] restarting in {2 * strikes} s")
        time.sleep(2 * strikes)
        supervisor.reload()


def _h_bench_wifi_drop(args, msg):
    """Bench only (NOKNOK_WATCHDOG = 1): power the radio off for a few seconds
    to exercise the link watch + rejoin + carrier restart path."""
    secs = float(args.get("seconds") or 8)
    def _drop():
        log("[bench] dropping the WiFi link for %.0f s" % secs)
        wifi.radio.enabled = False
        end = time.monotonic() + secs
        while time.monotonic() < end:
            rpc._feed()                  # keep the bench watchdog quiet meanwhile
            time.sleep(0.5)
        wifi.radio.enabled = True
    rpc.defer(_drop)
    return {"dropping": secs}

def _after_join():
    """DEV-79: post-join work when the brain gets online while a product runs
    (it booted offline, or the router came back). Runs from rpc.defer(), outside
    any module transaction. Bounded: NTP waits at most 2 s, and only once."""
    log("[wifi] online while the product runs — post-join work")
    try:
        sync_time_ntp(socketpool.SocketPool(wifi.radio), timeout=2)
    except Exception as e:
        log(f"[ntp] after join skipped ({e!r})")
    if _mdns_wanted() and getattr(rpc, "_mdns", None) is None:
        rpc.start_mdns(80, log)

def _mdns_wanted():
    """DEV-70: mDNS is on unless settings.toml says NOKNOK_MDNS = 0."""
    return str(nk.env("NOKNOK_MDNS", "1")).strip() != "0"

def start_app_channel():
    """Home-WiFi /rpc + mDNS for the product's lifetime (DEV-34). After this,
    noknok.py's drivers pump the channel between module transactions, so the
    product answers the app without a line of its own. Called only once all
    network *client* work (OTA downloads) is over — the DEV-32 rule — and
    never on an offline brain (AP-on-demand is DEV-35). Best-effort."""
    try:
        register_ops()
        creds = load_wifi_credentials() or {}
        if creds.get("ssid"):
            # Lets the servicing slot re-join after a router reboot (and bring
            # the carrier up on a brain that booted offline) — rate-limited,
            # bounded, never inside a module read.
            rpc.set_wifi_credentials(creds["ssid"], creds.get("password") or "", 80,
                                     on_up=_after_join)
        if str(nk.env("NOKNOK_WATCHDOG", "0")) == "1":
            rpc.arm_watchdog(8, log)
            rpc.dispatcher().register("bench.wifi_drop", _h_bench_wifi_drop)
        nk.set_service_hook(_service)    # rpc.service + the DEV-154 stable-run check
        if not wifi.radio.connected:
            log("[rpc] offline — app channel will come up if the network appears")
            return
        rpc.start_http(socketpool.SocketPool(wifi.radio), port=80, logfn=log)
        # mDNS advertises noknok-XXXX.local, on by default since DEV-70 (three
        # clean bench nights; the 17 Sep idle hang never came back).
        # settings.toml NOKNOK_MDNS = 0 switches it off.
        if _mdns_wanted():
            rpc.start_mdns(80, log)
        else:
            log("[mdns] off (NOKNOK_MDNS = 0) — reach the brain by IP")
        log("[rpc] app channel up — http://%s/rpc  (%s)"
            % (wifi.radio.ipv4_address, rpc.device_name()))
    except Exception as e:
        log(f"[rpc] app channel NOT started (ignored): {e!r}")


def safe_idle(connected):
    """Parked after CRASH_MAX crashes. Two ways out, neither needing a laptop:
    hold the knob for the factory-reset gesture (DEV-7 — this used to be dead
    here), or a newer product.py published upstream — fetched once if we are
    online, and only acted on if it actually differs from the one that crashes.
    A power cycle also resets the strike count for three more tries."""
    log(f"[crash] product parked after {CRASH_MAX} crashes — safe idle. "
        f"Hold the knob to factory-reset.")
    event(f"[CRASH] parked after {CRASH_MAX} crashes")

    if connected:
        try:
            creds = load_wifi_credentials() or {}
            with open(product_script_path(), "r") as fh:
                current = fh.read()
            if download_and_save_script(creds.get("script_url")):
                with open(PRODUCT_SCRIPT_FILE, "r") as fh:
                    fresh = fh.read()
                if fresh != current:
                    log("[crash] a different product.py was published — trying it")
                    event("[CRASH] fresh product.py fetched, strikes reset")
                    _set_crash_count(0)
                    supervisor.reload()
                log("[crash] published product.py is identical — staying parked")
        except Exception as e:
            log(f"[crash] re-fetch skipped: {e!r}")

    c = get_conductor()
    knob = c.knob[0] if (c is not None and c.knob) else None
    if knob is None:
        log("[crash] no knob on the bus — power-cycle to retry")
    # Parked is exactly when the app must still get through (the "stopped"
    # card, DEV-36): the channel comes up after the re-fetch above (no more
    # client traffic) and this loop pumps it.
    start_app_channel()
    while True:
        if knob is not None:
            try:
                c.check_factory_reset(knob.read())
            except Exception:
                pass
        rpc.service()
        time.sleep(0.05)


def main():
    # ── Cold-boot WiFi workaround (17 Sep 2026, CircuitPython 10.3.0, Pico 2 W) ──
    # After a POWER-ON or hard reset every WiFi join times out ("Unknown
    # failure 1", never associates), 3/3 attempts, however long the timeout;
    # after a soft reload the same code joins in 3 s. Bench-bisected: a tiny
    # code.py joins fine cold, but a multi-second compile freeze right after the
    # radio's cold init (this file + noknok.py are ~230 KB of source) leaves it
    # wedged, and nothing from Python un-wedges it — not waiting, not
    # radio.enabled off/on, not stop_station(). Only a VM reset does. So on the
    # power-on run we do the fresh-start bookkeeping and reload once; the
    # second run joins. Costs ~3 s per power-on. Real fix = no boot-time
    # compile (precompiled .mpy / frozen modules, DEV-38) — then drop this.
    if _is_fresh_start():
        _set_crash_count(0)              # a power cycle always grants three fresh tries
        try:                             # DEV-154: a real replug grants fresh safe-mode restarts
            if microcontroller.cpu.reset_reason == microcontroller.ResetReason.POWER_ON:
                _sm_clear()
        except Exception:
            pass
        _boot_hold_reset_check()         # v0.18: button held from power-on? wipe + AP
        print("[boot] power-on — reloading once (cold-boot WiFi workaround, see main())")
        supervisor.reload()
    log_new_boot()
    log("[boot] noknok Pico W — starting")
    global _skip_ota_once
    _skip_ota_once = _take_post_ota_marker()     # DEV-132: read + clear every run
    try:
        # reset_reason survives a reload: not again on the reload after an update pass
        if (microcontroller.cpu.reset_reason == microcontroller.ResetReason.WATCHDOG
                and not _skip_ota_once):
            event("[WDT] watchdog reset — the previous run hung (bench watchdog)")
    except Exception:
        pass
    _sm_report()                         # DEV-154: a restart made by safemode.py
    # DEV-18: say which filesystem mode boot.py chose, and sweep any .tmp left
    # by a write that a power cut interrupted (the real file is untouched).
    log("[fs] CIRCUITPY drive %s — filesystem %s — runtime store: %s" % (
        ("VISIBLE to PCs (maker mode: program writes will fail)"
         if nk.usb_drive_visible() else "hidden"),
        "writable in short windows only" if not nk.usb_drive_visible()
        else "owned by the PC",
        nk.store().backend))
    nk.clean_tmp()
    _report_pin_config()                 # DEV-74: pins come only from settings.toml

    creds = load_wifi_credentials()
    if not creds:
        log("[boot] No credentials — starting AP provisioning")
        run_ap_provisioning()
        return

    log(f"[boot] wifi.json ssid='{creds.get('ssid')}' "
        f"script_url={'present' if creds.get('script_url') else 'MISSING'}")

    # Direct-boot WiFi join can be flaky right after a hardware reset (the
    # radio/AP may need a moment). Try a few times.
    connected = False
    for attempt in range(1, 4):
        log(f"[boot] WiFi join attempt {attempt}/3")
        if connect_wifi(creds["ssid"], creds["password"]):
            connected = True
            break
        if attempt < 3:
            time.sleep(3)
    log(f"[boot] connect_wifi result: {'CONNECTED' if connected else 'FAILED'}")

    if product_script_exists() and _crash_count() >= CRASH_MAX:
        safe_idle(connected)                 # never returns

    # The WiFi credentials are NEVER deleted here. A failed join means the
    # router is down, or out of range, or rebooting — not that the customer
    # wants to set the product up again. This used to wipe wifi.json after three
    # failed attempts and drop into AP mode, so a router hiccup at the wrong
    # moment forced a full re-setup in the app and, worse, the product would not
    # run at all until then. Only the factory-reset gesture deletes credentials.

    if connected:
        try:
            sync_time_ntp(socketpool.SocketPool(wifi.radio))
        except Exception as e:
            log(f"[ntp] setup error (ignored): {e}")

        # Pending product switch (provision over home WiFi): fetch + compile the
        # new script BEFORE any Conductor exists (DEV-32 rule) and only then
        # replace the running one. A failure keeps the old product and says so.
        switch = nk.store().get(SWITCH_STORE_KEY)
        if isinstance(switch, dict) and switch.get("script_url"):
            nk.store().delete(SWITCH_STORE_KEY)          # one attempt per request
            if download_and_save_script(switch["script_url"]):
                # Only now does the brain "become" the new product.
                save_wifi_credentials(creds["ssid"], creds.get("password") or "",
                                      switch["script_url"], switch.get("module_firmware"),
                                      switch.get("product_id"))
                creds = load_wifi_credentials() or creds
                _install_settings(switch.get("config_defaults"))
                event("[CFG] product switched -> %s" % switch["script_url"].rsplit("/", 1)[-1])
            else:
                event("[CFG] product switch FAILED (download) — keeping the current product")
                _field_alerts.append("product switch failed")

        if product_script_exists():
            # Bring connected modules up to current firmware BEFORE handing off
            # to the product (includes the parked-module rescue). Best-effort;
            # the bus is released afterwards so product.py can own it.
            try:
                check_and_flash_modules(creds.get("module_firmware"))
            except Exception as e:
                log(f"[fw] check_and_flash_modules error (ignored): {e!r}")
            _release_conductor()
            alert_customer()
            _hard_reset_if_needed()          # DEV-133/134: USB module moved -> fresh host
            _reload_for_clean_heap()         # DEV-132: only after an online OTA pass
            start_app_channel()              # after the last download, before the product
            run_product(connected=True)
        else:
            log("[boot] product.py missing — will download")
            if download_and_save_script(creds.get("script_url")):
                log("[boot] download OK — reloading to run product.py")
                supervisor.reload()
            log("[boot] download failed — no product to run; offering setup "
                "(credentials kept, retried on next boot)")
            run_ap_provisioning()
    else:
        if product_script_exists():
            # Offline: the product runs anyway. No OTA, no NTP; a module parked
            # after a power cut is still rescued, from the on-device cache.
            log("[boot] offline — running the product without updates")
            rescue_offline()
            alert_customer()
            _hard_reset_if_needed()      # DEV-133/134: a USB module was rescued
            start_app_channel()          # hook only; carrier comes up if the network appears
            run_product(connected=False)
        else:
            log("[boot] offline and no product.py — offering setup "
                "(credentials kept, retried on next boot)")
            run_ap_provisioning()

main()


