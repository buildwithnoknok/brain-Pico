# Changelog - brain-Pico (Pico 2 W brain software)

Newest first. One entry per version bump, written in the same commit as the bump. Format:
`## <version> - <date>` then `- DEV-xx - what changed and why. Breaking: yes/no.`
Convention: Confluence "Development Conventions" (SD space, page 120160257).

## code.py 0.31 + noknok_rpc.py 0.2 - 2026-10-22

- DEV-79 - a brain that started while the router was down stayed offline in practice until it was unplugged: the background rejoin froze the product for up to 5 s on every attempt, and the work that normally follows a join (clock, mDNS) never ran. Now most attempts only start the join (0.5 s) and the radio finishes it in the background; every third attempt is a full one as a safety net. Attempts are spaced 30 s up to 5 min, so the brain is online within 15 min of the network coming back. Once online, the app channel comes up, the clock is synced (2 s limit) and mDNS starts when enabled. Module updates still wait for the next boot that is due. An offline start no longer counts as a "link drop" in `status.link`. New host test `tools/test_wifi_rejoin.py`, bench script `tools/bench/bench_dev79_kick.py`. Breaking: no.

## noknok.py 1.19 - 2026-10-22

- DEV-108 - a landscape product (Smart Lamp Maxi) could crash at boot with "region 'bell' is off the panel" when the display missed one reply on a disturbed bus: the driver then assumed an 80x160 portrait panel. It now asks the display up to 3 times, and if there is still no answer it assumes the display's own start-up geometry, 160x80 landscape (or the orientation the product last set), logs `[display] ... no answer to GET_INFO` once, and waits 2 s before asking again. New host test `tools/test_display_fallback.py` (9 checks). Breaking: no (a product that relied on the old 80x160 fallback with a silent display now gets 160x80, which matches the real module).

## Tools: restore ends running - 2026-10-22 (no component version bumped)

- DEV-114 - after `RESTORE OK` the brain sat idle at the REPL (lamp dark, no setup hotspot) until someone replugged it, because the verify steps run through `pico.py`, which stops `code.py`. `restore_brain.sh` now ends with a hard reset (also after a failed verify) and says so, so the brain starts its product or the noknok-setup WiFi by itself. Breaking: no.

## code.py 0.30 - 2026-10-22

- DEV-141 - setup with the right WiFi password could end with "Something went wrong talking to the device" in the app although the device joined and saved: the reply to `provision` was still on its way when the setup hotspot closed 1 s later, and the restart after the join lost it. The hotspot now stays up until the app confirms the reply (`hello` with `ack`) or 5 s pass. `provision` accepts an optional app-chosen `join_id`, and `hello.setup.received` names the last accepted one, so an app can still follow a join whose reply was lost. `tools/test_setup_join.py` +16 checks. Breaking: no (additive; older apps get the 5 s wait).

## code.py 0.29 - 2026-10-20

- DEV-139 - after a successful setup the product could fail to download on every boot ("memory allocation failed, allocating 36027 bytes"): the device was set up but never started its product. The download now goes straight to a temporary file in small pieces instead of being held in memory twice, is checked for completeness against its announced size, then replaces `product.py`. The compile check before replacing stays; when memory is too tight for it, it is skipped and logged, and the product start judges the file (three-strikes crash recovery). New host test `tools/test_download_stream.py`. Breaking: no.

## code.py 0.28 - 2026-10-20

- DEV-136 - after a wrong WiFi password the setup hotspot could fail to come back: the network scan the brain did right before restarting it hung after the router rejected the password (2 of 2). The brain now scans for networks once per boot, before the hotspot first starts, and reuses that list whenever the hotspot restarts after a failed join. Host test `tools/test_setup_join.py` extended. Breaking: no (a network that appears after the brain started setup is not in the list; type its name).

## noknok.py 1.18 + noknok_usb.py 1.2 + code.py 0.27 - 2026-10-20

- DEV-133 / DEV-134 - **USB module updates and rescue now go around a hard reset of the brain.** The Pico's USB host does not see a USB module re-connect within one session (DEV-45; a fresh host always does). So the LEDs 16x update 2.2.0 -> 2.2.1 failed and left the module in its bootloader (the product then crashed 3 times and parked), and after the PicoHub's power manager (X035) was updated or rescued, which switches the USB host port off while it is in its bootloader, the USB module was missing until a hard reset. Now: a USB update only asks the module into its bootloader (`[FW] ... PENDING`) and the brain hard-resets; on the next boot the new `Conductor.usb_fw_pass()` flashes every USB module it finds in its bootloader (PID 4E42) from the on-device cache, the brain hard-resets once more, and the boot after that checks the version (`[USBFW] ... flashed` / `verified`). The same pass is the first **USB rescue**: a module left in its bootloader for any reason is flashed again (its type is remembered by USB serial in the Store, key `usb`). Limits: 2 flashes and 2 bootloader requests per module, then `gave_up` and the buzzer/LED alert. After an X035 update or rescue on a brain with USB host pins, the brain hard-resets before the product instead of the DEV-132 soft reload. Each hard reset costs one boot (~25 s), only on a day an update or rescue happens. `noknok_usb`: `discover()` notes modules seen in their bootloader (`last_bootloaders`), new `UsbModuleFlasher.request_bootloader()`. `Conductor.USB_UPDATE_VIA_RESET = False` gives the old in-session flash (bench scripts). New host test `tools/test_usb_fw_pass.py`; `tools/usb_sim.py` extended; bench scripts `tools/bench/bench_dev133_*.py`, `bench_dev134_park_x035.py`. Bench-proven 20 Oct 2026 on MOD-0011 (Smart Lamp Maxi): rescue 2.2.0, OTA update 2.2.0 -> 2.2.1 (PENDING, flashed, verified, product up in 1:45 min), X035 rescue without lost USB module. Breaking: no.

## code.py 0.26 - 2026-10-20

- DEV-132 - a large product (Smart Lamp Maxi) could crash with `MemoryError` a few seconds after it started, on the boot where the daily module update check ran (5 of 6 such boots, crash strike 1 of 3). The check's internet work leaves the memory fragmented. The brain now restarts its software once after that check, before the product, so the product always starts on clean memory; the restarted run skips the check (a marker byte in `nvm[5]`), so it cannot loop when the update server is unreachable. Costs a few seconds once a day. New host test `tools/test_post_ota_reload.py`. Breaking: no.

## code.py 0.25 - 2026-10-20

- DEV-130 - setup could freeze for good: a WiFi scan while the setup hotspot runs can block forever inside CircuitPython 10.3.1 on the Pico 2 W (seen 1 in 4 setups), and the web server stopped answering until a power cycle. The brain now never scans with the hotspot up: it scans once right before the hotspot starts (at boot and before every restart after a failed join) and keeps that list. `wifi.scan` returns it with `live: false` and `age_s` while the hotspot runs (a fresh scan only on home WiFi, `live: true`); the `provision` check for an unknown network uses the same list. A scan also stops collecting after 10 s. Bench script `tools/bench/bench_dev130_scan_then_ap.py`; host test `tools/test_setup_join.py` extended. Breaking: no (the app's "search again" shows the same list until the hotspot restarts).

## noknok.py 1.17 + code.py 0.24 - 2026-10-19

- DEV-124 - firmware update for the PicoHub's power manager (module type `0x07`, CH32X035, bootloader flash layout 3). `noknok.py`: `picohub` joins the firmware groups (`firmware_report()`, `update_all()`) and the parked-module rescue map; `module_flasher`: plain message for the X035 bootloader's new error 9 (image not built for this chip) and a 10 s erase timeout (the X035 erases 57 KB). `code.py`: the board the brain sits on is always part of the daily OTA pass (`CARRIER_FIRMWARE`), whatever the product lists; the stage-1 pass skips the X035 (`STAGE1_OWN_LINE`), whose stage-1 has its own version line, instead of raising a daily alert. Host tests: `tools/test_picohub.py` (PicoHub now in the firmware report), `tools/test_rescue_paths.py` (carrier always resolved, stage-1 pass skips it). Bench: module repo `firmware/test/bl_x035_bench.py`. Breaking: no.

## noknok.py 1.16 + code.py 0.23 - 2026-10-19

- DEV-123 - new driver `NoknokPicoHub` for the noknok PicoHub's power manager (module type `0x07`), available as `c.picohub` (`None` without one): `status()`, `source_caps()`, `set_rail()`, `cycle_rail()`, `reset_pico()`, `set_min_input_voltage()`, `clear_faults()`. Replies are CRC-checked with one retry, then `noknok.PicoHubError`; steering calls return the board's result code. The `status` op of `/rpc` carries the power status as `power`, and no longer lists modules without a driver. Modules without a driver now keep their address reserved in the saved state, so after a brain reboot a new module can no longer be given an address that is still in use. The PicoHub gets no firmware update from the brain yet. New host test `tools/test_picohub.py`; `tools/test_registry_complete.py` knows types listed in the registry that are deliberately not updated yet. Breaking: no.

## code.py 0.22 - 2026-10-18

- DEV-100 - setup now tells the app when the WiFi join fails. New op `wifi.scan` lists the networks the device can see. `provision` (and the `/connect` form) refuse a network that is not visible (`reason: network_not_found`, hotspot kept; `force` for a hidden network) and answer with a `join_id`. A failed join is remembered in RAM and shown in `hello` as `setup.last_join` (`auth` / `not_found` / `timeout` / `other` / `not_saved`) once the hotspot is back; a wrong password stops retrying at once; the device plays its alert and blinks its own LED. The browser page says "Connecting...", not "Connected!". Nothing new is written to the filesystem. New host test `tools/test_setup_join.py`. Breaking: no (app 1.5.3 and older keep working, without the feedback).

## noknok.py 1.15 + code.py 0.21 - 2026-10-18

- DEV-74 - **two I2C buses and no hard-coded pins.** The brain takes I2C bus A (`NOKNOK_I2C_SDA` / `_SCL`), the new optional bus B (`NOKNOK_I2C_B_SDA` / `_B_SCL`) and the USB host pins (`NOKNOK_USB_DP` / `_DM`) only from `settings.toml`; there is no default in the code any more (it used to fall back to GP8/GP9 and GP16/GP17). A missing or misspelled pin group is off and reported: console, a `[CFG]` event (only when the problem changes), the Pico's own LED blinking at boot, and a new `config` block in the `hello` op. A complete set is kept in the Store; if `settings.toml` loses all its pins the brain uses that last-known-good copy and says so. Both buses are enumerated in one ~3 s sweep into one registry with one address space (a module moved between buses keeps its address); modules know their bus (`i2c_bus`), the saved state and `firmware_report()` record it, the parked-module rescue probes both buses, `Conductor.deinit()` releases both, and `status` lists each module's bus. The repo `settings.toml` now carries the noknok PicoHub pins (bus A GP10/GP11, bus B GP20/GP21, USB GP0/GP1). Bench scripts read the pins from `settings.toml` too (`noknok.i2c_bus_pins()`). New host test `tools/test_two_bus.py`. **Breaking:** a brain whose `settings.toml` has no pin keys and that never booted with them (no last-known-good copy) reaches no module until the file is installed; a brain that relied on the GP8/GP9 default needs `NOKNOK_I2C_SDA = "GP8"` / `NOKNOK_I2C_SCL = "GP9"` in its file.

## Tools: registry check - 2026-10-18 (no component version bumped)

- DEV-98 - `tools/test_registry_complete.py` (host, tier S): fails when a module type the brain knows (I2C and USB type tables in `noknok.py` / `noknok_usb.py`) has no entry in the Ecosystem module registry `modules.json`, or the other way round; `--online` also fetches every index and image anonymously and checks size + crc32. Nothing ships to a brain. Breaking: no.

## code.py 0.20 - 2026-10-18

- DEV-97 - no boot that starts a product skips the parked-module rescue any more. A connected boot whose daily update check was due used to skip it completely when the product has no `module_firmware`, the HTTPS session failed or nothing resolved (GitHub unreachable); those boots now rescue from the on-device firmware cache like offline boots do. A module that stays parked is reported loudly: a `MODULE STILL PARKED` log line, the `[RESCUE]` event in the history the app reads, and the buzzer/LED alert; every later boot retries. New host test `tools/test_rescue_paths.py`. Breaking: no.

## code.py 0.19 - 2026-10-18

- DEV-99 - removed the setup-time `POST /firmware/check` route and the `firmware.check` op. On the setup hotspot the brain has no internet and manifests carry only minimum versions, so it could only ever answer "no update"; the app banner it fed never appeared. Module updates happen silently in the post-WiFi OTA pass (unchanged). Breaking: no (an older app calling the route gets 404 and shows nothing, as before).

## noknok.py 1.14 - 2026-10-18

- DEV-101 - the runtime Store looks for an I2C FRAM at 0x50 only when `settings.toml` says `NOKNOK_FRAM = 1`. Before, it probed unless the setting was `0`, so an unrelated EEPROM/FRAM a maker wired to 0x50 could silently become the Store; without the setting the Store now never touches the I2C bus. New host test `tools/test_store_fram_optin.py`. Breaking: only for a brain that relied on an FRAM without setting `NOKNOK_FRAM = 1` (no noknok board fits one); add the setting to keep using it.

## Tools: bench - 2026-10-10 (no component version bumped)

- `tools/bench/`: test helpers from the Sprint 1 QA run - boot capture from replug, listen-only console reader, hands-free power-pull loop for `bench_yank.py` (incl. runtime-only mode), `/rpc` failure-mode script, offline `wifi.json` helper, read-only bench inventory, Display rotation check, deliberately crashing product. Bench only, nothing ships to a brain. Breaking: no.

## Tools: restore fix - 2026-10-17 (no component version bumped)

- DEV-84 / DEV-111 - `restore_brain.sh` no longer leaves a non-working brain (found by Bob on a real restore: `RESTORE OK` but no I2C modules and `MemoryError`). With `--data` it restores the backup's `settings.toml` (the maker's pins) instead of the repo default (`--settings FILE` overrides); the libraries are installed as `.mpy` (`--mpy DIR`, `mpy-cross` on the host, or the backup's own), never as `.py` sources, and the script stops BEFORE the erase when no `.mpy` exists (`--allow-source` to override); the crash-strike counter in nvm is cleared (`clear_strikes.py`); `verify_brain.py` now also compiles the installed product, enumerates the I2C modules on the configured pins against the modules the brain remembers, and checks the strike counter. New `--dry-run`. Breaking: no (a restore without a source of `.mpy` now refuses instead of installing sources).

## Docs: stale public statements - 2026-10-10 (no component version bumped)

- DEV-87 - README, `tools/soak/README.md`, `soak_run.py` header, `code.py` and `noknok.py` comments/docstrings no longer say what is not true: per-port `uhubctl` does not cut VBUS (ganged does, verify per host); `noknok_events.txt` / `noknok_state.json` are gone (the Store holds the event history and module state); no FRAM on any noknok board; Display firmware 0.7.0 is current and `bench_dev41.py` has 8 steps; the display examples now fit the 80 px panel (size 16, 5 characters; size 32 = 2). Removed the dead `software/flip_v_once.py` (rev 1.0 rework-board probe). Comments and docs only; no code behaviour changed. Breaking: no.

## Tools: restore - 2026-10-10 (no component version bumped)

- DEV-84 - `tools/restore/`: `restore_brain.sh` (backup, reformat, restore from this checkout, hard reset, verify; `--data` puts a backup's setup data back), `backup_brain.sh`, and the Pico-side `erase_fs.py`, `hard_reset.py`, `verify_brain.py`. Replaces the one-off scripts that lived only on the bench Pi4 (and had gone stale: no `noknok_rpc.py`); no password in any file (`PICO_SUDO_PASS` env var or a terminal prompt), no WiFi credentials in git. Bench-proven on a deliberately wiped Pico (70 s, `RESTORE OK`). Also `.gitattributes`: `*.sh` always LF. Breaking: no.

## noknok.py 1.13 - 2026-10-10

- DEV-65 - follow-up found by the first run on 8 real modules. `bootloader_version()` always sends BOOT after entering the bootloader, even when the read fails (a failed read used to leave the module parked at 0x7E, invisible to `enumerate()`); a "layout read, stage-1 too old to say" answer is remembered per UID instead of being re-read (a bootloader round trip plus re-enumeration) on every `firmware_report()` / `update_all()`. New `software/bench_unpark.py` releases modules parked in their bootloader. `tools/bench_update_modules.py` also walks the standard GP8/GP9 bus. Breaking: no.

## noknok.py 1.12 - 2026-10-08

- DEV-65 - flash-layout gate inside the library. `Conductor.update_all()` now refuses (fail closed, nothing written) any I2C module whose bootloader layout is not exactly the manifest entry's `layout`; `firmware_report()` gains `layout_image`, `layout_installed`, `blocked` and optional `read_layout` / `strict_layout`. Before, only the provisioning path in `code.py` was gated; `tools/bench_update_modules.py` and any caller of `update_all()` could hang a layout-1 module with a layout-2 image. `tools/bench_update_modules.py` manifest now carries layouts and current versions; `update_demo.py` documents that it needs them. code.py unchanged (0.18). Breaking: `update_all()` callers whose manifest has no `layout` now get their I2C modules refused (pass `layout` or `check_layout=False`).

## Baseline - 2026-10-02

- Changelog starts here. Current: noknok.py 1.11 (__version__), code.py 0.18 (CODE_VERSION). Track the two versions separately in each entry. Earlier history: git log and the README.

## noknok.py 1.11 - 2026-10-02 (backfilled for DEV-46)

- DEV-46 - read-only `info` settings: `c.settings.info(id, fn)` shows a live value (e.g. the LEDs 16x temperature) on the app's settings page. `settings.get` returns `info{id: value}`, computed on every call and never stored (no `seq` bump, no dirty flag, no flash write); `settings.set` rejects an info id as `read-only`; a provider that raises gives `null` (logged once). `docs/provisioning-http-api.md` updated. Tests: `software/bench_info_settings.py` (real 16x, PASS), `software/bench_lamp16.py` (unmodified product on the Pico, PASS). Breaking: no.

## noknok.py 1.10 / noknok_usb.py 1.1 - 2026-09-29 (backfilled for DEV-46)

- DEV-46 - USB modules are identified by the `0xF0` type byte, not the PID (all noknok USB apps share PID `0x4E4E`): `0x04` = LEDs (8x, `NoknokLEDs`), `0x06` = LEDs 16x (`NoknokLEDs16`); an unknown type is logged once and skipped, no answer is retried next pass. New `NoknokLEDs16`: 16 LEDs, RGBW (every colour call takes `w=`, plus `white(level)`), `status()` from GET_STATUS 0x30 (`temp_c`, `vbus_mv`, `cc1_mv`/`cc2_mv`, `budget_ma`, `led_ma`, thermal/VBUS flags, `limited`), `temperature()`. Conductor: `c.leds16`, manifest type `usb_leds_16x`, included in `firmware_report()` and role assignment. `c.leds` (8x) unchanged. Tests: `tools/usb_sim.py` 39/39, `tools/usb_hw_leds16.py` (real 16x on a fast host, PASS), `software/bench_dev46.py` (8x + 16x + Display behind the Pico, PASS). Breaking: no.
