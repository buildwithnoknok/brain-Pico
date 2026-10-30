#!/bin/sh
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# swd_reset.sh [tag] - reset the brain's RP2350 over SWD (Debug Probe). Recovers a DEV-172 freeze
# without unplugging anything (boots like a power-on). sudo: BENCH_SUDO_PW or passwordless sudo.
sudo_() { if [ -n "${BENCH_SUDO_PW:-}" ]; then printf '%s\n' "$BENCH_SUDO_PW" | sudo -S -p '' "$@"; else sudo "$@"; fi; }
sudo_ timeout 30 openocd -f interface/cmsis-dap.cfg -f target/rp2350.cfg \
    -c 'adapter speed 5000' -c init -c 'reset run' -c shutdown 2>&1 | tail -4
echo "swd reset done ${1:-}"
