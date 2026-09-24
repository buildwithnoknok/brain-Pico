#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# soak_run.py - unattended long-run architecture soak, driven from the Pi4
# bench host (Pi4RFID). Runs for hours with nobody watching; read the result
# the next morning with soak_report.py.
#
#   Per iteration:  power-cycle the product -> wait for the Pico to re-enumerate
#                   -> run soak_probe.py on it -> compare what came back against
#                   the golden baseline -> append one JSON line to the log.
#
# Power is cut with uhubctl on the Pi's own USB hub port. The Pico's VBUS
# pass-through feeds the PicoHub, so cutting that port cold-boots the Pico,
# the PicoHub and every module - a real power failure, not a soft reset.
#
# Design rules:
#   * It must NEVER exit on an error. Every failure is logged and the loop
#     continues - a soak that dies at 03:00 tells you nothing.
#   * The sudo password is NOT stored here (this repo is public). It comes from
#     $SOAK_SUDO_PW or ~/.soak_sudo (chmod 600, outside the repo).
#   * Raw output is kept only for failing iterations, in failures/.
#
# Usage:  ./soak_run.py [--hours 12] [--gap 15] [--dir <outdir>] [--usb]
#         touch <outdir>/STOP   to end the run cleanly.

import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PICO_PY = os.path.join(os.path.dirname(HERE), "pico.py")
if not os.path.exists(PICO_PY):
    PICO_PY = "/home/noknok/dev/pico/pico.py"
PROBE = os.path.join(HERE, "soak_probe.py")
PORT_DIR = "/dev/serial/by-id"
UHUBCTL = "/usr/sbin/uhubctl"

# Each entry: (name, off_seconds, extra_cycles, hold_seconds)
# extra_cycles = -1 means "do not cut power at all" (the idle-hold scenario).
SCENARIOS = {
    "cold":  ("cold_boot",   5.0,  0,   0),    # the ordinary case
    "short": ("short_off",   0.3,  0,   0),    # fast recycle / brownout-ish
    "long":  ("long_off",   30.0,  0,   0),    # caps fully discharged
    "storm": ("storm",       1.0,  5,   0),    # 5 rapid cycles, then probe
    "hold":  ("idle_hold",   0.0, -1, 180),    # NO power cut: 3 min of traffic
}
PATTERN = ["cold", "cold", "short", "cold", "long",
           "cold", "storm", "cold", "short", "cold"]
HOLD_EVERY = 25          # every Nth iteration is an idle_hold instead

PW = None


def log(msg):
    print("%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def sudo_pw():
    pw = os.environ.get("SOAK_SUDO_PW")
    if pw:
        return pw
    try:
        with open(os.path.expanduser("~/.soak_sudo")) as f:
            return f.read().strip()
    except OSError:
        log("FATAL: no sudo password - set $SOAK_SUDO_PW or create ~/.soak_sudo")
        sys.exit(2)


def uhubctl(args, timeout=30):
    cmd = ["sudo", "-S", UHUBCTL] + args
    try:
        p = subprocess.run(cmd, input=(PW + "\n").encode(),
                           capture_output=True, timeout=timeout)
        return p.stdout.decode("utf-8", "replace")
    except Exception as e:
        log("uhubctl %s failed: %s" % (" ".join(args), e))
        return ""


def find_pico_port():
    """Locate the hub location + port the Pico is plugged into, e.g. ('1-1','4').
    Auto-detected so a replug into another socket does not silently break the run."""
    out = uhubctl([])
    loc = None
    for line in out.splitlines():
        m = re.match(r"Current status for hub (\S+)", line)
        if m:
            loc = m.group(1)
            continue
        m = re.match(r"\s*Port (\d+):.*\[(.*)\]", line)
        if m and loc and "Pico" in m.group(2):
            return loc, m.group(1)
    return None, None


def power(loc, port, state):
    uhubctl(["-l", loc, "-p", port, "-a", state])


def wait_for_pico(timeout=45):
    """Wait for the Pico's serial device to reappear after a power cycle.
    Returns seconds waited, or None on timeout."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            for n in os.listdir(PORT_DIR):
                if n.startswith("usb-Raspberry_Pi_Pico") and n.endswith("-if00"):
                    time.sleep(1.5)          # let CircuitPython finish booting
                    return round(time.time() - t0, 1)
        except OSError:
            pass
        time.sleep(0.5)
    return None


def power_cycle(loc, port, off_s, extra_cycles=0):
    """Cut power for off_s. extra_cycles>0 = that many rapid off/on pulses first."""
    for _ in range(max(0, extra_cycles)):
        power(loc, port, "off")
        time.sleep(0.4)
        power(loc, port, "on")
        time.sleep(0.8)
    power(loc, port, "off")
    time.sleep(off_s)
    power(loc, port, "on")


def build_probe(hold_sec, do_usb, reps, tmp_path):
    """Prepend the knobs to soak_probe.py and write the file we actually run."""
    with open(PROBE) as f:
        src = f.read()
    prelude = "HOLD_SEC = %d\nDO_USB = %d\nREPS = %d\n" % (hold_sec, do_usb, reps)
    with open(tmp_path, "w") as f:
        f.write(prelude + src)


def run_probe(hold_sec, do_usb, reps, outdir):
    tmp = os.path.join(outdir, "_probe_gen.py")
    build_probe(hold_sec, do_usb, reps, tmp)
    budget = 90 + hold_sec
    try:
        p = subprocess.run([sys.executable, PICO_PY, "run", tmp, str(budget)],
                           capture_output=True, timeout=budget + 60)
        out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return None, "probe TIMEOUT after %ss" % (budget + 60)
    except Exception as e:
        return None, "probe launch failed: %s" % e

    for line in out.splitlines():
        if line.startswith("@@NK@@"):
            try:
                return json.loads(line[6:]), out
            except Exception as e:
                return None, "bad JSON (%s)\n%s" % (e, out)
    return None, out


def fingerprint(rep):
    """The identity of the whole product: what is on each bus, at what address,
    running what firmware. This is what must not change across a night."""
    fp = {}
    for bus, data in sorted(rep.get("buses", {}).items()):
        fp[bus] = sorted(
            "%s/%s/0x%02x/%s" % (m.get("type"), m.get("uid"), m.get("addr") or 0,
                                 m.get("fw"))
            for m in data.get("modules", [])
        )
    # USB modules count too - a PIO-USB module that stops attaching after a
    # cold boot is exactly the kind of fault this soak exists to find. Only
    # included when USB probing is on, so keep --usb consistent with the
    # baseline you are comparing against.
    usb = rep.get("usb")
    if usb is not None:
        fp["usb"] = sorted(
            "leds/%s/%s" % (m.get("serial"), m.get("fw"))
            for m in usb.get("modules", [])
        )
    return fp


def compare(base, now):
    """Return a list of human-readable differences (empty = identical)."""
    diffs = []
    for bus in sorted(set(list(base.keys()) + list(now.keys()))):
        b, n = set(base.get(bus, [])), set(now.get(bus, []))
        for missing in sorted(b - n):
            diffs.append("%s MISSING %s" % (bus, missing))
        for extra in sorted(n - b):
            diffs.append("%s UNEXPECTED %s" % (bus, extra))
    return diffs


def main(argv):
    global PW
    hours, gap, outdir, do_usb, reps = 12.0, 15.0, os.path.join(HERE, "runs"), 0, 10
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--hours":
            hours = float(argv[i + 1]); i += 2
        elif a == "--gap":
            gap = float(argv[i + 1]); i += 2
        elif a == "--dir":
            outdir = argv[i + 1]; i += 2
        elif a == "--usb":
            do_usb = 1; i += 1
        elif a == "--reps":
            reps = int(argv[i + 1]); i += 2
        else:
            log("unknown argument %s" % a)
            return 2

    PW = sudo_pw()
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(os.path.join(outdir, "failures"), exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    jsonl = os.path.join(outdir, "soak_%s.jsonl" % stamp)
    stopfile = os.path.join(outdir, "STOP")
    if os.path.exists(stopfile):
        os.remove(stopfile)

    loc, port = find_pico_port()
    if not loc:
        log("FATAL: could not find the Pico on any switchable hub port")
        return 2
    log("Pico on hub %s port %s - log: %s" % (loc, port, jsonl))
    log("run for %.1f h, gap %.0f s, usb=%d, reps=%d" % (hours, gap, do_usb, reps))
    log("stop early with:  touch %s" % stopfile)

    # A baseline already in the output directory is reused, so several nights
    # in a row are measured against the SAME golden set. Change the hardware ->
    # delete baseline.json (or use a fresh --dir) and the next run recaptures it.
    baseline = None
    basefile = os.path.join(outdir, "baseline.json")
    if os.path.exists(basefile):
        try:
            with open(basefile) as f:
                baseline = json.load(f)
            log("reusing baseline: %s" % json.dumps(baseline))
        except Exception as e:
            log("baseline.json unreadable (%s) - will recapture" % e)

    deadline = time.time() + hours * 3600
    n = 0
    n_pass = n_fail = 0

    while time.time() < deadline:
        if os.path.exists(stopfile):
            log("STOP file found - ending cleanly")
            break
        n += 1
        key = "hold" if (n % HOLD_EVERY == 0) else PATTERN[(n - 1) % len(PATTERN)]
        name, off_s, extra, hold_sec = SCENARIOS[key]
        rec = {"n": n, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "scenario": name, "off_s": off_s, "result": "FAIL",
               "reason": None, "boot_sec": None}

        try:
            if extra >= 0:
                power_cycle(loc, port, off_s, extra)
                boot = wait_for_pico(45)
                rec["boot_sec"] = boot
                if boot is None:
                    rec["reason"] = "Pico never re-enumerated after power-on"
                    raise RuntimeError(rec["reason"])

            report, raw = run_probe(hold_sec, do_usb, reps, outdir)
            if report is None:
                rec["reason"] = "no probe result"
                rec["raw"] = str(raw)[-2000:]
                raise RuntimeError(rec["reason"])

            rec["report"] = report
            fp = fingerprint(report)

            if baseline is None:
                baseline = fp
                with open(os.path.join(outdir, "baseline.json"), "w") as f:
                    json.dump(baseline, f, indent=2)
                log("baseline captured: %s" % json.dumps(baseline))

            problems = compare(baseline, fp)
            errs = sum(b.get("err", 0) for b in report.get("buses", {}).values())
            hold_err = (report.get("hold") or {}).get("err", 0)
            if errs:
                problems.append("%d I2C op error(s)" % errs)
            if hold_err:
                problems.append("%d hold error(s)" % hold_err)
            for b in report.get("buses", {}).values():
                if b.get("fatal"):
                    problems.append("bus fatal: %s" % b["fatal"])
            if report.get("nvm0") not in (0xFF, 0xC0):
                problems.append("crash counter nvm0=0x%02X" % report["nvm0"])
            if (report.get("usb") or {}).get("error"):
                problems.append("usb: %s" % report["usb"]["error"])

            if problems:
                rec["reason"] = "; ".join(problems)
            else:
                rec["result"] = "PASS"
        except Exception as e:
            if not rec["reason"]:
                rec["reason"] = "%s: %s" % (type(e).__name__, e)

        if rec["result"] == "PASS":
            n_pass += 1
        else:
            n_fail += 1
            try:
                with open(os.path.join(outdir, "failures",
                                       "iter_%05d.txt" % n), "w") as f:
                    f.write(json.dumps(rec, indent=2))
            except OSError:
                pass
            log("iter %d  %-10s FAIL  %s" % (n, name, rec["reason"]))

        with open(jsonl, "a") as f:
            f.write(json.dumps(rec) + "\n")

        if n % 10 == 0:
            log("iter %d  %s  pass=%d fail=%d" % (n, name, n_pass, n_fail))

        time.sleep(gap)

    log("DONE - %d iterations, %d pass, %d fail" % (n, n_pass, n_fail))
    log("summary:  ./soak_report.py %s" % jsonl)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:
        log("interrupted")
        sys.exit(1)
