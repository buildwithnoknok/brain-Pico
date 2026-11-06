#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev205_ota.py IP [RUNS] [OUTDIR] - DEV-205: the brain's own daily OTA pass downloads a
# module image (streamed into the cache, noknok_fetch) and flashes it from the cache file page
# by page - no whole-image allocation (MemoryError at ~16 KB on a booted brain before).
#
# Runs on the bench host with the brain on USB (pico.py in ~/dev/pico) and a PicoHub on the
# brain's bus. Needs the older PicoHub app image on the brain as /picohub_030.bin (the image the
# PicoHub is put back on before each run). Per run:
#   1. REPL: flash the PicoHub back to /picohub_030.bin with the brain's own I2C flasher
#      (ModuleFlasher, the same path the OTA uses) and delete the cached /data/fw_picohub.*
#   2. make the daily check due (nvm[1:5] = 0) and hard-reset, console held open
#   3. check: "[fw] fetch: mem_free .., largest free block .." logged, "[fw] cached picohub",
#      flashing reached 100 %, status shows the PicoHub on the published version, the brain is
#      provisioned with 0 strikes and the USB LEDs are still there
# One JSON line per run + PASS count; console logs in OUTDIR.
import glob, json, os, re, socket, subprocess, sys, time

IP = sys.argv[1]
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
OUT = os.path.expanduser(sys.argv[3] if len(sys.argv) > 3 else "~/dev205")
PICO = os.path.expanduser("~/dev/pico/pico.py")
os.makedirs(OUT, exist_ok=True)

DOWNGRADE = """import noknok as nk
from module_flasher import ModuleFlasher
c = nk.Conductor()
c.enumerate()
h = c.picohub
if h is None:
    print('DOWN no picohub')
else:
    data = open('/picohub_030.bin', 'rb').read()
    ModuleFlasher(c._bus(getattr(h, 'i2c_bus', 'A'))).flash(data, runtime_addr=h.address)
    import time
    time.sleep(1.5)
    c.enumerate()
    print('DOWN picohub now', c.picohub.firmware_version if c.picohub else None)
with nk.writable():
    for f in ('/data/fw_picohub.bin', '/data/fw_picohub.json'):
        try:
            import os
            os.remove(f)
        except OSError:
            pass
import microcontroller
microcontroller.nvm[1:5] = bytes(4)
print('DOWN cache cleared, check due')
"""


def rpc(op, timeout=4.0):
    b = json.dumps({"id": 1, "op": op, "args": {}}).encode()
    try:
        c = socket.create_connection((IP, 80), timeout=timeout)
        c.sendall(b"POST /rpc HTTP/1.1\r\nHost: x\r\nConnection: close\r\nContent-Length: %d\r\n\r\n"
                  % len(b) + b)
        d = b""
        while True:
            x = c.recv(4096)
            if not x:
                break
            d += x
        c.close()
        return json.loads(d.split(b"\r\n\r\n", 1)[1])
    except Exception:
        return None


def port():
    return glob.glob("/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00")[0]


def hard_reset_capture(path, secs):
    import serial
    s = serial.Serial(port(), 115200, timeout=0.5)
    s.write(b"\x02\x03\x03")
    time.sleep(0.5)
    s.write(b"import microcontroller\r\nmicrocontroller.reset()\r\n")
    time.sleep(0.3)
    s.close()
    out, end = b"", time.time() + secs
    while time.time() < end:
        try:
            s = serial.Serial(port(), 115200, timeout=0.2)
        except Exception:
            time.sleep(0.02)
            continue
        try:
            while time.time() < end:
                out += s.read(4096)
        except Exception:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass
    with open(path, "wb") as f:
        f.write(out)
    return out.decode("utf-8", "replace")


ok = 0
for n in range(1, RUNS + 1):
    r = subprocess.run([sys.executable, PICO, "exec", DOWNGRADE], capture_output=True, text=True, timeout=180)
    down = [l for l in r.stdout.splitlines() if l.startswith("DOWN")]
    log = hard_reset_capture(os.path.join(OUT, "run%d.log" % n), 200)
    st = None
    for _ in range(10):
        st = rpc("status")
        if st:
            break
        time.sleep(5)
    st = st or {}
    mods = {m.get("type"): m.get("fw") for m in st.get("modules", [])}
    mem = re.search(r"fetch: mem_free (\d+), largest free block (\d+)", log)
    res = {"run": n, "downgrade": down,
           "mem_free": int(mem.group(1)) if mem else None,
           "largest_block": int(mem.group(2)) if mem else None,
           "cached": "[fw] cached picohub" in log,
           "flashed_100": "16768/16768 (100%)" in log or re.search(r"flashing (\d+)/\1 \(100%\)", log) is not None,
           "memoryerror": "MemoryError" in log or "memory allocation failed" in log,
           "state": st.get("state"), "strikes": st.get("strikes"),
           "picohub": mods.get("picohub"), "leds16": mods.get("leds16")}
    res["pass"] = bool(res["cached"] and res["flashed_100"] and not res["memoryerror"]
                       and res["state"] == "provisioned" and res["strikes"] == 0
                       and res["picohub"] == "0.3.1" and res["leds16"])
    ok += res["pass"]
    print(json.dumps(res), flush=True)
print("bench_dev205_ota: %d/%d PASS" % (ok, RUNS))
