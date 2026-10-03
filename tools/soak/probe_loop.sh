#!/bin/sh
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# probe_loop.sh [attempts] [gap_seconds] [-t|-3]
#
# Hammer the SWD probe on a marginal clamp and report the hit rate. Castellated
# half-hole pads give intermittent contact, so a single failed probe means
# nothing - what matters is whether a clean read EVER happens, and how often.
#
#   marchid ffffffff -> line floating (no contact)
#   marchid 00000000 -> line held low (wrong pad, bridge, or firmware driving PD1)
#   Detected CH32V003 -> good contact
N="${1:-40}"
GAP="${2:-3}"
PWR="${3:--t}"
M=/home/noknok/dev/ch32fun/minichlink/minichlink

ok=0
i=1
while [ "$i" -le "$N" ]; do
    OUT=$($M "$PWR" -i 2>&1)
    if echo "$OUT" | grep -q 'Detected CH32V003'; then
        ok=$((ok + 1))
        UID1=$(echo "$OUT" | grep -o 'R32_ESIG_UNIID1: [0-9a-f]*' | head -1)
        echo "attempt $i: OK   $UID1"
    else
        echo "attempt $i: fail $(echo "$OUT" | grep -o 'marchid : [0-9a-f]*' | head -1)"
    fi
    i=$((i + 1))
    [ "$i" -le "$N" ] && sleep "$GAP"
done
echo "=========================================="
echo "clean probes: $ok of $N"
