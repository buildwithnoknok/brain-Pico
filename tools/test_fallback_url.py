# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_fallback_url.py - DEV-265, tier S (host Python, needs internet).
#
# code.py's fallback SCRIPT_URL (a brain whose credentials carry no script_url) must return HTTP 200:
# a dead URL made the brain 404 on every boot and fall back to the setup hotspot. This test reads
# the constant from software/code.py as text (nothing is imported or run) and fetches it.
#
# Run from the repo root:   python -I tools/test_fallback_url.py
# Exit 0 = HTTP 200 and the body looks like a Python product script; 1 = not; 2 = offline (no verdict).

import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "..", "software", "noknok_main.py"), encoding="utf-8").read()
m = re.search(r'^SCRIPT_URL\s*=\s*"([^"]+)"', src, re.M)
if not m:
    print("FAIL: SCRIPT_URL not found in software/noknok_main.py")
    sys.exit(1)
url = m.group(1)
print("fallback SCRIPT_URL:", url)
try:
    with urllib.request.urlopen(url, timeout=20) as r:
        code, body = r.status, r.read(4096).decode("utf-8", "replace")
except urllib.error.HTTPError as e:
    print("FAIL: HTTP %d" % e.code)
    sys.exit(1)
except OSError as e:
    print("OFFLINE: %s (no verdict)" % e)
    sys.exit(2)
ok = code == 200 and ("import" in body or "def " in body)
print("HTTP", code, "- looks like a script" if ok else "- does NOT look like a product script")
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
