# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# pr_update.py - DEV-124 power-pull run, Pico side: start ONE app update of the MOD-0011 power manager
# (alternating 0.3.0 <-> 0.3.1) and print markers. The Pi cuts the power at a random moment while this runs.
# Needs /bl_x035_bench.py, /picohub_030.bin, /picohub_031.bin on the Pico.
exec(open("/bl_x035_bench.py").read())
a = find_hub()
v = app_version(a) if a is not None else None
print("PR START", v)
target = "/picohub_031.bin" if (v is None or v[3] == 0) else "/picohub_030.bin"
img = open(target, "rb").read()
err = _push(img, from_addr=a) if a is not None else _push(img)
print("PR PUSHED", err)
F.boot()
time.sleep(1.5)
print("PR DONE", find_hub())
