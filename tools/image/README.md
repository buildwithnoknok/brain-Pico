# tools/image — the one-file brain image (DEV-38)

`noknok-brain-<code.py version>-cp<CircuitPython version>.uf2` brings a Pico 2 W back to a
complete noknok brain with **one gesture**: hold BOOTSEL, plug it into any PC, drag the file
onto the `RP2350` drive. It holds CircuitPython **and** the CIRCUITPY filesystem (`boot.py`,
`code.py`, `settings.toml`, `noknok.mpy`, `noknok_rpc.mpy`, `noknok_usb.mpy`,
`module_flasher.mpy`, `lib/`). The same file is the factory-flash artefact.

It replaces the multi-step bench recovery (flash_nuke → CircuitPython → copy files) for
consumers and production. `tools/restore/restore_brain.sh` stays the bench tool (backups,
setup data, a maker's own `settings.toml`).

## Build

```
python -I tools/image/build_brain_uf2.py \
    --cp-uf2 adafruit-circuitpython-raspberry_pi_pico2_w-en_US-10.3.1.uf2 \
    --mpy-cross mpy-cross-10.3.1.exe          # Linux: the Linux mpy-cross 10.3.1
```

Output in `build/` (git-ignored): the `.uf2`, a `.json` manifest (every input and output
checksum, every file with its sha256, the flash layout) and a `.uf2.sha256`.

- **Pinned inputs.** CircuitPython 10.3.1 for `raspberry_pi_pico2_w` (sha256 pinned in the
  script; md5 = Adafruit's S3 ETag) and mpy-cross 10.3.1 (version checked). A CircuitPython
  upgrade = new pin + matching mpy-cross + `software/lib/` from the matching bundle, in one commit.
- **Reproducible.** Same brain-Pico commit + same inputs = byte-identical UF2: FAT timestamps
  are the commit time, files are sorted, the `.mpy` carry no host path (`-s noknok.py`; the
  brain's tracebacks then say `noknok.py`, not `C:/Users/.../noknok.py`). The script refuses
  a build with uncommitted changes in `software/` (`--allow-dirty` for a test build only).
- **Standard library only**, no mkfs/mtools: the FAT is built by the script.
- `--settings FILE` ships another `settings.toml` (a maker's pins); default = the repo's
  (PicoHub MOD-0011 pins, `NOKNOK_USB_DRIVE = 0`).
- `--wipe-store` also erases the nvm sector (see below) -> `...-wipe.uf2`.

## What is where (CircuitPython 10.3.1, Pico 2 W)

| Flash (XIP) | What | Written by the image |
|---|---|---|
| `0x10000000` – `0x10172200` | CircuitPython (the pinned UF2's own blocks, unchanged) | yes |
| `0x10180000` – `0x10181000` | nvm, 4 KB = the noknok **Store** (settings, state, WiFi copy, strike counter) | **no** (kept), unless `--wipe-store` |
| `0x10181000` – `0x10400000` | CIRCUITPY: FAT16, 1 FAT, 512-byte clusters, label `CIRCUITPY`, no partition table | used part only (~310 KB); the free clusters after it are left as they are (the FAT marks them free) |

Sources: `ports/raspberrypi/mpconfigport.h` (`CIRCUITPY_INTERNAL_NVM_SIZE`, drive start =
firmware + nvm) and `boards/raspberry_pi_pico2_w/mpconfigboard.mk` (`CIRCUITPY_FIRMWARE_SIZE`
= 1536 KB) at tag 10.3.1. Another board or CircuitPython version = re-check these numbers.

**Store kept (default) = consumer recovery.** The brain's WiFi copy survives in the Store:
`code.py` rebuilds `/data/wifi.json` from it, joins the home WiFi, downloads its product
again and runs - no app needed. A brain with nothing in the Store opens the `noknok-setup`
hotspot. **`-wipe` = factory state** (fresh chips have an empty nvm anyway; use it for a unit
going to another person, or when the Store itself is suspect).

## Checks

- Host: `python -I tools/test_brain_uf2.py` (needs the pinned UF2 + mpy-cross, else SKIPPED):
  two builds identical; UF2 blocks valid, numbered 0..N-1, one family; CircuitPython blocks
  unchanged; nvm untouched (or 0xFF with `--wipe-store`); the filesystem read back from the
  UF2 with a separate FAT reader, every file byte-identical to the repo, exactly the restore set.
  It writes the filesystem image to `$TEMP/noknok-brain-fs.img`.
- Independent (Linux, the image file only): `fsck.fat -n -v noknok-brain-fs.img` and
  `mount -o loop,ro`, then compare every file's sha256 with the manifest.

## Test plan on hardware (Bob, tier M)

**Needs:** a **spare Pico 2 W** (NOT a bench brain: the image replaces its filesystem, so its
setup and product are gone) on a PicoHub or bare, a PC or Pi with a USB port, the
`noknok-brain-0.35-cp10.3.1.uf2` built from the commit under test (and the `-wipe` one), its
manifest. BOOTSEL = hold the white button while plugging in; the `RP2350` drive appears.
Supply >= 5.0 V (DEV-107).

| # | Do | Expect |
|---|---|---|
| 1 | Erased Pico: drag `flash_nuke.uf2`, then **only** `noknok-brain-...-wipe.uf2` | drive disappears, Pico reboots by itself; console: `noknok boot: CIRCUITPY drive hidden`, `power-on — reloading once`, then `No credentials — starting AP provisioning`; WiFi `noknok-setup` visible. Note the time from drag to hotspot. |
| 2 | Set up from the app (any product) | provisioning completes, the product runs - as on a hand-restored brain |
| 3 | `python3 tools/pico.py run tools/restore/verify_brain.py` | PASS (the DEV-84 acceptance check) |
| 4 | File set = factory unit: list the CIRCUITPY files over the REPL (or `pico.py ls`) | exactly the manifest's `files` + CircuitPython's own `boot_out.txt` (and `/data` after setup); no `.py` next to the four `.mpy` |
| 5 | Break it: remove `code.py` (or run `tools/restore/erase_fs.py`), replug | brain "empty" (drive visible, fail-open) |
| 6 | BOOTSEL + drag the **default** (`...-cp10.3.1.uf2`, Store kept) | restored in one step; because the Store kept the WiFi copy: `wifi.json missing — restored from the Store copy`, joins, downloads the product, runs it, without the app |
| 7 | Repeat 5 + drag the `-wipe` file | restored to factory state: `noknok-setup` hotspot again |
| 8 | Drag the image onto a brain that runs an OLDER release | boots the new code; setup data in `/data` is gone (expected: whole filesystem replaced), Store kept |

Known edge: RP2350-E10 - a Pico that once ran a firmware with a picotool **partition table**
may ignore a drag-and-drop UF2 (CircuitPython's own UF2 too). Drag `flash_nuke.uf2` first.

## Not done yet (rest of DEV-38, after a PASS)

- Publish per release (GitHub Releases or `releases/`) with the `.sha256`.
- Consumer instructions (website support page, Max) and the internal factory-flash procedure
  (Confluence), and the optional "Recover my device" page in the app.
