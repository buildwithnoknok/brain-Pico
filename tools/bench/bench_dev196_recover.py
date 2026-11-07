#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev196_recover.py UF2 [RUNS] [OUTDIR] - DEV-196: the non-wipe "Recover my device" brain
# image must bring a set-up brain back on its home WiFi, running its product, with no app step.
#
# Runs on the bench host (the Pi) with the brain on USB (pico.py in ~/dev/pico). Needs sudo
# for the mount: env BENCH_SUDO_PW. No hands: per run
#   1. Store read BEFORE (pico.py): WiFi copy present (never the password), state keys, a CRC
#      of the whole nvm area (0x10180000, the Store)
#   2. the brain goes to its UF2 bootloader by itself (on_next_reset(UF2) + noknok.hard_reset(),
#      radio off first), the RP2350 drive is mounted and the UF2 copied - the same as a drag
#   3. console captured from the first second after the copy, up to CAPTURE_S
#   4. Store read AFTER
# Logs per run in OUTDIR: runN.log (console) and one JSON line per run with the timings (s after
# the copy finished) of: "restored from the Store copy", WiFi connected, app channel up, product
# running, and whether the setup hotspot came up instead.
import glob, json, os, re, socket, subprocess, sys, time

UF2 = os.path.abspath(sys.argv[1])
IP = os.environ.get("BRAIN_IP", "192.168.1.194")   # DEV-207: reachable after the run?
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
OUT = os.path.expanduser(sys.argv[3] if len(sys.argv) > 3 else "~/dev196")
PICO = os.path.expanduser("~/dev/pico/pico.py")
PW = os.environ.get("BENCH_SUDO_PW")
CAPTURE_S = 300
MNT = "/mnt/rp2350"
os.makedirs(OUT, exist_ok=True)
if not PW:
    raise SystemExit("set BENCH_SUDO_PW (Pi sudo password) in the environment")

STORE_READ = """import os, microcontroller
import noknok as nk
from module_flasher import crc32
s = nk.store()
w = s.get('wifi')
st = s.get('state') or {}
print('STORE wifi_copy=%s ssid=%s state=%d nvm_crc=%08x wifi_json=%s strikes=%d' % (
    bool(w), (w or {}).get('ssid'), len(st), crc32(microcontroller.nvm[0:len(microcontroller.nvm)]),
    'wifi.json' in os.listdir('/data'), microcontroller.nvm[0] & 0x0F))
"""

TO_UF2 = """import microcontroller
microcontroller.on_next_reset(microcontroller.RunMode.UF2)
import noknok
noknok.hard_reset()
"""


def sudo(*cmd):
    return subprocess.run(["sudo", "-S", "-p", ""] + list(cmd), input=PW + "\n",
                          capture_output=True, text=True, timeout=60)


def store_read():
    r = subprocess.run([sys.executable, PICO, "exec", STORE_READ], capture_output=True, text=True,
                       timeout=120)
    lines = [l for l in r.stdout.splitlines() if l.startswith("STORE")]
    return lines[0] if lines else "STORE read failed: " + r.stdout[-200:]


def flash():
    subprocess.run([sys.executable, PICO, "exec", TO_UF2], capture_output=True, timeout=60)
    dev = None
    for _ in range(100):
        cand = glob.glob("/dev/disk/by-label/RP2350")
        if cand:
            dev = cand[0]
            break
        time.sleep(0.2)
    if not dev:
        raise RuntimeError("RP2350 drive did not appear")
    sudo("mkdir", "-p", MNT)
    sudo("umount", MNT)
    r = sudo("mount", dev, MNT)
    if r.returncode:
        raise RuntimeError("mount failed: %s" % r.stderr)
    sudo("cp", UF2, MNT + "/")
    sudo("sync")
    sudo("umount", MNT)
    return time.time()


def capture(t0, path):
    import serial
    out, end = b"", t0 + CAPTURE_S
    stamps = []
    while time.time() < end:
        try:
            s = serial.Serial(glob.glob("/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00")[0], 115200,
                              timeout=0.2)
        except Exception:
            time.sleep(0.05)
            continue
        try:
            while time.time() < end:
                chunk = s.read(4096)
                if chunk:
                    out += chunk
                    stamps.append((time.time() - t0, len(out)))
                    if b"product.py present" in out and b"app channel up" in out:
                        end = min(end, time.time() + 10)     # product up: a little more, then stop
        except Exception:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass
    with open(path, "wb") as f:
        f.write(out)
    return out.decode("utf-8", "replace"), stamps


def when(text, stamps, marker):
    """Seconds after the copy at which `marker` first appeared in the capture."""
    i = text.encode("utf-8", "replace").find(marker.encode())
    if i < 0:
        return None
    for t, n in stamps:
        if n > i:
            return round(t, 1)
    return None


ok = 0
for n in range(1, RUNS + 1):
    before = store_read()
    t0 = flash()
    text, stamps = capture(t0, os.path.join(OUT, "run%d.log" % n))
    time.sleep(5)
    reachable = False
    for _ in range(6):
        try:
            c = socket.create_connection((IP, 80), timeout=4)
            body = b'{"id":1,"op":"hello","args":{}}'
            c.sendall(b"POST /rpc HTTP/1.1\r\nHost: x\r\nConnection: close\r\nContent-Length: %d\r\n\r\n"
                      % len(body) + body)
            reply = b""
            while True:
                part = c.recv(4096)
                if not part:
                    break
                reply += part
            reachable = b'"state"' in reply
            c.close()
        except Exception:
            pass
        if reachable:
            break
        time.sleep(5)
    after = store_read()
    res = {"run": n, "before": before, "after": after,
           "restored_s": when(text, stamps, "restored from the Store"),
           "wifi_s": when(text, stamps, "[wifi] Connected"),
           "channel_s": when(text, stamps, "app channel up"),
           "product_s": when(text, stamps, "product.py present"),
           "setup_ap": "noknok-setup" in text or "starting AP provisioning" in text,
           "reachable": reachable}
    res["pass"] = bool(res["product_s"] is not None and res["product_s"] < 120 and not res["setup_ap"]
                       and "wifi_copy=True" in before and reachable)
    ok += res["pass"]
    print(json.dumps(res), flush=True)
print("bench_dev196_recover: %d/%d PASS" % (ok, RUNS))
