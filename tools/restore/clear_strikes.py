# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# clear_strikes.py - runs ON the Pico (pico.py run). Resets the three-strikes crash
# counter in nvm (byte 0, see code.py CRASH_NVM_INDEX / _set_crash_count).
#
# Why: the counter lives in nvm, which a filesystem reformat does not touch. A brain
# that parked itself in safe idle after 3 product crashes would stay parked after a
# restore (DEV-111). Only byte 0 is written; the Store record at nvm[64:] is untouched.
# Keep MAGIC in step with code.py CRASH_MAGIC.
import microcontroller

MAGIC = 0xC0
before = microcontroller.nvm[0]
microcontroller.nvm[0] = MAGIC | 0
print("strikes: nvm[0] %02x -> %02x (count %d -> 0)"
      % (before, microcontroller.nvm[0], (before & 0x0F) if (before & 0xF0) == MAGIC else 0))
