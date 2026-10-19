# tools/bench - test helpers for the brain bench

Small tools used by the QA runs (Bob) and anyone testing a brain on a bench host (a Pi with the
brain Pico on USB). They complement `tools/pico.py`, `tools/rpc_call.py` and the `software/bench_*.py`
scripts. Nothing here ships to a customer brain. MIT, like the rest of this repo.

| File | Runs on | What it does |
|---|---|---|
| `boot_capture.py [SECONDS]` | host | Waits for an unplug + replug, then records the boot from the first second. Sends nothing. |
| `serial_listen.py [SECONDS] [--reboot\|--ctrlc]` | host | Listen-only console reader (reopens after resets). `--reboot` soft-reboots first; `--ctrlc` tells "busy" from "console dead". |
| `yank_loop.sh [RUNS] [--runtime-only]` | host | Hands-free power-pull rounds with `software/bench_yank.py`: a person pulls during each tone, the loop records each boot and runs the next round. `--runtime-only` for brains with real setup data. |
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
| `bench_dev98_downgrade16.py` | DEV-98: flashes an older LEDs 16x app (`/leds16_202.bin`) through the Pico USB host, makes the OTA check due, hard-resets. Needs two runs (the bootloader is not seen on the first, DEV-45/DEV-133); then `tools/usb_bench/leds_ota_bl.py` finishes the flash. Images: module repo `firmware/bin` at commit `2d1856a` is v2.2.0 (the oldest that reports type 0x06). |
