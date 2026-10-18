<!-- SPDX-FileCopyrightText: 2026 noknok (Christopher Houben) -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Restoring a brain Pico (DEV-84)

For a brain whose filesystem was damaged by a power cut, or whose `code.py` is gone. Background:
README *Filesystem policy* and ADR-003 — on CircuitPython + RP2 no filesystem write can be made
power-safe, so a cut during setup/OTA can, in the worst case, take the FAT. CircuitPython itself and
the ROM bootloader are never written by our code, so the Pico can always be recovered; this is how.

The files come straight from `software/` in the checkout you run it from — there is no second copy
that can go stale. (A copy kept on the bench host did: it lacked `noknok_rpc.py`, which `code.py`
needs since DEV-34.)

## Quick use

On a Linux host (the bench Pi4, or any PC with Python 3 + pyserial) with **one** brain Pico plugged in:

```bash
git clone https://github.com/buildwithnoknok/brain-Pico && cd brain-Pico
# 1. libraries must be .mpy: build them on the PC (tools/build_mpy.ps1 -> pico-deploy\mpy), copy the folder to the host
tools/restore/restore_brain.sh --mpy ~/mpy                                   # factory-fresh brain
tools/restore/restore_brain.sh --mpy ~/mpy --data ~/pico-backup-20261010-093000   # a brain that was set up
tools/restore/restore_brain.sh --dry-run ...    # print the plan, change nothing
```

It ends with `RESTORE OK` (and exit code 0) or `RESTORE FAILED`.

**What ends up on the brain** (printed before anything is erased; the script stops if it cannot do it properly):

* **`settings.toml`**: with `--data`, the *backup's* (the maker's own pins, e.g. I2C on GP20/GP21); `--settings FILE`
  overrides; with neither, the repo's `software/settings.toml` (the noknok PicoHub pins: I2C bus A GP10/GP11, bus B GP20/GP21, USB GP0/GP1). A restore never silently
  replaces a maker's pins with the default.
* **Libraries as `.mpy`, never `.py`**: `noknok.py` is ~190 KB of source and does not fit in the Pico's RAM next to a
  product (`MemoryError`, brain parks itself in safe idle). Sources of the `.mpy`, in order: `--mpy DIR`; `mpy-cross` on the
  host (`$MPY_CROSS` or on the `PATH`; its version must match the Pico's CircuitPython); the four `.mpy` inside the
  `--data` backup. If none exists the script stops *before the erase*. `--allow-source` installs `.py` anyway (small
  products only). `.mpy` and `.py` of the same library are never both installed (the `.py` would win).
* **Without `--data`** the brain comes back factory-fresh and opens the `noknok-setup` WiFi; the app then provisions it
  again — that is the designed recovery.
* **Crash strikes are cleared** (they live in nvm and survive a reformat; otherwise a brain that had parked itself after
  3 crashes stays parked).

## The pieces

| File | Runs on | What |
|---|---|---|
| `restore_brain.sh` | host | The whole procedure: backup → reformat → copy → reset → verify. Options `--data DIR`, `--yes`, `--no-backup`. |
| `backup_brain.sh [DEST]` | host | Read-only copy of everything on the Pico to `DEST` (default `~/pico-backup-<date>`). Safe on a damaged filesystem. **Keep `DEST` out of git** — it can hold `wifi.json` (the customer's WiFi password). |
| `erase_fs.py` | Pico | `storage.erase_filesystem()`. Destroys all files; the board resets itself. |
| `hard_reset.py` | Pico | A real reset, so `boot.py` runs again (a soft reload does not). |
| `clear_strikes.py` | Pico | Sets the three-strikes crash counter (nvm byte 0) back to 0. |
| `verify_brain.py` | Pico | "Does this brain work?": lists `/lib` (size, CRC), imports the modules `code.py` needs, compiles the installed product (catches `MemoryError`), enumerates the I2C modules on the *configured* pins and compares them with the modules the brain remembers (none answering = wrong pins or dead bus = FAILED), checks the strike counter is 0. Prints `VERIFY OK` / `VERIFY FAILED`. |

The Pico-side scripts run through `tools/pico.py run <file>`.

## Why the files go over a mount

After the reformat there is no `boot.py`, so the Pico shows its drive and the host mounts it. While the host
holds the filesystem the Pico cannot write to it over the REPL, so `restore_brain.sh` copies through the mount
(`udisksctl`, or `sudo mount` — it asks for the password; nothing is stored), `sync`s and unmounts, and copies
`boot.py` **last**. The drive then disappears again at the final hard reset (read-only brain,
`NOKNOK_USB_DRIVE = 0`).

## When something goes wrong

* **"cannot talk to a Pico"** — more than one Pico attached, or the board is not running CircuitPython. A Pico in
  BOOTSEL mode needs CircuitPython flashed first (drag the UF2 onto `RPI-RP2`); then run this.
* **The drive never shows up after the reformat** — unplug and replug the Pico, then rerun with `--no-backup`.
* **"drive still visible"** at the end — `settings.toml` has `NOKNOK_USB_DRIVE = 0` but `boot.py` did not run; hard
  reset again and read `boot_out.txt` (a maker brain with `NOKNOK_USB_DRIVE = 1` keeps its drive on purpose).
* **A brain that must be rebuilt without the Pi4 at all** is the job of DEV-38 / DEV-86 (a single UF2 with CircuitPython
  and the filesystem inside); this folder is the procedure until that exists.
