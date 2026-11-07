# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_wifi_store_copy.py - DEV-196, tier S (host Python, no hardware).
#
# The non-wipe brain image rewrites the filesystem; the brain then rejoins its WiFi from the
# Store copy. A brain set up before DEV-18 (15 Sep) or whose Store record was lost had only
# /data/wifi.json and NO Store copy, so the image dropped it into setup mode.
# load_wifi_credentials() now back-fills the copy from a good wifi.json:
#   - file present, Store has no copy  -> copy written once, file creds returned
#   - file present, Store copy equal   -> NO Store write (boot cost zero)
#   - file present, Store copy differs -> Store follows the file (the one in use)
#   - file missing, Store copy present -> restored from the Store (unchanged behaviour)
#   - neither                          -> None, no write
#
# Run from the repo root:   python -I tools/test_wifi_store_copy.py

import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SW = os.path.join(HERE, "..", "software")
sys.path.insert(0, SW)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class _Any:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Any()
    def __getattr__(self, n): return _Any()


class Response:
    def __init__(self, body, length=None, status=200, fail_after=None):
        self.status_code = status
        self.body = body
        self.headers = {} if length is None else {"content-length": str(length)}
        self.fail_after = fail_after
        self.closed = False

    @property
    def text(self):
        raise AssertionError("DEV-139: response.text must not be used")

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.body), chunk_size):
            if self.fail_after is not None and i >= self.fail_after:
                raise OSError("ETIMEDOUT")
            yield self.body[i:i + chunk_size]

    def close(self):
        self.closed = True


class Session:
    queue = []

    def __init__(self, *a, **k): pass

    def get(self, url, timeout=None):
        return Session.queue.pop(0)


for name in ("busio", "board", "digitalio", "socketpool", "ssl"):
    stub(name)
stub("adafruit_requests", Session=Session)
stub("microcontroller", nvm=bytearray(4096))
stub("supervisor", reload=lambda: None, runtime=_Any())
stub("wifi", radio=_Any())
stub("adafruit_connection_manager", get_radio_socketpool=lambda r: _Any(),
     get_radio_ssl_context=lambda r: _Any())
stub("adafruit_httpserver", Server=_Any, Request=_Any, Response=_Any, POST="POST")

src = open(os.path.join(SW, "code.py"), encoding="utf-8").read().rstrip()
assert src.endswith("main()"), "code.py no longer ends with main() - update this test"
ns = {"__name__": "code_under_test"}
exec(compile(src[: -len("main()")], "code.py", "exec"), ns)
logs = []
ns["log"] = lambda msg: logs.append(msg)
ns["time"] = types.SimpleNamespace(sleep=lambda s: None, monotonic=lambda: 0.0)


class FakeStore:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.writes = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):          # same contract as noknok.Store.set: equal -> no write
        if key in self.data and self.data[key] == value:
            return True
        self.data[key] = value
        self.writes += 1
        return True


files = {}
written = []
nk = ns["nk"]
nk.read_json = lambda path, default=None: files.get(path, default)
nk.write_json_atomic = lambda path, obj: (written.append(path), files.__setitem__(path, obj)) and True
fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-60s %s %s" % (name, "PASS" if cond else "FAIL", "" if cond else extra))
    if not cond:
        fails += 1


WIFI = ns["WIFI_CREDENTIALS_FILE"]
KEY = ns["WIFI_STORE_KEY"]
CREDS = {"ssid": "home", "password": "pw", "script_url": "https://x/p.py"}


def run(file_creds, store_creds):
    files.clear(); written.clear(); logs.clear()
    if file_creds is not None:
        files[WIFI] = dict(file_creds)
    st = FakeStore({KEY: dict(store_creds)} if store_creds is not None else {})
    nk.store = lambda: st
    return ns["load_wifi_credentials"](), st


print("back-fill")
got, st = run(CREDS, None)
check("file + no Store copy: copy written once", got == CREDS and st.data.get(KEY) == CREDS and st.writes == 1)
got, st2 = run(CREDS, CREDS)
check("file + equal Store copy: no Store write", got == CREDS and st2.writes == 0)
other = dict(CREDS, ssid="old")
got, st3 = run(CREDS, other)
check("file + different Store copy: Store follows the file", got == CREDS and st3.data[KEY] == CREDS and st3.writes == 1)
print("restore (unchanged)")
got, st4 = run(None, CREDS)
check("no file + Store copy: restored, file rewritten", got == CREDS and WIFI in written
      and any("restored from the Store copy" in l for l in logs), repr(logs))
got, st5 = run(None, None)
check("neither: None, no write", got is None and st5.writes == 0 and not written)

print()
print("%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
