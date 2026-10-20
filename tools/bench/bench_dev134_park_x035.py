# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev134_park_x035.py - DEV-134, tier M, runs ON THE PICO (pico.py run). Parks the
# PicoHub power manager (X035) in its bootloader with no app - J2 (USB host port VBUS) goes
# off, like an interrupted X035 update - then soft-reloads the brain. Needs /bl_x035_bench.py
# (module-I2C-picohub-MOD-0011 firmware/test/) on the Pico and the OTA check NOT due.
# Expected next boots: [RESCUE] ... type=picohub action=reflashed, [BOOT] hard reset before the
# product (J2 was off), then the product finds the USB module (no [CRASH] strikes).
# Before code.py 0.27 the soft reload left the USB module missing: 3 strikes, parked (DEV-134).
import time
import supervisor

exec(open("/bl_x035_bench.py").read())
hub = find_hub()
print("hub at", hub)
enter_bl(hub)
time.sleep(0.5)
F.erase()
print("X035 parked without an app (J2 off); soft reload")
time.sleep(1.0)
supervisor.reload()
