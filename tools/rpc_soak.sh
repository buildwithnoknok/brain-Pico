#!/bin/sh
# rpc_soak.sh — one-command DEV-34 risk-#1 soak on the Pi4 bench.
#
# Starts bench_rpc_soak.py on the Pico (serial, background, log to a file),
# waits for its "SOAK IP=" line, then starts rpc_soak_client.py against that
# address. Both run detached (nohup), so this returns immediately; check back
# with:  tail -n 5 ~/dev/pico/soak_pico.log ~/dev/pico/soak_client.log
#
# Usage:  sh rpc_soak.sh [minutes]        (default 65; Pico script has its own
#         SOAK_MINUTES — keep the two equal, edit both if you change one)
# Needs: bench_rpc_soak.py + rpc_soak_client.py next to pico.py (~/dev/pico).
set -e
cd "$(dirname "$0")"
MIN=${1:-65}
SERIAL_TIMEOUT=$(( MIN * 60 + 300 ))
: > soak_pico.log
: > soak_client.log

nohup ./pico.py run bench_rpc_soak.py "$SERIAL_TIMEOUT" > soak_pico.log 2>&1 &
echo "pico soak started (pid $!) — waiting for its IP"

IP=""
for i in $(seq 1 90); do
    IP=$(grep -o 'SOAK IP=[0-9.]*' soak_pico.log | head -1 | cut -d= -f2)
    [ -n "$IP" ] && break
    sleep 1
done
if [ -z "$IP" ]; then
    echo "no SOAK IP line after 90 s — see soak_pico.log:"; tail -n 20 soak_pico.log; exit 1
fi
# give the Conductor + server a moment to come up before the first request
sleep 8
nohup python3 rpc_soak_client.py "$IP" "$MIN" > soak_client.log 2>&1 &
echo "client started (pid $!) against $IP for $MIN min"
echo "logs: ~/dev/pico/soak_pico.log  ~/dev/pico/soak_client.log"
