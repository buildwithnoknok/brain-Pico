# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# crash_product.py - a product that crashes on purpose at every start. Put it on a brain as
# /data/product.py (`pico.py put crash_product.py /data/product.py`) to exercise the 3-strike crash
# recovery and safe idle (DEV-32, DEV-7). Online, safe idle replaces it with the published product
# within seconds; to stay parked, make the brain offline first (wifi_offline.py).
print("[bench] crash test product starting - raising now")
raise RuntimeError("bench crash_product.py: deliberate crash")
