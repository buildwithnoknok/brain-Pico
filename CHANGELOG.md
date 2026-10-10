# Changelog - brain-Pico (Pico 2 W brain software)

Newest first. One entry per version bump, written in the same commit as the bump. Format:
`## <version> - <date>` then `- DEV-xx - what changed and why. Breaking: yes/no.`
Convention: Confluence "Development Conventions" (SD space, page 120160257).

## Tools: bench - 2026-10-10 (no component version bumped)

- `tools/bench/`: test helpers from the Sprint 1 QA run - boot capture from replug, listen-only console reader, hands-free power-pull loop for `bench_yank.py` (incl. runtime-only mode), `/rpc` failure-mode script, offline `wifi.json` helper, read-only bench inventory, Display rotation check, deliberately crashing product. Bench only, nothing ships to a brain. Breaking: no.

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
