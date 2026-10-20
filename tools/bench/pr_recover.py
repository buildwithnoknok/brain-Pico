# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# pr_recover.py - DEV-124 power-pull run: if the power manager is parked at 0x7E, push the app again
# (what the brain's start-up rescue does), then report.
exec(open("/bl_x035_bench.py").read())
recover("/picohub_030.bin")
a = find_hub(timeout=8)
print("PR RECOVERED", a is not None, app_version(a) if a is not None else None)
