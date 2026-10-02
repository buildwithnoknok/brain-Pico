# SPDX-License-Identifier: MIT
# hard_reset.py - run ON THE PICO: full chip reset (like a power cycle for the USB host
# port). pico.py reports a SerialException afterwards - expected. Wait ~25 s, then retry
# (the first REPL access after a reset times out once).
import microcontroller
microcontroller.reset()
