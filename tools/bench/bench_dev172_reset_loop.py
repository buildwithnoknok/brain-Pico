#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev172_reset_loop.py IP [N] [OUTDIR] - DEV-172: N software resets of a running brain
# (default 50), the console captured PASSIVELY from the first byte after every reset.
#
# Runs on the bench host (the Pi), unattended, for about an hour (N=50). Per cycle:
#   1. wait until the product runs (/rpc hello answers), then 20-40 s more (random)
#   2. reset it, alternating the two software paths:
#        "repl" - Ctrl-C on the console, then microcontroller.reset() (what pico.py exec does,
#                 the path of the 29 Oct hang)
#        "rpc"  - the app's {"op": "reboot"} over WiFi (no console interference at all)
#   3. timestamps: USB port gone / back, first console byte, first /rpc answer
#   4. after recovery: /rpc status -> PicoHub power status (reset_cause, rails, 5 V, input),
#      the brain's events since boot, strikes, link stats
# A cycle whose brain does not answer within 180 s is a HANG: the console tail, the port state,
# dmesg and the REPL's answer to Ctrl-C are recorded, then the script waits (up to 8 h) for a
# human replug and carries on - so one hang does not end the run.
#
# Only this program may read the console while it runs (no pico.py, no other listener):
# reserve the rig first (~/BENCH_IN_USE).
# Environment: DEV172_HOW=rpc|repl|both (default both); DEV172_RADIO=off|on|alt (see below); DEV172_CUT=1 recovers a hang by itself
# with a real power cut (sudo uhubctl -l 2 off/on - only on a rig where the Pi powers the
# brain, not with a PD charger on the PicoHub) instead of waiting for a human replug;
# sudo needs BENCH_SUDO_PW in the environment or passwordless sudo. Output in OUTDIR (default ~/dev172):
#   console.log  every console byte, each line stamped with the Pi's clock
#   cycles.jsonl one JSON line per cycle (the result)
#   run.log      human-readable progress
import glob, json, os, random, subprocess, sys, threading, time, urllib.request
import serial

IP = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 50
OUT = os.path.expanduser(sys.argv[3] if len(sys.argv) > 3 else "~/dev172")
PORT = "/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00"
RECOVER_S = 180
HOW = os.environ.get("DEV172_HOW", "both")
# DEV-179 A/B: DEV172_RADIO=off (default: radio off before the reset, as shipped) | on (plain
# reset, radio still up) | alt (alternate per cycle). Applies to app reboots (rpc) only;
# needs code.py 0.38+ with the reboot op's radio_off arg.
RADIO = os.environ.get("DEV172_RADIO", "off")
CUT = os.environ.get("DEV172_CUT") == "1"


def power_cut():
    # sudo password from BENCH_SUDO_PW (never in a file), else passwordless sudo
    pw = os.environ.get("BENCH_SUDO_PW")
    for a in ("off", "on"):
        subprocess.run(["sudo", "-S", "-p", "", "/usr/sbin/uhubctl", "-l", "2", "-a", a],
                       input=(pw + chr(10)) if pw else "", text=True,
                       capture_output=True, timeout=30)
        if a == "off":
            time.sleep(4)
os.makedirs(OUT, exist_ok=True)


def now():
    return time.strftime("%F %T") + ".%03d" % (int(time.time() * 1000) % 1000)


def say(msg):
    line = "%s %s" % (now(), msg)
    print(line, flush=True)
    with open(os.path.join(OUT, "run.log"), "a") as f:
        f.write(line + "\n")


class Console(threading.Thread):
    """Owns the serial port: reads everything, reopens it the moment it comes back."""
    def __init__(self):
        super().__init__(daemon=True)
        self.s = None
        self.lock = threading.Lock()
        self.log = open(os.path.join(OUT, "console.log"), "a")
        self.tail = ""                # the last ~4 KB, for the hang record
        self.gone_at = self.back_at = self.first_byte_at = None
        self.bytes = 0
        self._bol = True

    def _write(self, text):
        out = []
        for ch in text:
            if self._bol:
                out.append(now() + " | ")
                self._bol = False
            out.append(ch)
            if ch == "\n":
                self._bol = True
        self.log.write("".join(out))
        self.log.flush()
        self.tail = (self.tail + text)[-4096:]

    def mark(self, text):
        with self.lock:
            self._write(("" if self._bol else "\n") + "#### " + text + "\n")

    def arm(self):
        """Start timing a reset: forget the previous gone/back/first-byte stamps."""
        self.gone_at = self.back_at = self.first_byte_at = None

    def send(self, data):
        with self.lock:
            if self.s is None:
                return False
            try:
                self.s.write(data)
                return True
            except Exception:
                return False

    def run(self):
        while True:
            if self.s is None:
                p = glob.glob(PORT)
                if not p:
                    time.sleep(0.02)
                    continue
                try:
                    s = serial.Serial(p[0], 115200, timeout=0.2)
                except Exception:
                    time.sleep(0.02)
                    continue
                with self.lock:
                    self.s = s
                if self.gone_at and not self.back_at:
                    self.back_at = time.time()
                self.mark("port open %s" % p[0])
            try:
                d = self.s.read(4096)
            except Exception:
                with self.lock:
                    try:
                        self.s.close()
                    except Exception:
                        pass
                    self.s = None
                if not self.gone_at:
                    self.gone_at = time.time()
                self.mark("port GONE")
                continue
            if d:
                if self.gone_at and not self.first_byte_at:
                    self.first_byte_at = time.time()
                self.bytes += len(d)
                with self.lock:
                    self._write(d.decode("utf-8", "replace"))


def rpc(op, timeout=4, **args):
    body = json.dumps({"id": 1, "op": op, "args": args}).encode()
    r = urllib.request.Request("http://%s/rpc" % IP, data=body,
                               headers={"Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(r, timeout=timeout).read().decode())
    return d.get("result", d)


def try_rpc(op, timeout=4, **args):
    try:
        return rpc(op, timeout, **args)
    except Exception:
        return None


def wait_hello(limit):
    t0 = time.time()
    while time.time() - t0 < limit:
        h = try_rpc("hello", 2)
        if h and "uptime" in h:
            return time.time(), h
        time.sleep(1)
    return None, None


def dmesg_tail(n=30):
    try:
        return subprocess.run(["dmesg", "-T"], capture_output=True, text=True,
                              timeout=10).stdout.splitlines()[-n:]
    except Exception as e:
        return ["dmesg failed: %r" % e]


def after_boot(rec):
    time.sleep(15)          # /rpc is up before the product's own USB enumeration is done
    st = try_rpc("status", 6) or {}
    p = st.get("power") or {}
    rec["picohub"] = {k: p.get(k) for k in ("reset_cause", "pd_state", "contract_v", "vin",
                                            "iin", "v5v", "temp_c", "rails", "faults",
                                            "warnings", "error") if k in p}
    rec["strikes"] = st.get("strikes")
    rec["modules"] = sorted(m.get("type") for m in st.get("modules") or [])
    rec["link"] = st.get("link")
    ev = st.get("events") or []
    rec["events_tail"] = ev[-4:]


def main():
    con = Console()
    con.start()
    say("=== DEV-172 reset loop: %d resets of %s, output %s" % (N, IP, OUT))
    t, h = wait_hello(RECOVER_S)
    if not t:
        say("brain not answering at the start - abort")
        return
    hangs = 0
    for i in range(1, N + 1):
        settle = random.randint(20, 40)
        time.sleep(settle)
        how = HOW if HOW in ("rpc", "repl") else ("repl" if i % 2 else "rpc")
        h = try_rpc("hello", 3) or {}
        rec = {"cycle": i, "how": how, "at": now(), "settled_s": settle,
               "uptime_before": h.get("uptime")}
        con.mark("cycle %d: reset via %s" % (i, how))
        con.arm()
        t0 = time.time()
        if how == "repl":
            con.send(b"\x03")
            time.sleep(0.3)
            con.send(b"\x03")
            time.sleep(1.5)
            rec["repl_prompt"] = ">>>" in con.tail[-400:]
            con.send(b"import microcontroller; microcontroller.reset()\r\n")
        else:
            radio_off = RADIO == "off" or (RADIO == "alt" and i % 2 == 0)
            rec["radio_off"] = radio_off
            rec["rpc_reply"] = try_rpc("reboot", 4, radio_off=radio_off)
        t_ok, h = wait_hello(RECOVER_S)
        rel = lambda x: round(x - t0, 2) if x else None
        rec.update(port_gone_s=rel(con.gone_at), port_back_s=rel(con.back_at),
                   first_byte_s=rel(con.first_byte_at), rpc_up_s=rel(t_ok))
        if t_ok:
            rec["result"] = "ok"
            rec["uptime_after"] = (h or {}).get("uptime")
            after_boot(rec)
        else:
            hangs += 1
            rec["result"] = "HANG"
            rec["port_present"] = bool(glob.glob(PORT))
            rec["console_tail"] = con.tail[-1500:]
            rec["dmesg"] = dmesg_tail()
            n0 = con.bytes
            con.send(b"\x03")
            time.sleep(0.5)
            con.send(b"\r\n")
            time.sleep(3)
            rec["ctrl_c_answer_bytes"] = con.bytes - n0
            rec["ctrl_c_tail"] = con.tail[-300:] if con.bytes > n0 else ""
            say("cycle %d: HANG (port %s, Ctrl-C answer %d bytes) - %s"
                % (i, "present" if rec["port_present"] else "absent", rec["ctrl_c_answer_bytes"],
                   "power cut" if CUT else "waiting for a replug"))
            con.mark("HANG - " + ("power cut" if CUT else "waiting for a replug"))
            if CUT:
                power_cut()
                rec["power_cut"] = True
            t_back, h = wait_hello(8 * 3600)
            rec["recovered_after_s"] = rel(t_back)
            if t_back:
                after_boot(rec)
                rec["after_replug"] = True
        with open(os.path.join(OUT, "cycles.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
        p = rec.get("picohub") or {}
        say("cycle %2d %-4s %-4s gone %s back %s byte %s rpc %s | hub reset=%s 5V=%s vin=%s faults=%s mods=%d"
            % (i, how, rec["result"], rec["port_gone_s"], rec["port_back_s"], rec["first_byte_s"],
               rec["rpc_up_s"], p.get("reset_cause"), p.get("v5v"), p.get("vin"), p.get("faults"),
               len(rec.get("modules") or [])))
        if rec["result"] == "HANG" and not rec.get("recovered_after_s"):
            break
    say("=== done: %d cycles, %d hang(s)" % (i, hangs))


main()
