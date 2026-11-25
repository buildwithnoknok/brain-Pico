# SPDX-License-Identifier: MIT
# code.py — noknok brain entry point (stub). The real program is noknok_main (DEV-283).
#
# CircuitPython runs code.py by name and compiles it from SOURCE on the Pico at every boot.
# The program used to live here in full (~170 KB): near that size the compile left the brain
# joined to WiFi but deaf (no ARP / ping / HTTP; DEV-283, also seen 6 Nov). So code.py stays a
# stub and the program ships precompiled as noknok_main.mpy (tools/build_mpy.ps1): no on-device
# compile, and new code can go into noknok_main.py without moving the edge.
#
# Do not add code here. Version, changelog and everything else: noknok_main.py (CODE_VERSION).
import noknok_main

noknok_main.main()
