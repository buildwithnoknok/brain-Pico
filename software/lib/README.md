# software/lib — CircuitPython libraries the brain needs

Pinned copies of the Adafruit CircuitPython Bundle `.mpy` files that `code.py`
imports (MIT, © Adafruit Industries). Kept in the repo so a brain is
reproducible from one commit and so the one-file recovery image (DEV-38) can be
built without fetching a bundle. Copy the whole folder to `/lib` on the Pico.

| File | For |
|------|-----|
| `adafruit_httpserver/` | the setup-AP HTTP server (`/connect`, `/roles/*`, `/firmware/check`) |
| `adafruit_requests.mpy` + `adafruit_connection_manager.mpy` | HTTPS downloads (product script, firmware index + images) |
| `adafruit_ntp.mpy` | optional wall-clock sync for log timestamps |

Built for CircuitPython 10.x (`.mpy` format is major-version specific — refresh
these from the matching bundle when CircuitPython is upgraded). Rescued from the
bench brain on 15 Sep 2026, CRC-verified against the on-device copies.
