#!/bin/bash
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
# DEV-172: build CircuitPython 10.3.1 for raspberry_pi_pico2_w (en_US) with Adafruit's CI compiler
# (Arm GNU 15.2.Rel1) to get firmware.elf symbols; then compare against the official .uf2.
set -ex
mkdir -p ~/cpbuild && cd ~/cpbuild
if [ ! -x gcc/bin/arm-none-eabi-gcc ]; then
  curl -fL -o gcc.tar.xz https://developer.arm.com/-/media/Files/downloads/gnu/15.2.rel1/binrel/arm-gnu-toolchain-15.2.rel1-x86_64-arm-none-eabi.tar.xz
  mkdir -p gcc && tar xf gcc.tar.xz -C gcc --strip-components=1
fi
export PATH=$PWD/gcc/bin:$PATH
arm-none-eabi-gcc --version | head -1
[ -d circuitpython ] || git clone --depth 1 --branch 10.3.1 https://github.com/adafruit/circuitpython.git
if [ ! -x venv/bin/pip ]; then rm -rf venv; python3 -m venv --without-pip venv; curl -fsSL -o get-pip.py https://bootstrap.pypa.io/get-pip.py; venv/bin/python3 get-pip.py -q; fi
. venv/bin/activate
cd circuitpython
pip install -q -r requirements-dev.txt cmake ninja
make -C ports/raspberrypi fetch-port-submodules
make -C mpy-cross -j16
make -C ports/raspberrypi BOARD=raspberry_pi_pico2_w TRANSLATION=en_US -j16
ls -la ports/raspberrypi/build-raspberry_pi_pico2_w/firmware.*
curl -fL -o ~/cpbuild/official.uf2 https://adafruit-circuit-python.s3.amazonaws.com/bin/raspberry_pi_pico2_w/en_US/adafruit-circuitpython-raspberry_pi_pico2_w-en_US-10.3.1.uf2
cmp ports/raspberrypi/build-raspberry_pi_pico2_w/firmware.uf2 ~/cpbuild/official.uf2 && echo UF2_IDENTICAL || echo UF2_DIFFERENT
