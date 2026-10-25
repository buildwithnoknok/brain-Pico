# tools/bench - test helpers for the brain bench

Small tools used by the QA runs (Bob) and anyone testing a brain on a bench host (a Pi with the
brain Pico on USB). They complement `tools/pico.py`, `tools/rpc_call.py` and the `software/bench_*.py`
scripts. Nothing here ships to a customer brain. MIT, like the rest of this repo.

| File | Runs on | What it does |
|---|---|---|
| `boot_capture.py [SECONDS]` | host | Waits for an unplug + replug, then records the boot from the first second. Sends nothing. |
| `serial_listen.py [SECONDS] [--reboot\|--ctrlc]` | host | Listen-only console reader (reopens after resets). `--reboot` soft-reboots first; `--ctrlc` tells "busy" from "console dead". |
| `yank_loop.sh [RUNS] [--runtime-only]` | host | Hands-free power-pull rounds with `software/bench_yank.py`: a person pulls during each tone, the loop records each boot and runs the next round. `--runtime-only` for brains with real setup data. |
| `dev107_store_cuts.sh [ROUNDS] [IP]` | host | DEV-107: power cut during a Store write, then probe the next boot. Default: the Pi cuts VBUS itself (`uhubctl -l 2`) 0.5-6 s into the tone; `HAND=1`: a person pulls during the tone. Even rounds record the boot from replug, odd rounds touch the console first at 5-30 s. On a dead console: `/rpc hello`, dmesg, listen + Ctrl-C, data-only port cycle. Logs `~/dev107/`. |
| `dev70_at.sh HH:MM HOURS PHASES...` | host | Starts `dev70_mdns_nights.sh` at a clock time today, plus a 15-min `/rpc status` link watcher (drops, rejoins, uptime) to `dev70/link3.log`. No USB contact during the run. |
| `rpc_failure_modes.sh <ip>` | host on the LAN | `/rpc` failure modes: bad values, reboot keeps settings, dead product URL keeps the old product. Resets settings at the end. |
| `wifi_offline.py IN OUT` | host | Offline copy of a brain's `wifi.json` (fake SSID, password emptied) for tests that need a brain without network. Prints key names only. |
| `bench_inventory.py` | Pico (`pico.py run`) | Read-only: library versions, `settings.toml` pins, WiFi SSID, pin problems, modules on every I2C bus `settings.toml` configures (bus A, bus B) and the USB host. |
| `bench_display_rotation.py` | Pico | Visual check of all four Display orientations (frame, corner markers, asymmetric text). |
| `crash_product.py` | Pico, as `/data/product.py` | Crashes on purpose: exercises 3-strike recovery and safe idle. |

Rules that cost time to learn:
- Only **one** program may read the Pico's serial port at a time (pico.py, a listener, Thonny).
- Find the port by id (`/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00`): a WCH-LinkE also has a serial port.
- After a power-on a brain needs ~25 s before the product runs; leave the console alone until then.
- Keep logs and backups in `~/` on the Pi, not `/tmp` (wiped on reboot).
- `wifi.json` holds a real WiFi password: never print it, delete host copies after a test.

DEV-97 / DEV-74 bench scripts (Bob, 19 Oct 2026; all run with `pico.py run`):

| File | What it does |
|---|---|
| `bench_dev97_state.py` | Read-only: parked module at 0x7E on each bus, modules + UIDs, `/data` and `/` listing. |
| `bench_dev97_fixture.py` | Writes a fake `/data/wifi.json` (SSID that does not exist, no secret) + minimal `/data/product.py` so the brain takes the offline-with-product boot path. `REMOVE = True` undoes it. |
| `bench_dev97_park.py` | Parks the Buzzer in its bootloader (ERASE, nothing written) after enumerating, so the rescue knows its UID. |
| `bench_dev97_recover.py` | Reflashes a parked Buzzer from `/data/fw_buzzer.bin` or `/buzzer_firmware.bin` on the Pico. |
| `bench_dev74_settings.py` | Rewrites `/settings.toml` for one failure scenario (`MODE` = noB, nopins, typo, restore); reboot afterwards. |
| `bench_dev74_drive.py` | Drives the Buzzer (bus A) and LED Button (bus B) and checks `i2c_bus` + `by_uid()` across buses. |
| `bench_dev74_cutenum.py` | DEV-74 step 7: arms the Pico watchdog (hard reset) while `enumerate()` runs, to cut it mid-sweep. |
| `bench_dev97_cut.py` | DEV-97 step 4: parks the Display, flashes it and hard-resets the brain after `CUT_AT` bytes. |
| `bench_dev98_downgrade.py` | DEV-98: flashes an older Display app (`/display_050.bin`) and makes the daily OTA check due. |
| `bench_factory_reset.py` | Clears wifi.json, product.py and the Store copy of the credentials (brain not reachable on the LAN). |
| `bench_dev98_downgrade16.py` | DEV-98: flashes an older LEDs 16x app (`/leds16_202.bin`) through the Pico USB host, makes the OTA check due, hard-resets. Needs two runs (the bootloader is not seen on the first, DEV-45); then `tools/usb_bench/leds_ota_bl.py` finishes the flash. Since code.py 0.27 `bench_dev133_rescue_setup.py` is the simpler downgrade. Images: module repo `firmware/bin` at commit `2d1856a` is v2.2.0 (the oldest that reports type 0x06). |
| `bench_dev130_scan_then_ap.py` | DEV-130: the code.py 0.25 radio order on the real Pico 2 W, three cycles: WiFi scan with the hotspot DOWN -> `start_ap` (AP up, 192.168.4.1) -> `stop_ap` -> a failed join -> again. Writes nothing; `pico.py reset` afterwards. Expect `RESULT PASS`. |
| `bench_dev123_cycle.py` | DEV-123 failure point: `status()` in a tight loop while `cycle_rail("bus_a", 300)` runs; no call may hang. |
| `pullrun.sh` + `pr_update.py` / `pr_check.py` / `pr_recover.py` | DEV-124 real power-pull run (20 Oct 2026): on the Pi, board fed from a Pi USB port; each round starts an app update on the Pico, cuts ALL Pi USB power with `uhubctl -l 2` (the USB 3 hub is the one that switches VBUS on a Pi 4B) at a random moment, restores it, classifies the power manager (old / new / parked) and recovers it. `sh pullrun.sh ROUNDS MIN MAX`; log in `~/bob/pullrun.log`. |
| `bench_dev133_rescue_setup.py` | DEV-133 USB rescue (and the downgrade for the update test): the cache for `usb_leds_16x` becomes v2.2.0 (`/leds16_220.bin`), the Store learns serial -> type, the OTA check is made NOT due, `/debug_log` on, then 0xB0 with no pending record and a hard reset. Expect `[USBFW] ... flashed` (2.2.0), hard reset, `[USBFW] ... verified`. Needs code.py 0.27 + noknok 1.18 + noknok_usb 1.2 on the Pico. |
| `bench_dev133_due.py` | DEV-133 USB update: makes the daily OTA check due and hard-resets. With a 16x below the published version expect `[FW] ... PENDING`, hard reset, `[USBFW] ... flashed`, hard reset, `[USBFW] ... verified`, product up (about 1:45 min). |
| `bench_dev133_read.py` | Read-only: last 25 Store events, the `[usb]`/`[fw]`/`[USBFW]`/`[BOOT]`/`[RESCUE]`/`[CRASH]` lines of `/data/log.txt` (needs `/debug_log`), the Store `usb` record, `nvm[0:6]`. Wait about 30 s after a reset before running it. |
| `bench_dev134_park_x035.py` | DEV-134: parks the PicoHub X035 without an app (J2 off) using `/bl_x035_bench.py`. Its own `supervisor.reload()` drops to the REPL under `pico.py`: follow with `pico.py reset`. Expect `[RESCUE] ... picohub ... reflashed`, `[BOOT] hard reset before the product`, then no `[CRASH]` strikes. |
| `dev70_mdns_nights.sh` | DEV-70 unattended mDNS idle test (Sue, 22 Oct 2026): per phase (default `on off on`, 12 h each) it writes `settings.toml` = base + `NOKNOK_MDNS = 1/0` (the only `pico.py` contact), cuts USB power with `uhubctl -l 2`, proves the reset and the name state, then polls `hello` every 30 s and resolves `noknok-a324.local` every 10 min. 10 failed polls in a row = HANG: captures ping, ARP, name, serial (listen + one Ctrl-C) before the next phase. No watchdog. `nohup bash dev70_mdns_nights.sh BASE.toml 12 on off on > ~/dev/pico/dev70/run.log 2>&1 &`; read `grep -E 'PHASE|HANG|RESET|RESULT|FAIL' run.log`. Shares the brain with DEV-67: never both at once. |
| `bench_dev79_kick.py` | DEV-79 (Sue): does a WiFi join started with a 0.5 s timeout finish in the background? 5 rounds (radio off/on, kick, watch 20 s). `RESULT background=5/5` means noknok_rpc `LINK_FULL_EVERY` can go to 0. `pico.py run bench_dev79_kick.py 200`, then `pico.py reset`. |
| `dhcp_offers.py` | DEV-113 (Sue), bench host, root: sends one DHCPDISCOVER (random MAC, takes no lease) and lists every offer with server, address, gateway, DNS. Healthy = one offer from the router; a `192.168.4.16..` offer via `192.168.4.1` is flagged as a stray setup-hotspot DHCP server. `echo raspberry \| sudo -S python3 dhcp_offers.py wlan0 8`. |
| `bench_dev113_rogue_dhcp.py` | DEV-113 (Sue), on the Pico: hotspot up, hotspot down, join home WiFi without a reset, hold 60 s ("PROBE NOW": run `dhcp_offers.py`), hard reset. `STOP_DHCP = True` tests the code.py 0.32 fix. |
| `bench_dev79_router_off.sh` | DEV-79 acceptance (Sue), bench host, detached: waits for the router to be unplugged, cuts the brain's USB power (cold boot with no network), records the console passively, waits for the router to come back, reports how long until the brain answers `hello` and whether the `.local` name resolves. Needs a person to unplug/replug the router. |
