# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev133_due.py - DEV-133, tier M, runs ON THE PICO (pico.py run). Makes the daily OTA
# check due (nvm[1:5] = 0) and hard-resets, so the next boot runs the online pass. With a
# LEDs 16x below the published version, expected: [FW] ... PENDING, hard reset, [USBFW]
# flashed, hard reset, [USBFW] verified, product up. Read the result with bench_dev133_read.py.
import time
import microcontroller

microcontroller.nvm[1:5] = bytes(4)
print("OTA check due; hard reset")
time.sleep(0.5)
microcontroller.reset()
