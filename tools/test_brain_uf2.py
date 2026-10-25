# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_brain_uf2.py - DEV-38, tier S (host Python, no hardware).
#
# Builds the one-file brain image twice with tools/image/build_brain_uf2.py and checks:
#   reproducible - both builds are byte-identical (same commit + same inputs)
#   uf2          - every block valid, numbered 0..N-1 of N, one family (CircuitPython's),
#                  256-byte payloads; the CircuitPython blocks are exactly the pinned UF2;
#                  nothing in the nvm sector (Store) unless --wipe-store, which writes it 0xFF;
#                  the filesystem starts at 0x10181000 and stays inside the 4 MB flash
#   filesystem   - reassembled FROM THE UF2 and read with a separate FAT16 + LFN reader:
#                  label CIRCUITPY, FAT16, every repo file byte-identical (code.py, boot.py,
#                  settings.toml, lib/), the four libraries as .mpy v6 with no host path inside
#   restore list - the image holds exactly what tools/restore/restore_brain.sh installs
# Writes the filesystem image to $TEMP/noknok-brain-fs.img for an independent check on Linux
# (fsck.fat -n, mount -o loop,ro) - see tools/image/README.md.
#
# Needs the pinned inputs (else SKIPPED, exit 0):
#   CP_UF2=<adafruit-circuitpython-raspberry_pi_pico2_w-en_US-10.3.1.uf2>
#   MPY_CROSS=<mpy-cross 10.3.1>
# Defaults: ~/Downloads/<that UF2>, ~/noknok/tools/mpy-cross/mpy-cross-10.3.1(.exe)
# Run from the repo root:   python -I tools/test_brain_uf2.py

import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "image"))
import build_brain_uf2 as B   # noqa: E402

home = os.path.expanduser("~")
CP = os.environ.get("CP_UF2") or os.path.join(
    home, "Downloads", "adafruit-circuitpython-raspberry_pi_pico2_w-en_US-10.3.1.uf2")
MX = os.environ.get("MPY_CROSS") or next(
    (p for p in (os.path.join(home, "noknok", "tools", "mpy-cross", n)
                 for n in ("mpy-cross-10.3.1.exe", "mpy-cross-10.3.1", "mpy-cross")) if os.path.exists(p)), "")
if not (os.path.exists(CP) and MX):
    print("SKIPPED: pinned CircuitPython UF2 or mpy-cross not found (CP_UF2 / MPY_CROSS)")
    sys.exit(0)

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-66s %s %s" % (name, "PASS" if cond else "FAIL", extra if not cond else ""))
    if not cond:
        fails += 1


# ── a FAT16 reader written separately from the builder ─────────────────────────
def read_fat16(img):
    bps, spc, rsv, nfats, nroot, tot16, _media, fatsz = struct.unpack_from("<HBHBHHBH", img, 11)
    label = img[43:54].decode("ascii").rstrip()
    fstype = img[54:62].decode("ascii").rstrip()
    root_start = (rsv + nfats * fatsz) * bps
    data_start = root_start + nroot * 32
    fat = img[rsv * bps:(rsv + fatsz) * bps]
    clus = spc * bps

    def chain(c):
        out = []
        while 2 <= c < 0xFFF8:
            out.append(c)
            if len(out) > 70000:
                raise ValueError("cluster loop")
            c = struct.unpack_from("<H", fat, c * 2)[0]
        return out

    def read_chain(c):
        return b"".join(img[data_start + (x - 2) * clus:data_start + (x - 1) * clus] for x in chain(c))

    def parse_dir(raw):
        lfn, out = {}, []
        for i in range(0, len(raw), 32):
            e = raw[i:i + 32]
            if e[0] == 0:
                break
            if e[0] == 0xE5:
                continue
            if e[11] == 0x0F:
                seq = e[0] & 0x1F
                u = struct.unpack("<5H", e[1:11]) + struct.unpack("<6H", e[14:26]) + struct.unpack("<2H", e[28:32])
                lfn[seq] = "".join(chr(x) for x in u if x not in (0, 0xFFFF))
                continue
            attr = e[11]
            if attr & 0x08:
                lfn = {}
                continue
            short = e[0:11].decode("ascii")
            name = "".join(lfn[k] for k in sorted(lfn)) if lfn else short.strip()
            lfn = {}
            if short in (".          ", "..         "):
                continue
            c, size = struct.unpack_from("<H", e, 26)[0], struct.unpack_from("<I", e, 28)[0]
            out.append((name, attr, c, size))
        return out

    files = {}

    def walk(raw, prefix):
        for name, attr, c, size in parse_dir(raw):
            path = prefix + name
            if attr & 0x10:
                walk(read_chain(c), path + "/")
            else:
                files[path] = read_chain(c)[:size] if size else b""

    walk(img[root_start:data_start], "")
    clusters = (tot16 * bps - data_start) // clus
    return {"label": label, "fstype": fstype, "clusters": clusters, "files": files}


# ── build twice ───────────────────────────────────────────────────────────────
print("DEV-38: one-file brain image")
with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2, \
        tempfile.TemporaryDirectory() as t3:
    p1, m1, img1 = B.build(CP, MX, t1, allow_dirty=True)
    p2, m2, _ = B.build(CP, MX, t2, allow_dirty=True)
    pw, mw, _ = B.build(CP, MX, t3, allow_dirty=True, wipe_store=True)
    u1, u2, uw = open(p1, "rb").read(), open(p2, "rb").read(), open(pw, "rb").read()
    names = (os.path.basename(p1), os.path.basename(pw))
check("reproducible: two builds byte-identical", u1 == u2, (m1["sha256"], m2["sha256"]))
check("name carries code.py + CircuitPython version", names[0] == "noknok-brain-%s-cp%s.uf2"
      % (B.code_version(), B.CP_VERSION) and names[1].endswith("-wipe.uf2"), names)

print("\nUF2 structure")
blocks = []
for i in range(len(u1) // 512):
    b = u1[i * 512:(i + 1) * 512]
    blocks.append(struct.unpack("<8I", b[:32]) + (b[32:], struct.unpack("<I", b[508:512])[0]))
n = len(blocks)
check("file is whole 512-byte blocks", len(u1) % 512 == 0)
check("all magics valid", all(b[0] == B.UF2_MAGIC0 and b[1] == B.UF2_MAGIC1 and b[9] == B.UF2_MAGIC_END
                              for b in blocks))
check("blocks numbered 0..N-1, numBlocks = N", all(b[5] == i and b[6] == n for i, b in enumerate(blocks)))
cp = open(CP, "rb").read()
cpb = B.uf2_parse(cp)
fam = {b[7] for b in blocks}
check("one family, the same as the pinned CircuitPython UF2", fam == {cpb[0][3]}, fam)
check("256-byte payloads, family flag set", all(b[4] == 256 and b[2] == B.UF2_FLAG_FAMILY for b in blocks))
check("first blocks = the pinned CircuitPython UF2, unchanged",
      [(b[3], b[8][:b[4]]) for b in blocks[:len(cpb)]] == [(a, p) for _f, a, p, _fam in cpb])
fsb = blocks[len(cpb):]
nvm_lo, nvm_hi = B.XIP_BASE + B.NVM_OFFSET, B.XIP_BASE + B.NVM_OFFSET + B.NVM_SIZE
check("default: nothing written into the nvm sector (Store kept)",
      not any(nvm_lo <= b[3] < nvm_hi for b in blocks))
check("filesystem blocks start at 0x10181000, 4 KB aligned",
      fsb[0][3] == B.XIP_BASE + B.FS_OFFSET and fsb[0][3] % 4096 == 0, hex(fsb[0][3]))
check("filesystem blocks contiguous and inside the 4 MB flash",
      all(b[3] == fsb[0][3] + 256 * i for i, b in enumerate(fsb))
      and fsb[-1][3] + 256 <= B.XIP_BASE + B.FLASH_SIZE)
wb = [(a, p) for _f, a, p, _fam in B.uf2_parse(uw) if nvm_lo <= a < nvm_hi]
check("--wipe-store: the whole nvm sector written as 0xFF",
      len(wb) == B.NVM_SIZE // 256 and all(p == b"\xFF" * 256 for _a, p in wb))

print("\nfilesystem read back from the UF2")
img = bytearray(B.FS_SIZE)
for b in fsb:
    o = b[3] - (B.XIP_BASE + B.FS_OFFSET)
    img[o:o + 256] = b[8][:256]
check("written part = the builder's image", bytes(img[:len(fsb) * 256]) == img1[:len(fsb) * 256])
fs = read_fat16(bytes(img))
check("label CIRCUITPY, FAT16, FAT16 cluster count", fs["label"] == "CIRCUITPY" and fs["fstype"] == "FAT16"
      and 4085 <= fs["clusters"] < 65525, (fs["label"], fs["fstype"], fs["clusters"]))
f = fs["files"]
sw = B.SW
for name in ("code.py", "boot.py", "settings.toml"):
    check("%s byte-identical to software/%s" % (name, name), f.get(name) == open(os.path.join(sw, name), "rb").read())
libs = []
for dp, _dn, fns in os.walk(os.path.join(sw, "lib")):
    for fn in fns:
        rel = os.path.relpath(os.path.join(dp, fn), sw).replace(os.sep, "/")
        libs.append(rel)
check("every software/lib file byte-identical (%d files)" % len(libs),
      all(f.get(r) == open(os.path.join(sw, r), "rb").read() for r in libs))
for lib in B.LIBS:
    d = f.get(lib + ".mpy", b"")
    check("%s.mpy present, mpy v6, no host path inside" % lib,
          d[:2] == b"C\x06" and b"Users" not in d and b":/" not in d, d[:2])
    check("  ...and no %s.py next to it (a .py would win over the .mpy)" % lib, lib + ".py" not in f)
expected = set(B.CODE_FILES) | {"settings.toml", ".fseventsd/no_log"} | {l + ".mpy" for l in B.LIBS} | set(libs)
check("exactly the restore set: code.py, boot.py, settings.toml, 4 x .mpy, lib/",
      set(f) == expected, sorted(set(f) ^ expected))
restore = open(os.path.join(HERE, "restore", "restore_brain.sh"), encoding="utf-8").read()
check("library list matches restore_brain.sh LIBS",
      'LIBS="%s"' % " ".join(B.LIBS) in restore)
check("manifest lists every file with its sha256",
      sorted(x["path"] for x in m1["files"]) == sorted(f) and
      all(B.sha256(f[x["path"]]) == x["sha256"] for x in m1["files"]))

out = os.path.join(tempfile.gettempdir(), "noknok-brain-fs.img")
open(out, "wb").write(bytes(img))
print("\n(filesystem image for the Linux check: %s)" % out)
print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
