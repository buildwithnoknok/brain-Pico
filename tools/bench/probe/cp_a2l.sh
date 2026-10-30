#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
# cp_a2l.sh ADDR... - name the function + source line for RP2350 addresses (CP 10.3.1 pico2_w build)
cd ~/cpbuild && export PATH=$PWD/gcc/bin:$PATH
E=circuitpython/ports/raspberrypi/build-raspberry_pi_pico2_w/firmware.elf
arm-none-eabi-addr2line -f -i -p -C -e $E "$@" | sed "s#$HOME/cpbuild/circuitpython/##"
