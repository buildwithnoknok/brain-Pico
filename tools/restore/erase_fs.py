# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# erase_fs.py - runs ON the Pico (pico.py run). Reformats CIRCUITPY. DESTROYS every
# file on the brain; the Pico resets itself afterwards. CircuitPython and the ROM
# bootloader are not touched. Use through restore_brain.sh, not by hand.
import storage
storage.erase_filesystem()
