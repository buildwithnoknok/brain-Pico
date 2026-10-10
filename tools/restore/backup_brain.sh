#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# backup_brain.sh [DEST]  - copy every file from a (possibly damaged) brain Pico to
# the machine this runs on, over the REPL. Read-only on the Pico, so it is safe on a
# corrupted filesystem. Run it BEFORE restore_brain.sh when anything on the Pico might
# be worth keeping (setup data, logs, bench files).
#
# DEST defaults to ~/pico-backup-<date>. KEEP IT OUT OF GIT: it can hold wifi.json
# (the customer's WiFi password) and product.py.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PICO="python3 $REPO/tools/pico.py"
DEST=${1:-$HOME/pico-backup-$(date +%Y%m%d-%H%M%S)}
mkdir -p "$DEST" || exit 1
echo "backup -> $DEST"
$PICO gettree / "$DEST" 2>&1 | grep -E 'got|rror' | tail -n 60
echo "--- files saved:"
find "$DEST" -type f | wc -l
echo "NOTE: $DEST may contain wifi.json - never commit it."
