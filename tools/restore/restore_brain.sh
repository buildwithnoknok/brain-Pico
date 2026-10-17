#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# restore_brain.sh - reformat a brain Pico and restore it from THIS repo checkout (DEV-84).
#
#   tools/restore/restore_brain.sh [--data DIR] [--mpy DIR] [--settings FILE]
#                                  [--allow-source] [--yes] [--no-backup] [--dry-run]
#
# Use it when a brain's filesystem is corrupted or code.py is gone after a power cut
# (DEV-18 / ADR-003). It takes the code straight from software/ in this checkout, so
# the brain always gets the current code - there is no second copy to go stale.
#
# What it does:
#   1. backs the Pico up (backup_brain.sh, read-only)           [skip: --no-backup]
#   2. reformats CIRCUITPY (erase_fs.py)                         DESTROYS the files
#   3. copies code, libraries, settings.toml, then boot.py LAST  (a fresh FS shows its
#      drive to the Pi; the files go over that mount - see below)
#   4. clears the crash-strike counter, hard-resets and verifies (verify_brain.py,
#      bench_fs_policy.py)
#
# Everything that decides what ends up on the brain is resolved BEFORE the erase, and the
# script stops there if it cannot do it properly (nothing is wiped on a half-plan).
#
#   --data DIR    put back a backup made by backup_brain.sh: data/ (wifi.json, product.py,
#                 firmware cache), noknok_state.json, noknok_roles.json AND the backup's
#                 settings.toml (the maker's pins, e.g. I2C on GP20/21) - see --settings.
#                 Without --data the brain comes back factory-fresh with the repo's
#                 default settings.toml (I2C on GP8/GP9) and opens the noknok-setup WiFi.
#   --settings F  use this settings.toml instead (wins over the backup's and the repo's).
#   --mpy DIR     directory with noknok.mpy, noknok_rpc.mpy, noknok_usb.mpy and
#                 module_flasher.mpy built by tools/build_mpy.ps1 for the CircuitPython
#                 version on the Pico. Libraries MUST be .mpy: noknok.py is ~190 KB of
#                 source and does not fit the Pico's RAM next to a product (MemoryError).
#                 Without --mpy the script uses, in this order: mpy-cross on this host
#                 ($MPY_CROSS or on the PATH; its version must match the Pico's), else the
#                 four .mpy found in the --data backup (the brain's own libraries).
#   --allow-source  no .mpy available? install the .py sources instead. Only for a small
#                 product: a big one (e.g. Smart Lamp Maxi) crashes with MemoryError.
#   --dry-run     resolve and print the plan, change nothing (no erase, no copy).
#   --yes         do not ask for the ERASE confirmation
#
# Runs on the bench host (Linux, e.g. the Pi4) with exactly ONE brain Pico attached.
# Needs python3 + pyserial (tools/pico.py). Mounting the fresh drive uses udisksctl or,
# failing that, `sudo mount`: passwordless sudo, or a prompt in a terminal, or the
# password in the environment variable PICO_SUDO_PASS for an unattended run.
# Nothing is stored anywhere.
#
# Why a mount: after the reformat the drive is visible (there is no boot.py yet) and the
# Pi auto-mounts it. While the host holds the filesystem, the Pico cannot write to it
# over the REPL, so the files go through the mount. boot.py is copied last, so the drive
# only disappears again (read-only brain, NOKNOK_USB_DRIVE = 0) at the final reset.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
SW=$REPO/software
PICO="python3 $REPO/tools/pico.py"
LIBS="noknok noknok_rpc noknok_usb module_flasher"     # what code.py imports, see build_mpy.ps1

DATA=""; MPYDIR=""; SETTINGS=""; ALLOW_SRC=0; YES=0; BACKUP=1; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --data) DATA=${2:-}; shift 2 ;;
    --mpy) MPYDIR=${2:-}; shift 2 ;;
    --settings) SETTINGS=${2:-}; shift 2 ;;
    --allow-source) ALLOW_SRC=1; shift ;;
    --yes) YES=1; shift ;;
    --no-backup) BACKUP=0; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,48p' "$0"; exit 0 ;;
    *) echo "unknown option $1 (see --help)"; exit 2 ;;
  esac
done

# ── Resolve the plan (nothing is touched yet) ───────────────────────────────────
for f in code.py settings.toml boot.py $(for l in $LIBS; do echo $l.py; done); do
  [ -f "$SW/$f" ] || { echo "missing $SW/$f - nothing erased"; exit 1; }
done
[ -d "$SW/lib" ] || { echo "missing $SW/lib - nothing erased"; exit 1; }
if [ -n "$DATA" ] && [ ! -d "$DATA" ]; then echo "--data $DATA is not a directory"; exit 1; fi
if [ -n "$SETTINGS" ] && [ ! -f "$SETTINGS" ]; then echo "--settings $SETTINGS is not a file"; exit 1; fi
if [ -n "$MPYDIR" ] && [ ! -d "$MPYDIR" ]; then echo "--mpy $MPYDIR is not a directory"; exit 1; fi

# settings.toml: --settings > the backup's > the repo default.
if [ -n "$SETTINGS" ]; then SETTINGS_SRC=$SETTINGS; SETTINGS_WHY="--settings"
elif [ -n "$DATA" ] && [ -f "$DATA/settings.toml" ]; then SETTINGS_SRC=$DATA/settings.toml; SETTINGS_WHY="the backup's (the maker's pins)"
else SETTINGS_SRC=$SW/settings.toml; SETTINGS_WHY="repo default (I2C on GP8/GP9)"
fi

# Libraries as .mpy: --mpy > mpy-cross on this host > the backup's own > (--allow-source) .py
have_all_mpy() { for l in $LIBS; do [ -f "$1/$l.mpy" ] || return 1; done; }
LIBMODE=""; LIBDIR=""; TMPMPY=""
if [ -n "$MPYDIR" ]; then
  have_all_mpy "$MPYDIR" || { echo "--mpy $MPYDIR must hold: $(for l in $LIBS; do printf '%s.mpy ' $l; done)- nothing erased"; exit 1; }
  LIBMODE=mpy; LIBDIR=$MPYDIR; LIBWHY="--mpy $MPYDIR"
else
  XC=${MPY_CROSS:-$(command -v mpy-cross 2>/dev/null || true)}
  if [ -n "$XC" ] && [ -x "$XC" ]; then
    TMPMPY=$(mktemp -d); ok=1
    for l in $LIBS; do "$XC" -O1 -o "$TMPMPY/$l.mpy" "$SW/$l.py" >/dev/null 2>&1 || ok=0; done
    if [ $ok -eq 1 ]; then LIBMODE=mpy; LIBDIR=$TMPMPY; LIBWHY="compiled here with $XC (version must match the Pico's CircuitPython)"
    else echo "(mpy-cross at $XC failed on the sources - trying the next source)"; fi
  fi
  if [ -z "$LIBMODE" ] && [ -n "$DATA" ] && have_all_mpy "$DATA"; then
    LIBMODE=mpy; LIBDIR=$DATA; LIBWHY="the backup's own .mpy (the brain's previous libraries)"
  fi
fi
if [ -z "$LIBMODE" ]; then
  if [ $ALLOW_SRC -eq 1 ]; then LIBMODE=src; LIBDIR=$SW; LIBWHY="--allow-source: .py sources (a big product will run out of RAM)"
  else
    echo "No .mpy libraries available - nothing erased."
    echo "  The Pico cannot import noknok.py as source next to a product (MemoryError). Give it .mpy:"
    echo "    --mpy DIR    built on the PC with tools/build_mpy.ps1 (copy the folder here), or"
    echo "    mpy-cross on this host (MPY_CROSS=/path or on the PATH), or"
    echo "    --data DIR   a backup that contains the brain's own .mpy files, or"
    echo "    --allow-source   to install .py sources anyway (small products only)."
    exit 1
  fi
fi

echo "Restore the brain from $SW"
echo "  libraries:     $LIBMODE  ($LIBWHY)"
echo "  settings.toml: $SETTINGS_WHY"
[ -n "$DATA" ] && echo "  setup data:    $DATA"
if [ $DRY -eq 1 ]; then echo "(dry run: nothing erased, nothing copied)"; [ -n "$TMPMPY" ] && rm -rf "$TMPMPY"; exit 0; fi
$PICO ls / >/dev/null 2>&1 || { echo "cannot talk to a Pico (is exactly one attached, CircuitPython running?)"; exit 1; }
if [ $YES -ne 1 ]; then
  read -r -p "This ERASES every file on the Pico. Type ERASE to go on: " ans
  [ "$ans" = "ERASE" ] || { echo "aborted"; exit 1; }
fi

if [ $BACKUP -eq 1 ]; then
  echo "--- 1/4 backup"; "$HERE/backup_brain.sh" || echo "(backup incomplete - the filesystem may be damaged; going on)"
fi

echo "--- 2/4 reformat"
$PICO run "$HERE/erase_fs.py" >/dev/null 2>&1
for i in $(seq 1 30); do [ -e /dev/disk/by-label/CIRCUITPY ] && break; sleep 1; done

echo "--- 3/4 copy files"
# Root is needed only to mount/unmount the fresh drive. Order: passwordless sudo, then
# an interactive prompt (when run in a terminal), then the password in the environment
# variable PICO_SUDO_PASS (for unattended runs: PICO_SUDO_PASS=... restore_brain.sh).
# The password is never written to a file.
as_root() {
  if sudo -n true 2>/dev/null; then sudo "$@"
  elif [ -t 0 ]; then sudo "$@"
  elif [ -n "${PICO_SUDO_PASS:-}" ]; then printf '%s\n' "$PICO_SUDO_PASS" | sudo -S -p '' "$@"
  else echo "root is needed to mount the drive: run in a terminal or set PICO_SUDO_PASS" >&2; return 1
  fi
}
DEV=$(readlink -f /dev/disk/by-label/CIRCUITPY 2>/dev/null || true)
[ -n "$DEV" ] || { echo "the fresh CIRCUITPY drive did not show up on the host - cannot copy. Unplug/replug the Pico and rerun with --no-backup."; exit 1; }
MP=$(findmnt -rn -S "$DEV" -o TARGET | head -1); OWN=0
if [ -z "$MP" ]; then
  if command -v udisksctl >/dev/null 2>&1 && udisksctl mount -b "$DEV" >/dev/null 2>&1; then
    MP=$(findmnt -rn -S "$DEV" -o TARGET | head -1)
  else
    MP=/mnt/circuitpy; as_root mkdir -p "$MP" && as_root mount -o "uid=$(id -u),gid=$(id -g)" "$DEV" "$MP" || { echo "could not mount $DEV"; exit 1; }
    OWN=1
  fi
fi
[ -n "$MP" ] || { echo "no mount point for $DEV"; exit 1; }
echo "  drive $DEV mounted at $MP"

rm -f "$MP/code.py" "$MP/settings.toml"                       # CircuitPython's samples
cp "$SW/code.py" "$MP/code.py" || exit 1
cp "$SETTINGS_SRC" "$MP/settings.toml" || exit 1
for l in $LIBS; do                                            # .mpy OR .py, never both
  if [ "$LIBMODE" = mpy ]; then cp "$LIBDIR/$l.mpy" "$MP/$l.mpy" || exit 1
  else cp "$LIBDIR/$l.py" "$MP/$l.py" || exit 1; fi
done
mkdir -p "$MP/lib" && cp -r "$SW/lib/." "$MP/lib/" || exit 1
if [ -n "$DATA" ]; then
  [ -d "$DATA/data" ] && cp -r "$DATA/data" "$MP/"
  for f in noknok_state.json noknok_roles.json; do [ -f "$DATA/$f" ] && cp "$DATA/$f" "$MP/"; done
fi
cp "$SW/boot.py" "$MP/boot.py" || exit 1                       # boot.py LAST
sync; sleep 2
echo "  on the drive: $(ls "$MP" | tr '\n' ' ')"
if [ $OWN -eq 1 ]; then as_root umount "$MP"; else udisksctl unmount -b "$DEV" >/dev/null 2>&1 || as_root umount "$DEV"; fi
sleep 1
[ -n "$TMPMPY" ] && rm -rf "$TMPMPY"

echo "--- 4/4 clear strikes, hard reset, verify"
# The crash-strike counter lives in nvm and survives a reformat: a brain that had parked
# itself after 3 crashes would stay parked until a power cycle even when it is repaired.
$PICO run "$HERE/clear_strikes.py" 2>&1 | grep -E 'strikes' | head -n 1
$PICO run "$HERE/hard_reset.py" >/dev/null 2>&1
sleep 12
if grep -Eq '^NOKNOK_USB_DRIVE *= *0' "$SETTINGS_SRC"; then
  if [ -e /dev/disk/by-label/CIRCUITPY ]; then echo "  FAIL: drive still visible (boot.py did not hide it)"; RC=1; else echo "  drive hidden (read-only brain): ok"; fi
fi
OUT=$($PICO run "$HERE/verify_brain.py" 60 2>&1); echo "$OUT" | tail -n 30
echo "$OUT" | grep -q "VERIFY OK" || RC=1
$PICO run "$SW/bench_fs_policy.py" 2>&1 | grep -E 'PASS|FAIL|SUMMARY|boot:' | tail -n 15
[ "${RC:-0}" -eq 0 ] && echo "RESTORE OK" || { echo "RESTORE FAILED - see above"; exit 1; }
