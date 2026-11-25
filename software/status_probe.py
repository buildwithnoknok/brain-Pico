# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# status_probe.py — print the display module's (busy, last_err) without drawing.
# Display fw 0.7.1+ (DEV-44): last_err is read-to-clear, so this shows the errors
# since the previous status read (the driver's own polls count). On fw <= 0.7.0
# it was sticky until a power cycle. I2C pins come from settings.toml (DEV-74).
import board
from noknok import Conductor
c = Conductor()      # pins from settings.toml (DEV-74)
c.enumerate()
d = c.display[0]
print("status (busy, last_err):", d.status(), " info:", d.info(refresh=True))
c.deinit()
