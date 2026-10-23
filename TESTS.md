<!-- SPDX-FileCopyrightText: 2026 noknok (Christopher Houben) -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Tests for the brain (Pico 2 W)

What to run, where, and what "pass" looks like. A cold reader should be able to run everything below
without asking the person who wrote it. Tiers: **S** = seconds, no interaction; **M** = a few minutes,
modules on the bus; **L** = a person at the bench (about 15 minutes).

Background for the power-loss tests: README *Filesystem policy* and ADR-003 (Confluence 114458625).
In short: on CircuitPython/RP2 no filesystem write can be made power-safe, so a running product never
writes files (settings and state live in the nvm *Store*), and setup/OTA writes go into `/data`.
These tests are what keeps that true. Any future `open(..., "w")` at runtime, or a write creeping back into
boot or enumeration, silently re-opens the failure that cost a whole brain on 15 Sep 2026.

## Host tests (no hardware)

Run from the repo root on any PC with Python 3. Each prints `PASS`/`ALL PASS` or a failure.

```bash
for t in tools/test_*.py; do python $t || echo "FAILED: $t"; done
```

Two exceptions: `tools/test_maxi_tz.py` needs a Linux host (it uses `time.tzset`) and `TZ=UTC`;
`tools/test_display_fallback.py` imports `display_sim` from `tools/`, so run it as `python tools/test_display_fallback.py`
(not with `python -I`). Run the host tests after any change to `software/`. A change that touches the Store,
`boot.py` or the boot path needs the bench tests below as well.

## Bench tests on the brain (power-loss policy, DEV-18 / DEV-76)

### Set-up (once per session)

* A bench host (the Pi4s, see `~/.claude/tools.md`) with **one** brain Pico on USB. Check the serial id first:
  `ls -l /dev/serial/by-id/` (the brain is `usb-Raspberry_Pi_Pico_2_W_<serial>-if00`; the WCH-LinkE also shows a serial port).
* A clone of this repo on that host (`git pull --rebase` first). Examples below use `python3 tools/pico.py run <file> [seconds]`
  from the repo root; `pico.py` copies nothing, it runs the file on the Pico.
* The brain has the current `code.py`, `boot.py`, `settings.toml` and the `.mpy` libraries, and was **hard-reset or
  power-cycled after the last install**: `boot.py` runs only on a hard reset, not after a soft reload. If unsure:
  `python3 tools/pico.py run tools/restore/hard_reset.py`, then wait about 30 s.
* Only one program may read the serial port at a time. After a power-on leave the console alone for about 30 s
  (touching it earlier can leave it dead, DEV-107).
* Check the bench is free: a bench brain may be reserved (for example `~/BENCH_IN_USE_*.txt` on the Pi).
* A brain with real setup data (`/data/wifi.json`, `product.py`): take a backup first
  (`tools/restore/backup_brain.sh <dir>`; it may contain the WiFi password, never commit it).

### 1. Filesystem policy: `software/bench_fs_policy.py` (tier S)

```bash
python3 tools/pico.py run software/bench_fs_policy.py
```

Proves: the filesystem is read-only to the program outside a write window; `noknok.writable()` opens a window and
nested windows share one remount; `write_atomic()` leaves no `.tmp`; `clean_tmp()` removes an orphan; plus what
`boot.py` decided (`boot_out.txt`) and what `settings.toml` says. Leaves no files behind.

**Pass:** every line `[PASS]` and `SUMMARY: N/N passed`. **Fail:** any `[FAIL]`. The most common cause is a brain
that was only soft-reloaded since `boot.py` changed (do a hard reset and repeat), or a missing/broken `boot.py`
(the drive shows up on the host: fail-open, see the 2 Oct 2026 `.mpy` incident).

### 2. Store and stable addresses: `software/bench_store.py` (tier S/M, needs the modules on the bus)

```bash
python3 tools/pico.py run software/bench_store.py
```

Proves: the Store backend is detected (`nvm` on shipping hardware), set/get/append/delete round-trip, the record
survives a fresh Store instance, two enumerations in a row give every module the same address and the second one
writes nothing, and no `*.tmp` or runtime files appear in `/` or `/data`.

**Pass:** `SUMMARY: N/N passed - ALL GOOD`. With no modules on the bus the enumeration checks cannot pass: connect the
brain's usual modules first (a bare Pico fails the address checks, which is a rig problem, not a bug).

### 3. The real power pull: `software/bench_yank.py` (+ `bench_yank_cleanup.py`), tier L, manual

The board tells you when to pull. Each run **verifies the previous round**, then **starts the next one**: three short beeps
(LED Button amber), then a continuous tone (LED red) for about 8 s while the Pico writes back to back. **Pull the USB cable
during the tone.** Replug, wait about 30 s, run again.

```bash
python3 tools/pico.py run software/bench_yank.py 80     # repeat once per pull
python3 tools/pico.py run software/bench_yank_cleanup.py   # at the end: prints the tally, removes the yank_* files
```

Hands-free driver (a person still pulls the cable during each tone; the script records every boot from the first second):

```bash
tools/bench/yank_loop.sh --runtime-only 3     # 3 runs = 2 pulls + 1 no-pull control; default is 9 runs
# watch:  tail -f ~/yank_run.log | grep -E 'ROUND|TONE|tally|lost'
```

Rounds cycle `idle, store, idle, store` (RUNTIME) and then `small, product, firmware` (SETUP). Missed the tone? Run again:
that round is a no-pull control.

| Round type | What is being written during the tone | Required result |
|---|---|---|
| `idle`, `store` (runtime) | nothing (product loop) / the Store back to back | **0 files lost, no new garbage directory entries.** A `store` round on the nvm backend may find the record empty: that is by design (ADR-003 amendment 22 Oct 2026, DEV-75) and counts as PASS. |
| `small`, `product`, `firmware` (setup) | `/data` files through `write_atomic()` | **Not a gate.** Known unsafe on this platform; the expected worst case is `/data` lost with the system files intact. They exist to reproduce the finding and measure containment. |

**Pass criterion for a story:** all RUNTIME rounds `PASS`, `lost_files` 0 in the tally printed by
`bench_yank_cleanup.py`. **Minimum for a story that touches `noknok.py` storage, `boot.py`, the `code.py` boot path or
`c.settings`: 2 runtime pulls (one `idle`, one `store`).** On a brain with real setup data use `--runtime-only`:
the setup rounds can wipe `/data`.

Gotchas learned on 15 Sep 2026:

* `bench_yank.py` disables auto-reload itself (`supervisor.runtime.autoreload = False`): its own writes would otherwise
  soft-reboot the board mid-test.
* **Garbage directory entries** left by an earlier pull cannot be deleted (`stat` fails, `remove` fails). Judge each round
  as a before/after delta: only *new* garbage counts. They go away when the directory is recreated (restore).
* If CircuitPython does not even reach the REPL after a pull, or the state file itself is gone, that is a **FAIL** of the
  filesystem, not a script problem: rescue what you can with `tools/pico.py get/gettree` over the REPL, then restore.
* A killed filesystem is restored with `tools/restore/restore_brain.sh` (it uses `erase_fs.py` and mounts the fresh drive;
  see `tools/restore/README.md`). The old DEV-18 helper scripts `erase_fs.py` and `restore_mount.sh` that lived only on the bench
  host are superseded by that folder (DEV-84), so nothing is left to copy.
* To cut the power of a **board under test** automatically (instead of a person pulling): feed it from a Pi4 USB-A port and use
  `uhubctl -l 2` (hub 2, the USB 3 hub, not `1-1`), see `~/.claude/tools.md` and `tools/soak/powertest.sh`; prove the cut with
  the Pico's `time.monotonic()` jumping backwards, never with `reset_reason`.
* A power pull is a hard reset: expect the brain to need about 25-30 s before the product runs. Do not judge "dead" earlier.

### When to run which

| Change touches | Run |
|---|---|
| `software/` Python (any) | host tests (S) |
| `boot.py`, `settings.toml` handling, `noknok.writable()/write_atomic()` | host tests + 1 + 3 (2 runtime pulls) |
| `noknok.py` Store, enumeration, stable addresses | host tests + 2 + 3 (2 runtime pulls) |
| `code.py` boot path, `c.settings` / `noknok_rpc.py` settings | host tests + 1 + 3 (2 runtime pulls) |
| release candidate / version gate | all of the above; the L run is done by Christopher in the evening, never `regress_all.sh` unasked |

## Other bench scripts

`tools/bench/README.md` lists the failure-mode helpers (`rpc_failure_modes.sh`, `wifi_offline.py`, `crash_product.py`, the DEV-74/97/98
scripts) and the rules that cost time to learn. `tools/restore/README.md` covers backup and restore of a brain.
