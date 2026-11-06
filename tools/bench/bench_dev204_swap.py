#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev204_swap.py IP [RUNS] [OUTDIR] - DEV-204: a module of ANOTHER type answering at an
# address the brain saved for a Knob must never be taken for that Knob (the PicoHub at an old
# Knob address read as "Knob held at power-on" and factory-reset a brain, 6 Nov 2026).
#
# Runs on the bench host with the brain on USB (pico.py in ~/dev/pico). No SWD, no phone: per
# run it SWAPS the saved addresses of the Knob and a module of ANOTHER type on the SAME bus
# (the PicoHub if it shares the bus, else e.g. the Display), so on the next boot each answers at
# the other's saved address (both directions at once), then HARD-resets the brain (the power-on path, which runs the
# boot-hold check) with the console port held open, and checks:
#   - no factory reset: hello still answers state "provisioned" (WiFi setup kept)
#   - the console shows the [MODULE] mismatch for the swapped addresses
#   - status lists a knob AND the picohub again (re-adopted as what they are)
# Needs a provisioned brain with a Knob (type 2) and a PicoHub (type 7) and noknok.py >= 1.26
# (the Store record carries "fw"). One JSON line per run + PASS count; console logs in OUTDIR.
import glob, json, os, socket, subprocess, sys, time

IP = sys.argv[1]
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
OUT = os.path.expanduser(sys.argv[3] if len(sys.argv) > 3 else "~/dev204")
PICO = os.path.expanduser("~/dev/pico/pico.py")
os.makedirs(OUT, exist_ok=True)

SWAP = """import json, noknok as nk
s = nk.store()
# a COPY: get() returns the Store's cached dict; changing it in place makes set() see
# "no change" and skip the write (while still returning True)
st = json.loads(json.dumps(s.get('state') or {}))
live = {u: i for u, i in st.items() if isinstance(i, dict) and i.get('address')}
k = [u for u, i in live.items() if i.get('type') == 2]
pair = None
for ku in k:
    for u, i in live.items():
        # another TYPE on the SAME bus with another firmware (a PicoHub preferred)
        if (i.get('type') != 2 and i.get('i2c_bus', 'A') == live[ku].get('i2c_bus', 'A')
                and i.get('fw') != live[ku].get('fw')):
            if pair is None or i.get('type') == 7:
                pair = (ku, u)
if pair:
    a, b = pair
    aa, ba = live[a]['address'], live[b]['address']
    st[a]['address'], st[b]['address'] = ba, aa
    print('SWAP knob', a, hex(aa), '<->', 'type', live[b].get('type'), b, hex(ba),
          'bus', live[a].get('i2c_bus', 'A'), s.set('state', st))
else:
    print('SWAP none: need a knob and another module type on the same bus')
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


def hard_reset_capture(path, secs=90):
    """microcontroller.reset() typed at the REPL, then the boot read with the port held."""
    import serial
    port = glob.glob("/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00")[0]
    s = serial.Serial(port, 115200, timeout=0.5)
    s.write(b"\x02\x03\x03")
    time.sleep(0.5)
    if os.environ.get("SOFT"):
        # SOFT=1: Ctrl-D reload - the restore runs where we can see it (a hard reset
        # restores in the power-on boot-hold phase, before USB is back). The brain may
        # still hard-reset itself (PicoHub round trip, DEV-204): the reader reconnects.
        s.write(b"\x04")
    else:
        s.write(b"import microcontroller\r\nmicrocontroller.reset()\r\n")
    time.sleep(0.5)
    s.close()
    out, end = b"", time.time() + secs
    while time.time() < end:
        try:
            s = serial.Serial(glob.glob("/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00")[0],
                              115200, timeout=0.5)
        except Exception:
            time.sleep(0.02)                # catch the power-on (boot-hold) phase too
            continue
        try:
            while time.time() < end:
                out += s.read(4096)
        except Exception:
            pass                        # USB re-enumerated (the reload once on power-on)
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
    r = subprocess.run([sys.executable, PICO, "exec", SWAP], capture_output=True, text=True, timeout=90)
    swap = [l for l in r.stdout.splitlines() if l.startswith("SWAP")]
    log = hard_reset_capture(os.path.join(OUT, "run%d.log" % n))
    time.sleep(10)
    h = None
    for _ in range(10):
        h = rpc("hello")
        if h:
            break
        time.sleep(3)
    st = rpc("status") or {}
    mods = [m.get("type") for m in st.get("modules", [])]
    res = {"run": n, "swap": swap[0] if swap else None,
           "state": (h or {}).get("state"),
           "mismatch_lines": log.count("[MODULE]"),
           "boot_hold_seen": "[reset] button held" in log,
           "factory_reset": "factory reset" in log.lower() and "[RESET]" in log,
           "modules": mods}
    res["pass"] = bool(swap and "none" not in swap[0] and res["state"] == "provisioned"
                       and res["mismatch_lines"] >= 1 and not res["factory_reset"]
                       and "knob" in mods and "picohub" in mods)
    ok += res["pass"]
    print(json.dumps(res), flush=True)
print("bench_dev204_swap: %d/%d PASS" % (ok, RUNS))
