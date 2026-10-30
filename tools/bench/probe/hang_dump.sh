#!/bin/sh
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# hang_dump.sh [tag] - DEV-172/173: snapshot a frozen brain (Pico 2 W) over SWD with the Raspberry Pi
# Debug Probe, plus 3 s of usbmon on USB bus 1. Run on the bench Pi WHILE the brain is frozen.
# Output: $DUMP_DIR (default ~/dev/dev172/dumps)/<time>_<tag>.txt (+ .usbmon). Releases both cores
# at the end. sudo: set BENCH_SUDO_PW in the environment, or run with passwordless sudo.
HERE=$(cd "$(dirname "$0")" && pwd)
sudo_() { if [ -n "${BENCH_SUDO_PW:-}" ]; then printf '%s\n' "$BENCH_SUDO_PW" | sudo -S -p '' "$@"; else sudo "$@"; fi; }
D=${DUMP_DIR:-$HOME/dev/dev172/dumps}; mkdir -p "$D"
T=$(date +%Y%m%d_%H%M%S)_${1:-hang}
OUT=$D/$T.txt
{
  echo "=== lsusb -t"; lsusb -t
  echo "=== usbmon bus 1, 3 s"
  sudo_ modprobe usbmon 2>/dev/null
  sudo_ timeout 3 cat /sys/kernel/debug/usb/usbmon/1u > "$D/$T.usbmon" 2>&1
  echo "usbmon lines: $(wc -l < "$D/$T.usbmon") -> $T.usbmon"
} > "$OUT" 2>&1
sudo_ timeout 120 openocd -f interface/cmsis-dap.cfg -f target/rp2350.cfg \
    -c 'adapter speed 5000' -c init -f "$HERE/hang_dump.tcl" -c shutdown >> "$OUT" 2>&1
echo "$OUT"; grep -E 'sample|Error|usbmon lines' "$OUT" | tail -3
