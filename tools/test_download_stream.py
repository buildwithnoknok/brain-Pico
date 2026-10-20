# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_download_stream.py - DEV-139, tier S (host Python, no hardware).
#
# download_and_save_script() streams product.py to <file>.tmp in 1 KB pieces
# instead of holding the whole script twice (response.text needed 2 x 36 KB in
# one block each and failed after setup with a fragmented heap):
#   - success: file written, renamed over product.py, never read via .text
#   - truncated body (fewer bytes than Content-Length): retried, nothing saved
#   - syntax error: refused, old product.py kept, tmp removed
#   - MemoryError in the compile check: logged, file still saved
#   - read-only filesystem: no retry, message
#
# Run from the repo root:   python -I tools/test_download_stream.py

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


class _Writable:
    readonly = False

    def __enter__(self):
        if _Writable.readonly:
            raise OSError(30, "Read-only filesystem")
        return self

    def __exit__(self, *a):
        return False


os.rename = os.replace      # CircuitPython's rename replaces the target; Windows' does not
ns["nk"].writable = _Writable
ns["nk"].ensure_dir = lambda p: None

d = tempfile.mkdtemp()
PRODUCT = os.path.join(d, "product.py")
ns["PRODUCT_SCRIPT_FILE"] = PRODUCT
GOOD = ("x = 1\n" + "# filler line for size\n" * 2000).encode()   # ~46 KB
fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-62s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails += 1


def run(*responses):
    Session.queue = list(responses)
    logs.clear()
    return ns["download_and_save_script"]("https://x/p.py")


print("streamed download (DEV-139)")
r = Response(GOOD, len(GOOD))
ok = run(r)
check("success: True, file saved with the right size",
      ok and os.path.getsize(PRODUCT) == len(GOOD))
check("  ...response closed, no .tmp left", r.closed and not os.path.exists(PRODUCT + ".tmp"))
check("  ...never via response.text (would raise)", ok)

with open(PRODUCT, "wb") as f:
    f.write(b"old = 1\n")
ok = run(Response(GOOD, len(GOOD) + 500), Response(GOOD, len(GOOD) + 500),
         Response(GOOD, len(GOOD) + 500))
check("truncated x3: False, old product.py kept",
      ok is False and open(PRODUCT, "rb").read() == b"old = 1\n")
check("  ...each attempt logged as truncated", sum("truncated" in m for m in logs) == 3, logs[-2:])
ok = run(Response(GOOD, len(GOOD) + 500), Response(GOOD, len(GOOD)))
check("truncated once, then complete: True (retry works)", ok and os.path.getsize(PRODUCT) == len(GOOD))

ok = run(Response(GOOD, len(GOOD), fail_after=4096), Response(GOOD, len(GOOD)))
check("connection drops mid-body: retried, then saved", ok and os.path.getsize(PRODUCT) == len(GOOD))

with open(PRODUCT, "wb") as f:
    f.write(b"old = 1\n")
BAD = b"def broken(:\n"
ok = run(Response(BAD, len(BAD)))
check("syntax error: refused, old product.py kept, tmp removed",
      ok is False and open(PRODUCT, "rb").read() == b"old = 1\n"
      and not os.path.exists(PRODUCT + ".tmp"))

real_compile = ns["__builtins__"]["compile"] if isinstance(ns["__builtins__"], dict) else compile
def _oom(*a, **k):
    raise MemoryError("memory allocation failed, allocating 36027 bytes")
ns["compile"] = _oom
ok = run(Response(GOOD, len(GOOD)))
check("MemoryError in the compile check: still saved, logged",
      ok and os.path.getsize(PRODUCT) == len(GOOD) and any("compile check skipped" in m for m in logs))
del ns["compile"]

ok = run(Response(GOOD))          # no Content-Length header
check("no Content-Length: saved (compile check guards it)", ok and os.path.getsize(PRODUCT) == len(GOOD))

_Writable.readonly = True
ok = run(Response(GOOD, len(GOOD)))
_Writable.readonly = False
check("read-only filesystem: False at once, message",
      ok is False and any("read-only" in m for m in logs) and len(Session.queue) == 0)

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
