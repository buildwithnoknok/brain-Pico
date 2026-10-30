#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev173_freeze.py IP N [OUTDIR] - DEV-173: which step freezes a running brain - the
# Ctrl-C into the product, a file write through the REPL, or a settings write (nvm flash)?
#
# Runs on a bench Pi that POWERS the brain (uhubctl -l 2 can cut it: a freeze is recovered
# by a power cut, unattended - never on a rig with a PD charger on the PicoHub). Per cycle,
# rotating through three kinds:
#   ctrlc - 60 s app traffic (hello 1/s, settings.get every 3 s), then pico.py exec (Ctrl-C +
#           raw REPL + print) - no write
#   put   - the same traffic, then pico.py put of a 2 KB file to /data/dev173.bin (Ctrl-C +
#           remount + write + sync), i.e. what froze on 29/30 Oct
#   nvm   - the same traffic plus settings.set every 7 s (each one flushed to nvm after 5 s
#           quiet), product keeps running; checked by hello only
# A step "froze" when neither the REPL (ctrlc/put) nor /rpc (nvm) answers within 25 s and
# the USB port did not re-enumerate. Then: console tail + dmesg recorded, power cut, next.
# Writes ~/dev173/run.log and cycles.jsonl. Needs tools/pico.py next to it (../pico.py).
import glob, json, os, random, socket, subprocess, sys, threading, time

IP, N = sys.argv[1], int(sys.argv[2])
OUT = os.path.expanduser(sys.argv[3] if len(sys.argv) > 3 else "~/dev173")
HERE = os.path.dirname(os.path.abspath(__file__))
PICO = [sys.executable, os.path.join(HERE, "..", "pico.py")]
os.makedirs(OUT, exist_ok=True)
blob = os.path.join(OUT, "dev173.bin")
with open(blob, "wb") as f:
    f.write(bytes(range(256)) * 8)


def say(msg):
    line = time.strftime("%F %T ") + msg
    print(line, flush=True)
    with open(os.path.join(OUT, "run.log"), "a") as f:
        f.write(line + "\n")


def rpc(op, args=None, timeout=4):
    b = json.dumps({"id": 1, "op": op, "args": args or {}}).encode()
    try:
        c = socket.create_connection((IP, 80), timeout=timeout)
        c.sendall(b"POST /rpc HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  b"Content-Length: %d\r\n\r\n" % len(b) + b)
        d = b""
        while True:
            x = c.recv(4096)
            if not x:
                break
            d += x
        c.close()
        r = json.loads(d.split(b"\r\n\r\n", 1)[1])
        return r.get("result", r)
    except Exception:
        return None


def wait_up(limit=150):
    t0 = time.time()
    while time.time() - t0 < limit:
        h = rpc("hello", timeout=3)
        if h and "uptime" in h:
            return True
        time.sleep(3)
    return False


def pico(*args, timeout=25):
    try:
        p = subprocess.run(PICO + list(args), capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)[-400:]
    except subprocess.TimeoutExpired:
        return -1, "timeout"


def usb_events():
    out = subprocess.run(["dmesg"], capture_output=True, text=True).stdout
    return out.count("Pico 2 W")


def traffic(seconds, with_settings):
    end = time.time() + seconds
    n, b = 0, 60
    while time.time() < end:
        rpc("hello", timeout=3)
        if n % 3 == 0:
            rpc("settings.get", timeout=3)
        if with_settings and n % 7 == 0:
            b = 60 if b != 60 else 140
            rpc("settings.set", {"values": {"brightness": b}}, timeout=3)
        n += 1
        time.sleep(1)


def cut():
    for a in ("off", "on"):
        subprocess.run("echo raspberry | sudo -S -p '' /usr/sbin/uhubctl -l 2 -a " + a,
                       shell=True, capture_output=True, timeout=30)
        if a == "off":
            time.sleep(4)


def safe_reset():
    pico("exec", "import wifi, time\nwifi.radio.enabled = False\ntime.sleep(0.3)\n"
         "import microcontroller\nmicrocontroller.reset()", timeout=20)


say("=== DEV-173 freeze hunt: %d cycles on %s" % (N, IP))
if not wait_up():
    say("brain not answering at the start - abort")
    sys.exit(1)
counts = {}
for i in range(1, N + 1):
    kind = ("ctrlc", "put", "nvm")[(i - 1) % 3]
    time.sleep(random.randint(10, 30))
    ev0 = usb_events()
    traffic(60, kind == "nvm")
    rec = {"cycle": i, "kind": kind, "at": time.strftime("%T")}
    if kind == "ctrlc":
        rc, out = pico("exec", "print('alive', 6 * 7)")
        ok = "alive 42" in out
    elif kind == "put":
        rc, out = pico("put", blob, "/data/dev173.bin", timeout=60)
        ok = "wrote" in out
    else:
        out = ""
        ok = wait_up(25)
    rec["ok"], rec["out"] = ok, out[-200:]
    if not ok:
        rec["usb_reenumerated"] = usb_events() != ev0
        rec["ping"] = subprocess.run(["ping", "-c", "2", "-W", "1", IP],
                                     capture_output=True).returncode == 0
        rc2, out2 = pico("exec", "print('alive', 6 * 7)")
        rec["repl_after"] = "alive 42" in out2
        if rec["repl_after"]:
            rec["result"] = "slow (REPL answered on the second try)"
        else:
            rec["result"] = "FROZE"
            cut()
    else:
        rec["result"] = "ok"
    counts[(kind, rec["result"])] = counts.get((kind, rec["result"]), 0) + 1
    with open(os.path.join(OUT, "cycles.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    say("cycle %2d %-5s %s %s" % (i, kind, rec["result"],
                                  "" if ok else "(usb re-enum %s, ping %s)"
                                  % (rec.get("usb_reenumerated"), rec.get("ping"))))
    if kind in ("ctrlc", "put") and rec["result"] != "FROZE":
        safe_reset()                          # the product was stopped: start it again
    if not wait_up(180):
        say("cycle %d: brain did not come back - power cut" % i)
        cut()
        wait_up(180)
say("=== done: %s" % ", ".join("%s %s: %d" % (k[0], k[1], v) for k, v in sorted(counts.items())))
