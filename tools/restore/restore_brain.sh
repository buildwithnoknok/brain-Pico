#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# restore_brain.sh - reformat a brain Pico and restore it from THIS repo checkout (DEV-84).
#
#   tools/restore/restore_brain.sh [--data DIR] [--yes] [--no-backup]
#
# Use it when a brain's filesystem is corrupted or code.py is gone after a power cut
# (DEV-18 / ADR-003). It takes the files straight from software/ in this checkout, so
# the brain always gets the current code - there is no second copy to go stale.
#
# What it does:
#   1. backs the Pico up (backup_brain.sh, read-only)           [skip: --no-backup]
#   2. reformats CIRCUITPY (erase_fs.py)                         DESTROYS the files
#   3. copies code, libraries, settings.toml, then boot.py LAST  (a fresh FS shows its
#      drive to the Pi; the files go over that mount - see below)
#   4. hard-resets and verifies (verify_brain.py, bench_fs_policy.py)
#
#   --data DIR   also put back setup data from a backup made by backup_brain.sh:
#                data/ (wifi.json, product.py, firmware cache), noknok_state.json,
#                noknok_roles.json. Without it the brain comes back factory-fresh and
#                opens the noknok-setup WiFi for the app (that IS the designed recovery).
#   --yes        do not ask for the ERASE confirmation
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

DATA=""; YES=0; BACKUP=1
while [ $# -gt 0 ]; do
  case "$1" in
    --data) DATA=${2:-}; shift 2 ;;
    --yes) YES=1; shift ;;
    --no-backup) BACKUP=0; shift ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "unknown option $1 (see --help)"; exit 2 ;;
  esac
done

# What a brain needs: everything code.py imports + settings. Checked against the repo
# first so a missing file stops the script BEFORE anything is erased.
CODE_FILES="code.py noknok.py noknok_rpc.py noknok_usb.py module_flasher.py settings.toml"
for f in $CODE_FILES boot.py; do
  [ -f "$SW/$f" ] || { echo "missing $SW/$f - nothing erased"; exit 1; }
done
[ -d "$SW/lib" ] || { echo "missing $SW/lib - nothing erased"; exit 1; }
if [ -n "$DATA" ] && [ ! -d "$DATA" ]; then echo "--data $DATA is not a directory"; exit 1; fi
$PICO ls / >/dev/null 2>&1 || { echo "cannot talk to a Pico (is exactly one attached, CircuitPython running?)"; exit 1; }

echo "Restore the brain from $SW"
[ -n "$DATA" ] && echo "  and the setup data in $DATA"
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
for f in $CODE_FILES; do cp "$SW/$f" "$MP/$f" || exit 1; done
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

echo "--- 4/4 hard reset + verify"
$PICO run "$HERE/hard_reset.py" >/dev/null 2>&1
sleep 12
if grep -Eq '^NOKNOK_USB_DRIVE *= *0' "$SW/settings.toml"; then
  if [ -e /dev/disk/by-label/CIRCUITPY ]; then echo "  FAIL: drive still visible (boot.py did not hide it)"; RC=1; else echo "  drive hidden (read-only brain): ok"; fi
fi
OUT=$($PICO run "$HERE/verify_brain.py" 2>&1); echo "$OUT" | tail -n 25
echo "$OUT" | grep -q "VERIFY OK" || RC=1
$PICO run "$SW/bench_fs_policy.py" 2>&1 | grep -E 'PASS|FAIL|SUMMARY|boot:' | tail -n 15
[ "${RC:-0}" -eq 0 ] && echo "RESTORE OK" || { echo "RESTORE FAILED - see above"; exit 1; }
