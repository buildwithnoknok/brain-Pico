#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# rpc_failure_modes.sh <brain-ip> - DEV-34 failure modes over /rpc, from any host on the LAN.
#
#   1. settings.set with a wrong type, a wrong format and an undeclared key   (expect rejected)
#   2. set non-default values, `reboot`, values must survive
#   3. `provision` with a dead script_url on home WiFi: the brain must come back on the OLD
#      product with `[CFG] product switch FAILED` in `status`
#   4. settings.reset (cleans up)
# Changes the brain's settings (reset at the end). Needs the brain online, product running.
H=${1:?usage: rpc_failure_modes.sh <brain-ip>}
R="python3 $(cd "$(dirname "$0")/.." && pwd)/rpc_call.py"
wait_back() {
  sleep "${1:-2}"
  for i in $(seq 1 40); do sleep 3; if $R $H hello >/dev/null 2>&1; then echo "back after ~$((${1:-2}+i*3)) s"; return 0; fi; done
  echo "NOT BACK after 2 min"; return 1
}
echo "### 1a wrong type";   $R $H settings.set '{"values":{"brightness":"abc"}}'
echo "### 1b wrong format"; $R $H settings.set '{"values":{"color":"blue"}}'
echo "### 1c undeclared key (DEV-109: currently accepted)"; $R $H settings.set '{"values":{"bench_unknown_key":true}}'
echo "### 2 set + reboot";  $R $H settings.set '{"values":{"color":"#0000FF","brightness":77}}'
$R $H reboot; wait_back 2; $R $H settings.get
echo "### 3 product switch with a dead URL"
$R $H provision '{"script_url":"https://raw.githubusercontent.com/buildwithnoknok/poc/main/scripts/bench_does_not_exist.py","product_id":"bench-dead-url-v1"}'
wait_back 8; $R $H hello | grep -A3 '"product"'; $R $H status | grep -E 'CFG'
echo "### 4 cleanup"; $R $H settings.reset
