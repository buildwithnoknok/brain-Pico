#!/bin/sh
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# flash_retry.sh <image> <uniid1> [attempts] [-t|-3]
#
# Retry flash_full_l2.sh until the 16 KB readback compares clean.
#
# Why retrying is safe: flash_full_l2.sh erases, writes, reads all 16 KB back and
# byte-compares before it reports success, and it refuses to touch a chip whose
# UNIID1 is not the one named. A failed attempt therefore cannot leave a silently
# bad image - it either verifies or it is reported as a failure and retried.
#
# Why retrying is needed: castellated half-hole pads give the pogo clamp marginal
# contact. A probe that returns "marchid ffffffff" / "Unknown chip type" is a
# contact or supply dropout, not a dead chip - see tools.md.
IMG="$1"
WANT1="$2"
N="${3:-6}"
PWR="${4:--t}"
GAP="${5:-2}"
DIR=/home/noknok/dev/restore_l2

[ -f "$IMG" ] || { echo "no image: $IMG"; exit 2; }
cd "$DIR" || exit 1

i=1
while [ "$i" -le "$N" ]; do
    echo "=================== attempt $i of $N ==================="
    OUT=$(sh flash_full_l2.sh "$IMG" "$WANT1" "$PWR" 2>&1)
    echo "$OUT" | grep -E 'Detected|UNIID1|Whole-chip|Image written|READBACK|REFUSE|Unknown chip|marchid|MISMATCH'
    if echo "$OUT" | grep -q 'READBACK OK'; then
        echo ">>> SUCCESS on attempt $i"
        exit 0
    fi
    if echo "$OUT" | grep -q 'REFUSE'; then
        echo ">>> REFUSED - wrong board or read protection on. Not retrying."
        exit 2
    fi
    i=$((i + 1))
    sleep "$GAP"
done
echo ">>> FAILED after $N attempts - re-seat the clamp on the castellated pads"
exit 1
