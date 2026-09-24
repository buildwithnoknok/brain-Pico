<!-- SPDX-FileCopyrightText: 2026 noknok (Christopher Houben) -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Architecture soak — unattended long-run test rig

Runs the whole noknok stack overnight with nobody watching: cold-boot it a few
hundred times, enumerate **both I²C buses plus the USB side**, hammer every
module with real traffic, and record whether the architecture still holds.

Read the result the next morning with one command. The nightly log is a few
hundred JSON records; `soak_report.py` compresses them to about 40 lines.

## What it tests

| Scenario | Weight | What it looks for |
|---|---|---|
| `cold_boot` (5 s off) | 5 in 10 | Modules re-enumerate at the same address with the same firmware after a real power failure |
| `short_off` (0.3 s off) | 2 in 10 | Fast recycle — reset lines, brownout, half-discharged rails |
| `long_off` (30 s off) | 1 in 10 | Fully discharged caps, true cold silicon |
| `storm` (5 rapid cycles) | 1 in 10 | Power instability — partial boots, crash counters, bus lock-up |
| `idle_hold` (3 min traffic) | every 25th | Slow leaks, drifting enumerate time, bus hangs under sustained load |

Every iteration also checks: the golden module set (UID / address / firmware per
bus), per-operation I²C error counts, the DEV-32 crash counter in `nvm[0]`,
reset reason, and free memory.

**Not covered yet:** OTA flashing and interrupted-flash (brick) tests. Those
need a sacrificial module — CH32V003 flash is rated ~10 k erase cycles and a
soak would burn through that. Do not point these at production modules.

## Running it

```sh
# once, on the Pi (NOT in this repo — it is public):
printf 'yourpassword' > ~/.soak_sudo && chmod 600 ~/.soak_sudo

cd ~/dev/pico/soak
./start_soak.sh 12            # 12 hours, detached, survives the SSH session
```

Then in the morning:

```sh
python3 soak_report.py        # newest log in ./runs
```

Stop early with `touch runs/STOP` — it finishes the current iteration and exits
cleanly.

## Files

| File | Runs on | Job |
|---|---|---|
| `soak_probe.py` | the Pico | One iteration's measurement. Prints a single `@@NK@@{json}` line. |
| `soak_run.py` | the Pi | Orchestrator: power-cycle → wait → probe → compare → log. Never exits on error. |
| `soak_report.py` | the Pi | Compresses a night into a readable verdict. |
| `start_soak.sh` | the Pi | `nohup` wrapper. |

## Design notes

- **Power** is cut with `uhubctl` on the Pi's own USB hub port (auto-detected by
  looking for the Pico in `uhubctl` output). The Pico's VBUS pass-through feeds
  the PicoHub, so cutting that one port cold-boots the Pico, the PicoHub and
  every module — a real power failure, not a soft reset.
- **The probe never writes the Pico's filesystem** (DEV-18: no runtime FS writes).
- **The probe never makes noise.** The buzzer is only ever read
  (`is_playing`/`stop`), LEDs are driven dim and briefly, and the display
  backlight is held at 15% — a soak can run next to someone asleep.
- **It must never die.** Every failure is caught, logged and the loop continues.
  A soak that exits at 03:00 tells you nothing.
- **The baseline persists.** `runs/baseline.json` is reused across nights so
  several runs measure against the same golden set. Change the hardware →
  delete it (or use a fresh `--dir`) and the next run recaptures it. Keep the
  `--usb` flag consistent with the baseline you compare against.
- **The sudo password is never in this repo** (it is public). It comes from
  `$SOAK_SUDO_PW` or `~/.soak_sudo`.

## Verified on the bench, 24 Sep 2026

Rig: Pi4RFID → Pico 2 W (hub `1-1` port 4) → PicoHub; display on I²C0 (GP20/21),
LED Button + buzzer + knob on I²C1 (GP18/19), USB LEDs on DUSB5.

- Probe: 8.4 s, 91 I²C ops, 0 errors
- Idle-hold: 8 loops, 728 ops, 0 errors in 45 s
- Orchestrator: 9/9 then 5/5 iterations pass across all power scenarios
- Fault detection proven with a seeded phantom module → `i2c1 MISSING …`
