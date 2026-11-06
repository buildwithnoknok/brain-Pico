# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_fetch_to_cache.py - DEV-205, tier S (host Python, no hardware).
#
# noknok_fetch.fetch_to_cache() streams a module image into the firmware cache in 1 KB pieces
# instead of `resp.content` (one contiguous block of the image size - MemoryError at
# ~16 KB on a booted brain):
#   - success: image + sidecar written, CRC32/size counted chunk by chunk, no .tmp left,
#     resp.content never touched, response closed
#   - 24 KB image (bigger than any today) works the same way
#   - wrong CRC32 / wrong size / truncated body / HTTP error / tiny body: raises,
#     NO new cache entry, the old cache entry (image + sidecar) untouched, .tmp removed
#   - code.py imports noknok_fetch only inside the OTA cache loop (nothing extra on a
#     normal boot - DEV-207 RAM edge) and no longer reads resp.content
#
# Run from the repo root:   python -I tools/test_fetch_to_cache.py

import json
import os
import sys
import tempfile
import types
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SW = os.path.join(HERE, "..", "software")
sys.path.insert(0, SW)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

for name in ("busio", "board", "digitalio", "supervisor"):
    sys.modules[name] = types.ModuleType(name)
mc = types.ModuleType("microcontroller")
mc.nvm = bytearray(4096)
sys.modules["microcontroller"] = mc

import noknok   # noqa: E402
import noknok_fetch   # noqa: E402

os.rename = os.replace          # CircuitPython's rename replaces the target; Windows' does not


class _Writable:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


noknok.writable = _Writable
noknok.ensure_dir = lambda p: None
noknok._print = lambda *a, **k: None


class Response:
    def __init__(self, body, status=200, fail_after=None):
        self.status_code = status
        self.body = body
        self.fail_after = fail_after
        self.closed = False
        self.chunks = 0

    @property
    def content(self):
        raise AssertionError("DEV-205: resp.content must not be used")

    def iter_content(self, chunk_size=1):
        assert chunk_size <= 1024, chunk_size
        for i in range(0, len(self.body), chunk_size):
            if self.fail_after is not None and i >= self.fail_after:
                raise OSError(116, "ETIMEDOUT")
            self.chunks += 1
            yield self.body[i:i + chunk_size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, resp):
        self.resp = resp

    def get(self, url, timeout=None):
        return self.resp


fails = 0
def check(name, cond, extra=""):
    global fails
    print("  %-64s %s %s" % (name, "PASS" if cond else "FAIL", "" if cond else extra))
    if not cond:
        fails += 1


d = tempfile.mkdtemp()
BIN, META = os.path.join(d, "fw_picohub.bin"), os.path.join(d, "fw_picohub.json")


def spec_for(img, **over):
    s = {"version": "0.3.1", "layout": 3, "size": len(img), "crc32": "%08x" % (zlib.crc32(img) & 0xFFFFFFFF)}
    s.update(over)
    return s


def run(resp, spec):
    try:
        return noknok_fetch.fetch_to_cache(Session(resp), "https://x/fw.bin", BIN, META, spec), None
    except Exception as e:
        return None, e


def cache():
    try:
        return open(BIN, "rb").read(), json.load(open(META))
    except OSError:
        return None, None


print("success")
img = os.urandom(16768)
r = Response(img)
n, err = run(r, spec_for(img))
got, meta = cache()
check("16768 B image cached, byte for byte", err is None and n == 16768 and got == img, repr(err))
check("...sidecar: version, layout, size, crc32", meta == spec_for(img), repr(meta))
check("...streamed in 1 KB chunks (17 chunks), response closed", r.chunks == 17 and r.closed, r.chunks)
check("...no .tmp left", not os.path.exists(BIN + ".tmp"))

print("24 KB image (bigger than any today)")
big = os.urandom(24 * 1024)
n, err = run(Response(big), spec_for(big, version="9.9.9"))
got, meta = cache()
check("24576 B cached", err is None and got == big and meta["size"] == 24576, repr(err))
good_img, good_meta = got, meta

print("failures leave the old cache entry untouched")
for name, resp, spec in (
        ("wrong crc32", Response(img), spec_for(img, crc32="deadbeef")),
        ("wrong size", Response(img), spec_for(img, size=len(img) + 1)),
        ("truncated body (ETIMEDOUT at 8 KB)", Response(img, fail_after=8192), spec_for(img)),
        ("HTTP 404", Response(b"nope", status=404), spec_for(img)),
        ("tiny body", Response(b"x" * 100), spec_for(b"x" * 100))):
    r = resp
    n, err = run(r, spec)
    got, meta = cache()
    check("%s: raises" % name, err is not None, repr(n))
    check("%s: old image + sidecar unchanged, no .tmp" % name,
          got == good_img and meta == good_meta and not os.path.exists(BIN + ".tmp"))
    check("%s: response closed" % name, r.closed)

print("no cache before a failed first download")
os.remove(BIN); os.remove(META)
n, err = run(Response(img, fail_after=4096), spec_for(img))
check("partial first download leaves NO cache entry", err is not None and cache() == (None, None)
      and not os.path.exists(BIN + ".tmp"))

print("code.py uses it")
src = open(os.path.join(SW, "code.py"), encoding="utf-8").read()
top = [l for l in src.splitlines() if l.startswith(("import noknok_fetch", "from noknok_fetch"))]
check("OTA cache loop imports noknok_fetch only there and calls it",
      src.count("from noknok_fetch import fetch_to_cache") == 1 and not top
      and "fetch_to_cache(session, url, *_cache_paths(mtype), spec)" in src, repr(top))
check("no resp.content left in code.py", "resp.content" not in src and ".content\n" not in src)

print("flashing from the cache file, page by page")
import module_flasher   # noqa: E402
img = os.urandom(16768)
n, err = run(Response(img), spec_for(img))
meta = json.load(open(META))
ci = noknok_fetch.open_cached(BIN, meta)
pages = b"".join(ci[o:o + module_flasher.PAGE] for o in range(0, len(ci), module_flasher.PAGE))
check("CachedImage: len + 64 B page slices give the exact image", len(ci) == 16768 and pages == img)
check("...file closed after the last page", ci._fh is None)
check("...module_flasher.crc32 uses the checked value (no whole-image read)",
      module_flasher.crc32(ci) == (zlib.crc32(img) & 0xFFFFFFFF))
data = bytearray(open(BIN, "rb").read()); data[100] ^= 0xFF
open(BIN, "wb").write(bytes(data))
try:
    noknok_fetch.open_cached(BIN, meta); bad = None
except ValueError as e:
    bad = e
check("corrupted cache file rejected (crc32 vs sidecar)", bad is not None and "crc32" in str(bad), repr(bad))
try:
    noknok_fetch.open_cached(BIN, {}); bad = None
except ValueError as e:
    bad = e
check("no sidecar: rejected", bad is not None, repr(bad))
src_cp = open(os.path.join(SW, "code.py"), encoding="utf-8").read()
check("code.py get_image flashes I2C modules from the file",
      "from noknok_fetch import open_cached" in src_cp and 'entry.get("bus") == "i2c"' in src_cp)

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
