#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev168_wifi_drop.py IP [MINUTES] [OUTDIR] - DEV-168: what a running brain does while its
# WiFi access point is switched off and on again. Runs on the bench host (the Pi) for MINUTES
# (default 40), unattended; a person switches the AP off (2 min) and on again at any time.
#
# Records, without touching the brain:
#   console.log  the brain's console, captured passively (no Ctrl-C), each line stamped with
#                the Pi's clock: shows every "[wifi] link DOWN", "rejoin attempt N (kick)",
#                "rejoined", "carrier restarted" line and anything unexpected
#   probes.jsonl every 10 s: does /rpc hello answer (and how fast); every 60 s while it
#                answers: status.link (drops, rejoins, backoff, poll_max_ms) + uptime
#   run.log      human-readable: when the brain went unreachable / came back, gap length
# Only this program may read the console (reserve the rig: ~/BENCH_IN_USE). The Pi itself
# must stay online during the AP-off (another AP, or LAN), else the probes say nothing.
import glob, json, os, sys, threading, time, urllib.request
import serial

IP = sys.argv[1]
MIN = float(sys.argv[2]) if len(sys.argv) > 2 else 40
OUT = os.path.expanduser(sys.argv[3] if len(sys.argv) > 3 else "~/dev168")
PORT = "/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00"
os.makedirs(OUT, exist_ok=True)


def now():
    return time.strftime("%F %T") + ".%03d" % (int(time.time() * 1000) % 1000)


def say(msg):
    line = "%s %s" % (now(), msg)
    print(line, flush=True)
    with open(os.path.join(OUT, "run.log"), "a") as f:
        f.write(line + "\n")


def console():
    """Read the console forever, reopen it when it comes back (a reset)."""
    log = open(os.path.join(OUT, "console.log"), "a")
    s, bol = None, True
    while True:
        if s is None:
            p = glob.glob(PORT)
            try:
                s = serial.Serial(p[0], 115200, timeout=0.2) if p else None
            except Exception:
                s = None
            if s is None:
                time.sleep(0.05)
                continue
            log.write("%s | #### port open\n" % now())
        try:
            d = s.read(4096)
        except Exception:
            s = None
            log.write("\n%s | #### port GONE (the brain reset)\n" % now())
            log.flush()
            continue
        out = []
        for ch in d.decode("utf-8", "replace"):
            if bol:
                out.append(now() + " | ")
                bol = False
            out.append(ch)
            bol = ch == "\n"
        log.write("".join(out))
        log.flush()


def rpc(op, timeout=3):
    body = json.dumps({"id": 1, "op": op, "args": {}}).encode()
    r = urllib.request.Request("http://%s/rpc" % IP, data=body,
                               headers={"Content-Type": "application/json"})
    t = time.monotonic()
    d = json.loads(urllib.request.urlopen(r, timeout=timeout).read().decode())
    return time.monotonic() - t, d.get("result", d)


threading.Thread(target=console, daemon=True).start()
say("=== DEV-168 WiFi drop watch: %s for %.0f min, output %s" % (IP, MIN, OUT))
end = time.time() + MIN * 60
down_since, last_status = None, 0
while time.time() < end:
    rec = {"at": now()}
    try:
        dt, h = rpc("hello")
        rec.update(ok=True, ms=round(dt * 1000), uptime=h.get("uptime"))
        if down_since:
            say("brain answers again after %.0f s unreachable (uptime %s s)"
                % (time.time() - down_since, h.get("uptime")))
            down_since = None
            last_status = 0
        if time.time() - last_status >= 60:
            _, st = rpc("status", 5)
            rec["link"], rec["ip"] = st.get("link"), st.get("ip")
            last_status = time.time()
    except Exception as e:
        rec.update(ok=False, error=repr(e)[:80])
        if not down_since:
            down_since = time.time()
            say("brain UNREACHABLE (%s)" % rec["error"])
    with open(os.path.join(OUT, "probes.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    time.sleep(10)
say("=== done%s" % (" - brain STILL unreachable since %.0f s" % (time.time() - down_since)
                     if down_since else ""))
