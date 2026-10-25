#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# build_brain_uf2.py - DEV-38: the one-file brain image noknok-brain-<version>.uf2.
#
# Hold BOOTSEL, plug the Pico 2 W into a PC, drag this ONE file onto the RP2350 drive:
# the Pico comes back with CircuitPython AND the complete noknok filesystem (boot.py,
# code.py, settings.toml, the four libraries as .mpy, lib/). Same file for factory flash.
#
# How: the pinned, checksum-verified CircuitPython UF2 + a FAT filesystem image of the
# CIRCUITPY drive, built here (pure Python, no mkfs/mtools), placed at the flash address
# where CircuitPython keeps that drive, merged into one UF2 with one block sequence.
#
#   flash 0x10000000  CircuitPython firmware      (1536 KB on the Pico 2 W)
#         0x10180000  nvm, 4 KB = the noknok Store  NOT written (kept) unless --wipe-store
#         0x10181000  CIRCUITPY FAT volume         .. end of the 4 MB flash (2556 KB)
#
# Reproducible: same brain-Pico commit + same CircuitPython UF2 + same mpy-cross = the
# same bytes (fixed timestamps = the commit time, sorted files, no host paths in the .mpy).
# The manifest next to the UF2 lists every input and output checksum.
#
# Usage (repo root):
#   python -I tools/image/build_brain_uf2.py --cp-uf2 <adafruit-circuitpython-raspberry_pi_pico2_w-en_US-10.3.1.uf2>
#          --mpy-cross <mpy-cross-10.3.1(.exe)> [--out DIR] [--settings FILE] [--wipe-store]
#          [--allow-dirty]
# Output: DIR/noknok-brain-<code.py version>-cp<cp version>[-wipe].uf2 + .json manifest + .sha256
#
# Background and the test plan: tools/image/README.md.

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
SW = os.path.join(REPO, "software")

# ── Pinned inputs ──────────────────────────────────────────────────────────────
# The ONLY CircuitPython this image is built and tested for. md5 = Adafruit's S3 ETag
# (checked 25 Oct 2026); a CircuitPython upgrade = new pin + new mpy-cross + new lib/.
CP_VERSION = "10.3.1"
CP_BOARD = "raspberry_pi_pico2_w"
CP_UF2_SHA256 = "61d322a6740cf52ae7bdcfbc2361e412cdd267f27f55aaa15fcf6e4cb2d1aed1"
CP_UF2_MD5 = "7b975ef129b68d33888a8b887e36b75e"
CP_UF2_URL = ("https://adafruit-circuit-python.s3.amazonaws.com/bin/raspberry_pi_pico2_w/en_US/"
              "adafruit-circuitpython-raspberry_pi_pico2_w-en_US-10.3.1.uf2")
MPY_CROSS_VERSION = "CircuitPython 10.3.1"           # start of `mpy-cross --version`

# ── Flash layout of CircuitPython 10.3.1 on the Pico 2 W ────────────────────────
# ports/raspberrypi/mpconfigport.h + boards/raspberry_pi_pico2_w/mpconfigboard.mk @10.3.1:
# CIRCUITPY_FIRMWARE_SIZE = 1536 KB, CIRCUITPY_INTERNAL_NVM_SIZE = 4 KB,
# CIRCUITPY_CIRCUITPY_DRIVE_START_ADDR = firmware + nvm; flash W25Q32 = 4 MB.
XIP_BASE = 0x10000000
FLASH_SIZE = 4 * 1024 * 1024
NVM_OFFSET = 1536 * 1024
NVM_SIZE = 4 * 1024
FS_OFFSET = NVM_OFFSET + NVM_SIZE                     # 0x181000
FS_SIZE = FLASH_SIZE - FS_OFFSET                      # 2 617 344 bytes
SECTOR = 512                                          # CircuitPython FILESYSTEM_BLOCK_SIZE

# What a brain consists of: tools/restore/restore_brain.sh (DEV-84), the reference.
CODE_FILES = ("code.py", "boot.py")
LIBS = ("noknok", "noknok_rpc", "noknok_usb", "module_flasher")   # imported by code.py -> .mpy

UF2_MAGIC0, UF2_MAGIC1, UF2_MAGIC_END = 0x0A324655, 0x9E5D5157, 0x0AB16F30
UF2_FLAG_FAMILY = 0x00002000


def sha256(b):
    return hashlib.sha256(b).hexdigest()


# ── FAT16 image (what CircuitPython's own f_mkfs would choose for 2556 KB) ──────
# One FAT, 512 root entries, 1 sector per cluster -> ~5060 clusters = FAT16. No
# partition table ("super floppy"): FatFs, Windows, macOS and Linux all mount it.
class Fat16:
    RESERVED = 1
    N_FATS = 1
    ROOT_ENTRIES = 512
    SPC = 1

    def __init__(self, size, label, stamp, volume_id):
        self.total = size // SECTOR
        self.root_secs = self.ROOT_ENTRIES * 32 // SECTOR
        fat = 1
        while True:                                   # smallest FAT that covers all clusters
            clusters = (self.total - self.RESERVED - self.N_FATS * fat - self.root_secs) // self.SPC
            if (clusters + 2) * 2 <= fat * SECTOR:
                break
            fat += 1
        self.fat_secs, self.clusters = fat, clusters
        assert 4085 <= clusters < 65525, "not a FAT16 cluster count: %d" % clusters
        self.fat_start = self.RESERVED
        self.root_start = self.fat_start + self.N_FATS * fat
        self.data_start = self.root_start + self.root_secs
        self.img = bytearray(size)
        self.fat = [0] * (clusters + 2)
        self.fat[0], self.fat[1] = 0xFFF8, 0xFFFF
        self.next_free = 2
        self.label = label
        self.date, self.time = stamp
        self.volume_id = volume_id
        self.root = []                                # raw 32-byte entries of the root dir

    # -- clusters
    def alloc(self, n):
        n = max(1, n)
        first = self.next_free
        if first + n - 1 > self.clusters + 1:
            raise RuntimeError("filesystem image full")
        for i in range(n):
            c = first + i
            self.fat[c] = c + 1 if i < n - 1 else 0xFFFF
        self.next_free += n
        return first

    def write_clusters(self, first, data):
        off = (self.data_start + (first - 2) * self.SPC) * SECTOR
        self.img[off:off + len(data)] = data

    # -- directory entries (always LFN + a unique short alias)
    @staticmethod
    def _short(name, taken):
        base, _, ext = name.upper().rpartition(".") if "." in name[1:] else (name.upper(), "", "")
        ok = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789$%'-_@~`!(){}^#&"
        base = "".join(ch for ch in base if ch in ok) or "X"
        ext = "".join(ch for ch in ext if ch in ok)[:3]
        n = 1
        while True:
            tail = "~%d" % n
            s = (base[:8 - len(tail)] + tail).ljust(8) + ext.ljust(3)
            if s not in taken:
                taken.add(s)
                return s.encode("ascii")
            n += 1

    def _entries(self, name, attr, cluster, size, taken):
        short = self._short(name, taken)
        csum = 0
        for b in short:
            csum = (((csum & 1) << 7) + (csum >> 1) + b) & 0xFF
        units = [ord(c) for c in name]
        if len(units) % 13:
            units += [0x0000] + [0xFFFF] * (12 - len(units) % 13)
        parts = [units[i:i + 13] for i in range(0, len(units), 13)]
        out = []
        for seq in range(len(parts), 0, -1):          # LFN entries come last-part-first
            u = parts[seq - 1]
            order = seq | (0x40 if seq == len(parts) else 0)
            e = struct.pack("<B10sBBB12sH4s", order,
                            struct.pack("<5H", *u[0:5]), 0x0F, 0, csum,
                            struct.pack("<6H", *u[5:11]), 0, struct.pack("<2H", *u[11:13]))
            out.append(e)
        out.append(struct.pack("<11sBBBHHHHHHHI", short, attr, 0, 0, self.time, self.date,
                               self.date, 0, self.time, self.date, cluster, size))
        return out

    def add_tree(self, tree):
        """tree: {name: bytes | dict}. Builds everything below the root."""
        self.root = self._dir_entries(tree, parent=0, is_root=True)

    def _dir_entries(self, tree, parent, is_root=False, me=0):
        taken, entries = set(), []
        for name in sorted(tree, key=lambda s: s.lower()):
            node = tree[name]
            if isinstance(node, dict):
                # size the directory first: "." + ".." + entries of the children
                n_child = 2 + sum(len(self._entries(k, 0, 0, 0, set())) for k in node)
                secs = -(-n_child * 32 // SECTOR)
                c = self.alloc(-(-secs // self.SPC))
                child = self._dir_entries(node, parent=0 if is_root else me, me=c)
                dot = struct.pack("<11sBBBHHHHHHHI", b".          ", 0x10, 0, 0, self.time, self.date,
                                  self.date, 0, self.time, self.date, c, 0)
                dotdot = struct.pack("<11sBBBHHHHHHHI", b"..         ", 0x10, 0, 0, self.time, self.date,
                                     self.date, 0, self.time, self.date, 0 if is_root else me, 0)
                self.write_clusters(c, dot + dotdot + b"".join(child))
                entries += self._entries(name, 0x10, c, 0, taken)
            else:
                data = bytes(node)
                c = self.alloc(-(-len(data) // (SECTOR * self.SPC))) if data else 0
                if data:
                    self.write_clusters(c, data)
                entries += self._entries(name, 0x20, c, len(data), taken)
        return entries

    def image(self):
        boot = bytearray(SECTOR)
        struct.pack_into("<3s8sHBHBHHBHHHII", boot, 0, b"\xEB\x3C\x90", b"noknok  ", SECTOR,
                         self.SPC, self.RESERVED, self.N_FATS, self.ROOT_ENTRIES, self.total,
                         0xF8, self.fat_secs, 63, 255, 0, 0)
        struct.pack_into("<BBBI11s8s", boot, 36, 0x80, 0, 0x29, self.volume_id,
                         self.label.ljust(11).encode("ascii"), b"FAT16   ")
        boot[510:512] = b"\x55\xAA"
        self.img[0:SECTOR] = boot
        fat = struct.pack("<%dH" % len(self.fat), *self.fat)
        for i in range(self.N_FATS):
            off = (self.fat_start + i * self.fat_secs) * SECTOR
            self.img[off:off + len(fat)] = fat
        vol = struct.pack("<11sBBBHHHHHHHI", self.label.ljust(11).encode("ascii"), 0x08, 0, 0,
                          self.time, self.date, self.date, 0, self.time, self.date, 0, 0)
        root = vol + b"".join(self.root)
        if len(root) > self.ROOT_ENTRIES * 32:
            raise RuntimeError("too many root entries")
        off = self.root_start * SECTOR
        self.img[off:off + len(root)] = root
        return bytes(self.img)

    def used_bytes(self):
        """Bytes from the start of the volume to the end of the last used cluster."""
        return (self.data_start + (self.next_free - 2) * self.SPC) * SECTOR


def fat_stamp(epoch):
    t = time.gmtime(epoch)
    date = ((t.tm_year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday
    tm = (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2)
    return date, tm


# ── UF2 ───────────────────────────────────────────────────────────────────────
def uf2_parse(data):
    blocks = []
    for i in range(len(data) // 512):
        b = data[i * 512:(i + 1) * 512]
        m0, m1, flags, addr, size, _bno, _nbl, fam = struct.unpack("<8I", b[:32])
        if m0 != UF2_MAGIC0 or m1 != UF2_MAGIC1 or struct.unpack("<I", b[508:512])[0] != UF2_MAGIC_END:
            raise ValueError("not a UF2 block at %d" % i)
        blocks.append((flags, addr, bytes(b[32:32 + size]), fam))
    return blocks


def uf2_write(blocks):
    n = len(blocks)
    out = bytearray()
    for i, (flags, addr, payload, fam) in enumerate(blocks):
        hdr = struct.pack("<8I", UF2_MAGIC0, UF2_MAGIC1, flags, addr, len(payload), i, n, fam)
        out += hdr + payload + b"\x00" * (476 - len(payload)) + struct.pack("<I", UF2_MAGIC_END)
    return bytes(out)


def data_blocks(base, data, fam, chunk=256):
    return [(UF2_FLAG_FAMILY, base + o, data[o:o + chunk], fam) for o in range(0, len(data), chunk)]


# ── build ─────────────────────────────────────────────────────────────────────
def git(*args):
    return subprocess.run(["git", "-C", REPO] + list(args), capture_output=True, text=True).stdout.strip()


def code_version():
    for line in open(os.path.join(SW, "code.py"), encoding="utf-8"):
        if line.startswith("CODE_VERSION"):
            return line.split("=")[1].strip().strip('"\'')
    raise RuntimeError("CODE_VERSION not found in code.py")


def compile_mpy(mpy_cross, lib, tmp):
    out = os.path.join(tmp, lib + ".mpy")
    # cwd = software/ and a bare source name: no host path inside the .mpy (reproducible,
    # and tracebacks on the brain say "noknok.py", not "C:/Users/.../noknok.py").
    r = subprocess.run([mpy_cross, "-O1", "-s", lib + ".py", "-o", out, lib + ".py"],
                       cwd=SW, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("mpy-cross failed on %s: %s" % (lib, r.stderr.strip()))
    return open(out, "rb").read()


def collect(mpy_cross, settings_path):
    tree, files = {}, []

    def put(path, data):
        node = tree
        parts = path.split("/")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = data
        files.append({"path": path, "size": len(data), "sha256": sha256(data)})

    for f in CODE_FILES:
        put(f, open(os.path.join(SW, f), "rb").read())
    put("settings.toml", open(settings_path, "rb").read())
    with tempfile.TemporaryDirectory() as tmp:
        for lib in LIBS:
            put(lib + ".mpy", compile_mpy(mpy_cross, lib, tmp))
    libdir = os.path.join(SW, "lib")
    for dp, dns, fns in os.walk(libdir):
        dns.sort()
        for fn in sorted(fns):
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, SW).replace(os.sep, "/")
            put(rel, open(full, "rb").read())
    # what CircuitPython itself creates on a fresh drive: stop macOS indexing it
    put(".fseventsd/no_log", b"")
    files.sort(key=lambda f: f["path"])
    return tree, files


def build(cp_uf2, mpy_cross, out_dir, settings_path=None, wipe_store=False, allow_dirty=False,
          check_pins=True):
    settings_path = settings_path or os.path.join(SW, "settings.toml")
    cp = open(cp_uf2, "rb").read()
    if check_pins and sha256(cp) != CP_UF2_SHA256:
        raise SystemExit("CircuitPython UF2 checksum mismatch: expected the pinned %s %s (%s),\n"
                         "got sha256 %s. Download: %s" % (CP_BOARD, CP_VERSION, CP_UF2_SHA256,
                                                         sha256(cp), CP_UF2_URL))
    ver = subprocess.run([mpy_cross, "--version"], capture_output=True, text=True).stdout.strip()
    if check_pins and not ver.startswith(MPY_CROSS_VERSION):
        raise SystemExit("mpy-cross must be %s (matches the pinned CircuitPython), got: %s"
                         % (MPY_CROSS_VERSION, ver))
    commit = git("rev-parse", "HEAD") or "unknown"
    dirty = bool(git("status", "--porcelain", "--", "software", "tools/image"))
    if dirty and not allow_dirty:
        raise SystemExit("software/ or tools/image/ has uncommitted changes - commit first "
                         "(a release image must come from a commit), or pass --allow-dirty for a test build")
    epoch = int(git("log", "-1", "--format=%ct") or "1767225600")   # commit time; fallback 2026-01-01

    cp_blocks = uf2_parse(cp)
    fams = {b[3] for b in cp_blocks}
    if len(fams) != 1:
        raise SystemExit("CircuitPython UF2 has %d families - expected one" % len(fams))
    fam = fams.pop()
    cp_end = max(a + len(p) for _f, a, p, _fam in cp_blocks)
    if cp_end > XIP_BASE + NVM_OFFSET or min(a for _f, a, _p, _fam in cp_blocks) < XIP_BASE:
        raise SystemExit("CircuitPython UF2 does not fit below the nvm sector (ends 0x%08X)" % cp_end)

    tree, files = collect(mpy_cross, settings_path)
    vol_id = int(sha256(json.dumps(files, sort_keys=True).encode())[:8], 16)
    fs = Fat16(FS_SIZE, "CIRCUITPY", fat_stamp(epoch), vol_id)
    fs.add_tree(tree)
    img = fs.image()
    # Write the volume up to the end of the last used cluster; the free clusters after it
    # are left as they are on the flash (the FAT marks them free, contents never read).
    used = -(-fs.used_bytes() // 4096) * 4096
    blocks = list(cp_blocks)
    if wipe_store:
        blocks += data_blocks(XIP_BASE + NVM_OFFSET, b"\xFF" * NVM_SIZE, fam)
    blocks += data_blocks(XIP_BASE + FS_OFFSET, img[:used], fam)
    uf2 = uf2_write(blocks)

    name = "noknok-brain-%s-cp%s%s" % (code_version(), CP_VERSION, "-wipe" if wipe_store else "")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name + ".uf2")
    open(path, "wb").write(uf2)
    manifest = {
        "image": name + ".uf2", "sha256": sha256(uf2), "size": len(uf2), "uf2_blocks": len(blocks),
        "code_version": code_version(), "brain_pico_commit": commit, "dirty": dirty,
        "built_from_commit_time": epoch,
        "circuitpython": {"version": CP_VERSION, "board": CP_BOARD, "uf2_sha256": sha256(cp),
                          "uf2_md5": hashlib.md5(cp).hexdigest(), "family": "0x%08x" % fam,
                          "url": CP_UF2_URL},
        "mpy_cross": ver,
        "layout": {"nvm": "0x%08x" % (XIP_BASE + NVM_OFFSET), "nvm_written": wipe_store,
                   "fs": "0x%08x" % (XIP_BASE + FS_OFFSET), "fs_size": FS_SIZE,
                   "fs_bytes_written": used, "fat": "FAT16, 1 FAT, %d clusters of %d B"
                   % (fs.clusters, SECTOR * fs.SPC), "label": "CIRCUITPY"},
        "fs_image_sha256": sha256(img),
        "files": files,
    }
    open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8", newline="\n").write(
        json.dumps(manifest, indent=2) + "\n")
    open(os.path.join(out_dir, name + ".uf2.sha256"), "w", newline="\n").write(
        "%s  %s\n" % (manifest["sha256"], name + ".uf2"))
    return path, manifest, img


def main():
    ap = argparse.ArgumentParser(description="Build the one-file brain image noknok-brain-<version>.uf2 (DEV-38).")
    ap.add_argument("--cp-uf2", required=True, help="the pinned CircuitPython UF2 for the Pico 2 W")
    ap.add_argument("--mpy-cross", required=True, help="mpy-cross matching that CircuitPython")
    ap.add_argument("--out", default=os.path.join(REPO, "build"), help="output dir (default: build/)")
    ap.add_argument("--settings", help="settings.toml to ship (default: software/settings.toml)")
    ap.add_argument("--wipe-store", action="store_true",
                    help="also erase the nvm sector (Store): true factory state; default keeps it")
    ap.add_argument("--allow-dirty", action="store_true", help="test build from uncommitted files")
    a = ap.parse_args()
    path, m, _ = build(a.cp_uf2, a.mpy_cross, a.out, a.settings, a.wipe_store, a.allow_dirty)
    print("wrote %s  (%d KB, %d blocks)" % (path, m["size"] // 1024, m["uf2_blocks"]))
    print("sha256 %s" % m["sha256"])
    print("from brain-Pico %s%s, code.py %s, CircuitPython %s, %s"
          % (m["brain_pico_commit"][:7], " (DIRTY)" if m["dirty"] else "", m["code_version"],
             CP_VERSION, m["mpy_cross"]))
    print("filesystem: %d files, %s, %d KB written%s"
          % (len(m["files"]), m["layout"]["fat"], m["layout"]["fs_bytes_written"] // 1024,
             ", Store erased" if a.wipe_store else ", Store (nvm) kept"))


if __name__ == "__main__":
    main()
