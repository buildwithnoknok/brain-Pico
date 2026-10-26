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

Since DEV-67 (25 Oct 2026) it also reads the MOD-0011 PicoHub power manager
(GET_STATUS only: faults, reset cause, input voltage), enumerates LEDs and LEDs 16x
on USB, records the Pico's uptime at every probe and FAILS an iteration whose cut did
not restart the Pico's clock (uptime > 120 s). Bus names in the log are `A`/`B`
(settings.toml), not `i2c0`/`i2c1`.

**Captures (DEV-165/166).** When a module from the baseline is missing, or answers with a
version other than its baseline one, the probe records what the bus looks like: raw scan,
what waits at 0x7F (hand-out) and 0x7E (bootloader), the raw bytes at the module's address,
then enumerates again after 20 s (slow boot = it comes back, stuck = it does not).
`soak_report.py` prints them under "captures". Dry run without hardware:
`python -I tools/test_soak_probe.py`.

**Not covered yet:** OTA flashing and interrupted-flash (brick) tests. Those
need a sacrificial module — CH32V003 flash is rated ~10 k erase cycles and a
soak would burn through that. Do not point these at production modules.

## Running it

```sh
# once, on the Pi (NOT in this repo — it is public):
printf 'yourpassword' > ~/.soak_sudo && chmod 600 ~/.soak_sudo

cd ~/dev/pico/soak
./start_soak.sh 12 --usb --hub 2   # 12 hours, detached; extra args go to soak_run.py
                              # --hub 2 = the Pi 4B's USB 3 hub, the one that cuts VBUS
                              # --pattern cold,short,cold,long = your own scenario mix (keys: cold short long storm hold)
                              # --serial <id> when more than one Pico is plugged in (also PICO_SERIAL=<id> for pico.py/powertest.sh)
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

- **Power** is switched with `uhubctl` on the Pi's own USB hub (found by looking for the
  Pico in `uhubctl` output), **ganged**: `uhubctl -l <hub> -a off|on`, all ports at once.
  **Per-port switching (`-p <port>`) does not cut power on a Raspberry Pi 4B.** It reports
  "off", but VBUS stays up and the Pico only loses its data link, which looks like a power
  cycle and is not (measured 28 Sep 2026: `time.monotonic()` kept counting). The ganged switch
  did restart the Pico's uptime clock on that rig (925.4 s → 9.1 s): a real cut, confirmed
  on the bench (it can only cut all four ports together, never one by one).
  **Verify on every new host before trusting a result**: `time.monotonic()` must restart
  near 0 across the "cut" (`reset_reason` is stale and proves nothing). Runs made with the
  per-port switch tested I2C traffic only. A purpose-built cut (relay or MOSFET in VBUS,
  smart plug) is DEV-67; ask for a hand-pulled cable for any test where the cut itself matters.
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

## First full night, 25-26 Oct 2026 (DEV-67)

Pi4RFID, Pico on a MOD-0011 fed from a Pi USB-A port (so the ganged cut removes the
modules' power too), bus A: Display, Knob (legacy bootloader), LED Button, PicoHub
power manager; bus B and USB empty. 14 h, 952 iterations, `uhubctl -l 2` (cut proven:
Pico uptime 9.6 s at every probe), 945 PASS / 7 FAIL, 265909 I2C operations with 0
errors, free memory flat (253632 B), crash counter 0xC0 throughout.
The 7 failures were all "a module not answering as its app right after a power-on":
the PicoHub power manager absent (4x, DEV-165) and Knob absent + LED Button reading
2.0.0 (3x, DEV-166). Both are open bugs; the probe does not yet capture what the bus
looks like when a module is missing.

## Capture rerun, 26 Oct 2026 (DEV-165 / DEV-166)

Same rig and wiring as the first night, in daytime, probe with the capture on, mix
`cold,short,cold,short,cold,short,cold,long`, gap 3 s: 10:41-14:42, 384 iterations
(184 cold_boot, 138 short_off, 47 long_off, 15 idle_hold), 384 PASS, 0 captures,
105924 I2C operations with 0 errors. A clean run does not prove the two bugs fixed:
at the first night's rate (7 in 952) 0 failures in 384 iterations has a chance of
about 6 %. The probe stays armed: the next failure on any rig records its capture.
