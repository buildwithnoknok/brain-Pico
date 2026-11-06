# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# noknok_fetch.py - DEV-205: stream a module firmware image straight into the brain's
# firmware cache (/data/fw_<type>.bin + .json sidecar), 1 KB at a time, size and CRC32
# counted chunk by chunk and checked against the published index before the rename.
#
# Why a separate module: the old code read the whole image with `resp.content`, which
# needs ONE free block of the image size (MemoryError at ~16 KB on a booted brain). And
# the brain runs close to its RAM edge (DEV-207: a little more code loaded at every boot
# left the WiFi deaf), so this code is imported ONLY inside the daily OTA pass, which is
# always followed by the clean-heap reload (DEV-132) before the product starts. Normal
# boots load nothing extra.
#
# Used by code.py's OTA cache loop. Setup/OTA time only (filesystem write, DEV-18).

import json
import os
import noknok as nk

__version__ = "1.0.0"


def largest_free_block(limit=65536):
    """Size of the largest bytearray the heap can hand out right now (binary search,
    every probe freed again). Diagnostics only (DEV-205: a 16 KB `resp.content`
    failed on a fragmented heap although gc.mem_free() was far larger)."""
    import gc
    gc.collect()
    lo, hi = 0, min(limit, gc.mem_free())
    while lo < hi:
        mid = (lo + hi + 1) // 2
        try:
            b = bytearray(mid)
            del b
            lo = mid
        except MemoryError:
            hi = mid - 1
    return lo

def _crc32_update(data, crc=0):
    """Running zlib CRC32 (same value as module_flasher.crc32 over the whole image)."""
    try:
        import binascii
        return binascii.crc32(data, crc) & 0xFFFFFFFF
    except (ImportError, AttributeError, TypeError):
        crc ^= 0xFFFFFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = (crc >> 1) ^ 0xEDB88320 if (crc & 1) else (crc >> 1)
        return crc ^ 0xFFFFFFFF

FETCH_CHUNK = 1024

def fetch_to_cache(session, url, bin_path, meta_path, spec, timeout=30):
    """DEV-205: download one module image straight into the firmware cache, in
    FETCH_CHUNK pieces - never the whole image in RAM (`resp.content` needed one
    contiguous block of the image size and failed with MemoryError at ~16 KB on a
    booted brain). Size and CRC32 are counted chunk by chunk and checked against
    the index (`spec` size / crc32) BEFORE the file is renamed into the cache; on
    any failure the .tmp is removed and the existing cache entry (if any) stays
    as it was. The sidecar (version, layout, size, crc32) is written after the
    image, both atomic (DEV-18). Setup/OTA time only (filesystem write). Returns
    the byte count; raises with a clear reason."""
    import gc
    gc.collect()
    try:                                     # diagnostics only, never fails a fetch
        nk._print("[fw] fetch: mem_free %d, largest free block %d" % (gc.mem_free(), largest_free_block()))
    except Exception:
        pass
    tmp = bin_path + ".tmp"
    want_size = spec.get("size")
    want_crc = spec.get("crc32")
    if isinstance(want_crc, str):
        want_crc = int(want_crc, 16)
    resp = session.get(url, timeout=timeout)
    try:
        if resp.status_code != 200:
            raise ValueError("HTTP %d" % resp.status_code)
        n, crc = 0, 0
        nk.ensure_dir(bin_path)
        with nk.writable():
            try:
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=FETCH_CHUNK):
                        fh.write(chunk)
                        n += len(chunk)
                        crc = _crc32_update(chunk, crc)
                    fh.flush()
                if n < 256:          # a real app image is KBs; this size means an error
                    raise ValueError("image too small (%d bytes) - not a firmware binary" % n)
                if want_size is not None and n != int(want_size):
                    raise ValueError("image size %d != declared %d" % (n, int(want_size)))
                if want_crc is not None and crc != int(want_crc):
                    raise ValueError("image crc32 %08x != declared %08x" % (crc, int(want_crc)))
                try:
                    os.sync()
                except Exception:
                    pass
                os.rename(tmp, bin_path)            # replaces the old image
                nk.write_atomic(meta_path, json.dumps(
                    {"version": spec.get("version"), "layout": spec.get("layout"),
                     "size": n, "crc32": "%08x" % crc}))
            except BaseException:
                nk.remove(tmp)
                raise
        return n
    finally:
        try:
            resp.close()
        except Exception:
            pass


class CachedImage:
    """A cached module image read from the file page by page instead of held whole
    (DEV-205: `fh.read()` of a 16 KB cache file failed with MemoryError on a booted
    brain). Offers what ModuleFlasher.flash() uses: len(), slices, and the CRC32
    (`known_crc32`, checked chunk by chunk against the sidecar when opened)."""

    def __init__(self, path, size, crc):
        self.path = path
        self.size = size
        self.known_crc32 = crc
        self._fh = None

    def __len__(self):
        return self.size

    def __getitem__(self, sl):
        if not isinstance(sl, slice):
            raise TypeError("CachedImage supports slices only")
        start = sl.start or 0
        stop = self.size if sl.stop is None else min(sl.stop, self.size)
        if stop <= start:
            return b""
        if self._fh is None:
            self._fh = open(self.path, "rb")
        self._fh.seek(start)
        data = self._fh.read(stop - start)
        if stop >= self.size:            # last page read: let the file go
            self.close()
        return data

    def close(self):
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None


def open_cached(bin_path, meta):
    """CachedImage for a cache entry, after streaming the file once to check its size
    and CRC32 against the sidecar `meta` (a file half-written by a power cut is
    rejected, never flashed). Raises ValueError with a clear reason."""
    if not meta:
        raise ValueError("no cached image (no sidecar) for %s" % bin_path)
    n, crc = 0, 0
    try:
        with open(bin_path, "rb") as fh:
            while True:
                chunk = fh.read(FETCH_CHUNK)
                if not chunk:
                    break
                n += len(chunk)
                crc = _crc32_update(chunk, crc)
    except OSError:
        raise ValueError("no cached image at %s" % bin_path)
    want_size, want_crc = meta.get("size"), meta.get("crc32")
    if want_size is not None and n != int(want_size):
        raise ValueError("cached image size %d != sidecar %d" % (n, int(want_size)))
    if want_crc is not None:
        want = int(want_crc, 16) if isinstance(want_crc, str) else int(want_crc)
        if crc != want:
            raise ValueError("cached image crc32 %08x != sidecar %08x" % (crc, want))
    return CachedImage(bin_path, n, crc)
