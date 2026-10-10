# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# hard_reset.py - runs ON the Pico (pico.py run). A real reset, so boot.py runs again
# (a soft reload does not re-run it).
import microcontroller
microcontroller.reset()
