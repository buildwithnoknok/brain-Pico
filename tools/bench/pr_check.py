# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# pr_check.py - DEV-124 power-pull run, Pico side: classify the power manager after a power cut:
# running app (with version), parked in the bootloader at 0x7E, or not answering at all.
exec(open("/bl_x035_bench.py").read())
a = find_hub(timeout=8)
if a is not None:
    print("PR STATE app", app_version(a))
else:
    st = F._read_status()
    print("PR STATE", "parked" if st is not None else "none", st)
