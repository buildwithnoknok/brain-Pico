# tools/fixtures - third-party sources for host tests only

`adafruit_httpserver/` - Adafruit CircuitPython HTTPServer **4.8.2** (MIT, (c) Dan Halbert for
Adafruit Industries, Michał Pokusa), unmodified `.py` sources from
github.com/adafruit/Adafruit_CircuitPython_HTTPServer tag 4.8.2: the version the brain ships as
`.mpy` in `software/lib/adafruit_httpserver/`. Used by `tools/test_http_freeze.py` (DEV-170) to run
the library's real `Server.poll()` on a PC. Never copied to a brain. Update together with
`software/lib/` when the bundle is refreshed.
