# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# status_probe.py — print the display module's (busy, last_err) without drawing.
# last_err is STICKY in firmware v0.5.0 (never cleared) — power-cycle the module
# to reset it. Pi4RFID bench pins GP20/21.
import board
from noknok import Conductor
c = Conductor(sda=board.GP20, scl=board.GP21)
c.enumerate()
d = c.display[0]
print("status (busy, last_err):", d.status(), " info:", d.info(refresh=True))
c.i2c.deinit()
