# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev74_cutenum.py - DEV-74 step 7: power loss DURING enumeration. Arms the
# Pico's watchdog (hard reset) for TIMEOUT seconds and runs enumerate(), which takes
# longer, so the brain resets part-way through the sweep of both buses, like a
# power cut. The NEXT boot must find every module again at its old address.
import microcontroller
from watchdog import WatchDogMode
import noknok

TIMEOUT = 1.0
c = noknok.Conductor()
microcontroller.watchdog.timeout = TIMEOUT
microcontroller.watchdog.mode = WatchDogMode.RESET
print("watchdog armed: %.1f s, enumerating..." % TIMEOUT)
c.enumerate()
print("enumerate finished before the watchdog? (no cut)")
microcontroller.watchdog.deinit()
