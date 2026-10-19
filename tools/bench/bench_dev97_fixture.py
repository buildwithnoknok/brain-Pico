# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev97_fixture.py - DEV-97 bench fixture for the OFFLINE boot path.
# Writes /data/wifi.json with a SSID that does not exist (no secret: empty password)
# and a minimal /data/product.py, so code.py takes the "offline, product present"
# branch: rescue_offline() -> _cached_image. Run again with REMOVE = True to undo.
# Python only touches /data inside nk.writable() windows (ADR-003).
import json
import noknok as nk

REMOVE = False
PRODUCT = "import time\nwhile True:\n    time.sleep(1)\n"
WIFI = {"ssid": "bob-no-such-network", "password": "", "script_url": None,
        "module_firmware": None, "product_id": None}

with nk.writable():
    if REMOVE:
        print("removed:", nk.remove("/data/wifi.json", "/data/product.py"))
    else:
        try:
            import os
            os.mkdir("/data")
        except OSError:
            pass
        with open("/data/wifi.json", "w") as f:
            f.write(json.dumps(WIFI))
        with open("/data/product.py", "w") as f:
            f.write(PRODUCT)
        print("fixture written")
