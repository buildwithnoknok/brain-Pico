#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# dev173_probe_loop.py IP N [OUTDIR] - DEV-173 with the Debug Probe: Ctrl-C into the running product
# while WiFi is busy (the freeze trigger Sue found), N times. Per cycle:
#   1. wait until the product is up (console prints "[maxi] up" or any "up:" line), max 120 s
#   2. 20-60 s of /rpc hello traffic (WiFi busy), console logged passively
#   3. close the port 1-4 s, re-open it (DEV173_REOPEN=1, default, = pico.py), Ctrl-C (x2, 1 s apart) -> wait up to 25 s for ">>>"
#   4. prompt -> Ctrl-D (product restarts) -> next cycle
#      silence -> FREEZE: SWD dump (hang_dump.sh), then SWD reset (swd_reset.sh) -> next cycle
# Output: OUTDIR/run.log, console.log, cycles.jsonl. Needs the Pi4RFID probe tools in ~/dev/dev172.
import glob, json, os, random, socket, subprocess, sys, threading, time
import serial

IP, N = sys.argv[1], int(sys.argv[2])
REOPEN = os.environ.get("DEV173_REOPEN", "1") == "1"   # close + re-open the port before each Ctrl-C (pico.py style)
OUT = os.path.expanduser(sys.argv[3] if len(sys.argv) > 3 else "~/dev/dev172/run173")
TOOLS = os.path.dirname(os.path.abspath(__file__))
PORT = "/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00"
os.makedirs(OUT, exist_ok=True)


def now():
    return time.strftime("%F %T")


def say(m):
    line = "%s %s" % (now(), m)
    print(line, flush=True)
    with open(os.path.join(OUT, "run.log"), "a") as f:
        f.write(line + "\n")


class Console:
    """Keeps the console open, logs every byte, survives the port vanishing."""
    def __init__(self):
        self.s = None; self.buf = b""; self.lock = threading.Lock(); self.bytes = 0
        self.hold = False
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            if self.s is None:
                if self.hold:
                    time.sleep(0.05); continue
                p = glob.glob(PORT)
                if p:
                    try:
                        self.s = serial.Serial(p[0], 115200, timeout=0.2)
                        self.log(b"\n#### port open\n")
                    except Exception:
                        self.s = None
                time.sleep(0.2); continue
            try:
                d = self.s.read(4096)
            except Exception:
                self.s = None; self.log(b"\n#### port GONE\n"); continue
            if d:
                with self.lock:
                    self.buf = (self.buf + d)[-20000:]; self.bytes += len(d)
                self.log(d)

    def log(self, d):
        with open(os.path.join(OUT, "console.log"), "ab") as f:
            f.write(d)

    def send(self, d):
        try:
            self.s.write(d); return True
        except Exception:
            return False

    def close(self):
        """Close the port like pico.py does between commands (DTR drops, host stops reading)."""
        self.hold = True
        s, self.s = self.s, None
        try:
            s.close()
        except Exception:
            pass

    def open_now(self):
        """Re-open immediately (a fresh open = the host submits new reads, like pico.py)."""
        p = glob.glob(PORT)
        self.s = serial.Serial(p[0], 115200, timeout=0.2)
        self.hold = False
        self.log(b"\n#### port re-opened for Ctrl-C\n")

    def mark(self):
        with self.lock:
            return self.bytes

    def since(self, n0):
        with self.lock:
            k = self.bytes - n0
            return self.buf[-k:] if k > 0 else b""


def rpc_hello():
    b = json.dumps({"id": 1, "op": "hello", "args": {}}).encode()
    try:
        c = socket.create_connection((IP, 80), timeout=3)
        c.sendall(b"POST /rpc HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(b) + b)
        d = b""
        while True:
            x = c.recv(4096)
            if not x:
                break
            d += x
        c.close()
        return b"uptime" in d
    except Exception:
        return False


def sh(cmd, tag):
    r = subprocess.run("%s %s" % (cmd, tag), shell=True, capture_output=True, text=True, timeout=300)
    return (r.stdout + r.stderr)[-1500:]


con = Console()
say("=== DEV-173 probe loop: %d Ctrl-C cycles on %s -> %s" % (N, IP, OUT))
freezes = 0
for i in range(1, N + 1):
    rec = {"cycle": i, "at": now()}
    n0 = con.mark(); t0 = time.time()
    while time.time() - t0 < 120 and b" up:" not in con.since(n0) and b"] up" not in con.since(n0):
        time.sleep(0.5)
    rec["up_s"] = round(time.time() - t0, 1) if time.time() - t0 < 120 else None
    busy = random.randint(20, 60); ok = 0; tries = 0; t1 = time.time()
    while time.time() - t1 < busy:
        tries += 1; ok += rpc_hello(); time.sleep(2)
    rec.update(busy_s=busy, rpc_ok=ok, rpc_tries=tries)
    if REOPEN:
        con.close(); time.sleep(random.uniform(1.0, 4.0))   # product keeps printing into nothing
        con.open_now()
    n1 = con.mark()
    con.send(b"\x03"); time.sleep(1); con.send(b"\x03")
    t2 = time.time()
    while time.time() - t2 < 25 and b">>>" not in con.since(n1):
        time.sleep(0.2)
    got = con.mark() - n1
    rec["prompt_s"] = round(time.time() - t2, 2) if b">>>" in con.since(n1) else None
    rec["bytes_after_ctrlc"] = got
    if rec["prompt_s"] is not None:
        rec["result"] = "ok"
        con.send(b"\x04")                       # Ctrl-D: soft reboot, product starts again
    else:
        freezes += 1
        rec["result"] = "FREEZE" if got == 0 else "NO_PROMPT"
        say("cycle %d: %s (%d bytes after Ctrl-C) -> SWD dump + reset" % (i, rec["result"], got))
        rec["dump"] = sh("sh %s/hang_dump.sh" % TOOLS, "dev173_c%d" % i)
        rec["reset"] = sh("sh %s/swd_reset.sh" % TOOLS, "c%d" % i)
    with open(os.path.join(OUT, "cycles.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    say("cycle %2d %-9s up %s busy %ss rpc %d/%d prompt %s bytes %d"
        % (i, rec["result"], rec["up_s"], busy, ok, tries, rec["prompt_s"], got))
say("=== done: %d cycles, %d freeze(s)" % (i, freezes))
