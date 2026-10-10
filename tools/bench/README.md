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
| `bench_inventory.py` | Pico (`pico.py run`) | Read-only: library versions, `settings.toml` pins, WiFi SSID, modules on GP8/9, GP20/21, GP18/19 and the USB host. |
| `bench_display_rotation.py` | Pico | Visual check of all four Display orientations (frame, corner markers, asymmetric text). |
| `crash_product.py` | Pico, as `/data/product.py` | Crashes on purpose: exercises 3-strike recovery and safe idle. |

Rules that cost time to learn:
- Only **one** program may read the Pico's serial port at a time (pico.py, a listener, Thonny).
- Find the port by id (`/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00`): a WCH-LinkE also has a serial port.
- After a power-on a brain needs ~25 s before the product runs; leave the console alone until then.
- Keep logs and backups in `~/` on the Pi, not `/tmp` (wiped on reboot).
- `wifi.json` holds a real WiFi password: never print it, delete host copies after a test.
