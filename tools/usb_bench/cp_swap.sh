#!/bin/sh
# SPDX-License-Identifier: MIT
# cp_swap.sh - run ON THE BENCH HOST (Pi4): install a CircuitPython .uf2 on the bench Pico
# without touching it (DEV-45 A/B tests). Files on CIRCUITPY survive a UF2 install.
#   sh cp_swap.sh <firmware.uf2> [pico.py dir, default ~/dev/pico]
# Steps: pico.py runs a 3-line script that reboots into the UF2 loader -> the RP2350 drive
# appears -> mount, copy, sync -> the Pico reboots into the new CircuitPython -> print
# boot_out.txt. Needs sudo (password on stdin, as for uhubctl on the bench Pis).
set -u
UF2="$1"; P="${2:-$HOME/dev/pico}"; MNT=/mnt/rp2uf2
[ -f "$UF2" ] || { echo "no such uf2: $UF2"; exit 2; }
cat > /tmp/to_uf2.py <<'EOF'
import microcontroller
microcontroller.on_next_reset(microcontroller.RunMode.UF2)
microcontroller.reset()
EOF
( cd "$P" && timeout 30 python3 pico.py run /tmp/to_uf2.py 10 ) >/dev/null 2>&1
i=0; while [ ! -e /dev/disk/by-label/RP2350 ] && [ $i -lt 30 ]; do sleep 1; i=$((i+1)); done
[ -e /dev/disk/by-label/RP2350 ] || { echo "UF2 drive did not appear (Pico wedged? needs a replug)"; exit 3; }
echo raspberry | sudo -S -p "" mkdir -p $MNT
echo raspberry | sudo -S -p "" mount /dev/disk/by-label/RP2350 $MNT || exit 4
echo raspberry | sudo -S -p "" cp "$UF2" $MNT/ ; sync
echo raspberry | sudo -S -p "" umount $MNT 2>/dev/null
i=0; while ! ls /dev/serial/by-id/usb-Raspberry_Pi_Pico* >/dev/null 2>&1 && [ $i -lt 40 ]; do sleep 1; i=$((i+1)); done
sleep 5
cat > /tmp/ver.py <<'EOF'
import os; print(os.uname().version)
EOF
cd "$P"; for t in 1 2 3; do timeout 30 python3 pico.py run /tmp/ver.py 10 && break; sleep 3; done
