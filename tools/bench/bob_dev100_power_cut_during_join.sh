#!/bin/bash
# Bob: DEV-100 power pull during the join: wait for a NEW join attempt in the console log, cut USB power (hub 2), restore it
N0=$(grep -ac 'attempting WiFi join' ~/bob_setup_console7.txt)
for i in $(seq 1 1800); do
  if [ "$(grep -ac 'attempting WiFi join' ~/bob_setup_console7.txt)" -gt "$N0" ]; then break; fi
  sleep 0.5
done
sleep 1.5
echo raspberry | sudo -S /usr/sbin/uhubctl -l 2 -a off > ~/bob_cut.log 2>&1
sleep 4
echo raspberry | sudo -S /usr/sbin/uhubctl -l 2 -a on >> ~/bob_cut.log 2>&1
echo "CUT DONE $(date +%T)" >> ~/bob_cut.log
