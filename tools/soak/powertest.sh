#!/bin/sh
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# powertest.sh "<uhubctl args>" [off_seconds]
#
# Try ONE power-cut method and PROVE whether the Pico actually reset, by
# comparing time.monotonic() across it.
#
# Why this exists: on 24 Sep 2026 a soak rig was built on `uhubctl -l 1-1 -p 4`
# and looked like it worked - the serial device disappeared and came back, and
# reset_reason read POWER_ON every time. It was cutting the DATA link only.
# reset_reason reports the LAST reset cause, so it stays POWER_ON for hours and
# proves nothing. A monotonic clock that jumps BACKWARDS is the only proof.
#
#   sh powertest.sh "-l 1-1 -p 4"      one port
#   sh powertest.sh "-l 1-1"           all ports of that hub (ganged)
#   sh powertest.sh "-l 1-1 -p 1-4"    all ports, named explicitly
ARGS="$1"
OFF=${2:-6}
cd /home/noknok/dev/pico || exit 1

BEFORE=$(timeout 60 python3 pico.py run uptime_pico.py 40 2>&1 | grep -o 'monotonic=[0-9.]*' | head -1)
echo "method : uhubctl $ARGS"
echo "before : $BEFORE"

echo raspberry | sudo -S /usr/sbin/uhubctl $ARGS -a off > /tmp/uh_off.txt 2>&1
tail -3 /tmp/uh_off.txt | sed 's/^/  off: /'
sleep "$OFF"
echo raspberry | sudo -S /usr/sbin/uhubctl $ARGS -a on > /tmp/uh_on.txt 2>&1
tail -3 /tmp/uh_on.txt | sed 's/^/  on : /'

i=0
while [ $i -lt 30 ]; do
    ls /dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00 >/dev/null 2>&1 && break
    i=$((i+1)); sleep 1
done
sleep 4
AFTER=$(timeout 60 python3 pico.py run uptime_pico.py 40 2>&1 | grep -o 'monotonic=[0-9.]*' | head -1)
echo "after  : $AFTER  (serial back after ${i}s)"
echo "VERDICT: 'after' SMALLER than 'before' = the Pico really reset."
