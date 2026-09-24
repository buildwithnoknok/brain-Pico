#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# soak_report.py - turn a night of soak_run.py output into ~40 readable lines.
#
# The point: a 12 h run produces several hundred JSON records. Nobody (human or
# agent) should have to read those. This prints the verdict, the per-scenario
# breakdown, every distinct failure with how often it happened, boot-time and
# free-memory trends, and the first failure in full - which is the one that
# usually explains the rest.
#
#   ./soak_report.py runs/soak_20260924_213000.jsonl
#   ./soak_report.py                  # newest log in ./runs

import glob
import json
import os
import sys


def load(path):
    recs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    return recs


def pct(a, b):
    return (100.0 * a / b) if b else 0.0


def trend(values):
    """First / median / last of a numeric series, for spotting drift."""
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return "n/a"
    s = sorted(vals)
    return "first=%s  median=%s  last=%s  min=%s  max=%s" % (
        vals[0], s[len(s) // 2], vals[-1], s[0], s[-1])


def main(argv):
    path = argv[1] if len(argv) > 1 else None
    if not path:
        here = os.path.dirname(os.path.abspath(__file__))
        hits = sorted(glob.glob(os.path.join(here, "runs", "soak_*.jsonl")))
        if not hits:
            print("no soak logs found")
            return 1
        path = hits[-1]

    recs = load(path)
    if not recs:
        print("empty log: %s" % path)
        return 1

    total = len(recs)
    passed = [r for r in recs if r.get("result") == "PASS"]
    failed = [r for r in recs if r.get("result") != "PASS"]

    print("=" * 72)
    print("noknok architecture soak - %s" % os.path.basename(path))
    print("=" * 72)
    print("window      : %s  ->  %s" % (recs[0].get("ts"), recs[-1].get("ts")))
    print("iterations  : %d" % total)
    print("PASS        : %d  (%.2f%%)" % (len(passed), pct(len(passed), total)))
    print("FAIL        : %d  (%.2f%%)" % (len(failed), pct(len(failed), total)))
    print("VERDICT     : %s" % ("CLEAN - no failures" if not failed
                                else "%d FAILURE(S) - see below" % len(failed)))

    # ── per scenario ─────────────────────────────────────────────────────────
    print("\n--- per scenario ---")
    scen = {}
    for r in recs:
        s = scen.setdefault(r.get("scenario", "?"), [0, 0])
        s[0] += 1
        if r.get("result") != "PASS":
            s[1] += 1
    print("%-12s %7s %7s %9s" % ("scenario", "runs", "fails", "fail %"))
    for name in sorted(scen):
        runs, fails = scen[name]
        print("%-12s %7d %7d %8.2f%%" % (name, runs, fails, pct(fails, runs)))

    # ── distinct failures ────────────────────────────────────────────────────
    if failed:
        print("\n--- distinct failures (count x reason) ---")
        buckets = {}
        for r in failed:
            reason = (r.get("reason") or "?")
            # collapse iteration-specific numbers so like groups with like
            key = "".join("#" if ch.isdigit() else ch for ch in reason)
            buckets.setdefault(key, []).append(r)
        for key in sorted(buckets, key=lambda k: -len(buckets[k])):
            group = buckets[key]
            iters = ", ".join(str(g["n"]) for g in group[:8])
            more = "" if len(group) <= 8 else " ..."
            print("  %4d x  %s" % (len(group), group[0].get("reason")))
            print("          scenarios: %s" % ", ".join(
                sorted(set(g.get("scenario", "?") for g in group))))
            print("          iterations: %s%s" % (iters, more))

        first = failed[0]
        print("\n--- FIRST failure in full (iteration %d, %s) ---"
              % (first.get("n"), first.get("ts")))
        print(json.dumps(first, indent=2)[:3000])

    # ── health trends ────────────────────────────────────────────────────────
    print("\n--- trends ---")
    print("boot time (s)      : %s" % trend([r.get("boot_sec") for r in recs]))

    mem = [(r.get("report") or {}).get("mem_end") for r in recs]
    print("free mem end (B)   : %s" % trend(mem))

    for bus in ("i2c0", "i2c1"):
        en = [((r.get("report") or {}).get("buses", {}).get(bus) or {}).get("enum_sec")
              for r in recs]
        print("%s enumerate (s) : %s" % (bus, trend(en)))

    ops = sum(sum(b.get("ok", 0) for b in
                  ((r.get("report") or {}).get("buses") or {}).values()) for r in recs)
    operr = sum(sum(b.get("err", 0) for b in
                    ((r.get("report") or {}).get("buses") or {}).values()) for r in recs)
    hops = sum(((r.get("report") or {}).get("hold") or {}).get("ok", 0) for r in recs)
    herr = sum(((r.get("report") or {}).get("hold") or {}).get("err", 0) for r in recs)
    print("I2C ops            : %d ok, %d error (%.4f%% error rate)"
          % (ops + hops, operr + herr, pct(operr + herr, ops + hops + operr + herr)))

    reasons = {}
    for r in recs:
        rep = r.get("report") or {}
        for bus, data in (rep.get("buses") or {}).items():
            for e in data.get("errors", []):
                reasons[e] = reasons.get(e, 0) + 1
    if reasons:
        print("\n--- distinct I2C operation errors ---")
        for e in sorted(reasons, key=lambda k: -reasons[k])[:15]:
            print("  %5d x  %s" % (reasons[e], e))

    rr = {}
    for r in recs:
        v = (r.get("report") or {}).get("reset_reason")
        if v:
            rr[v] = rr.get(v, 0) + 1
    print("\nreset reasons      : %s" % ", ".join(
        "%s=%d" % (k, v) for k, v in sorted(rr.items(), key=lambda kv: -kv[1])))

    nv = {}
    for r in recs:
        v = (r.get("report") or {}).get("nvm0")
        if v is not None:
            nv["0x%02X" % v] = nv.get("0x%02X" % v, 0) + 1
    print("crash counter nvm0 : %s" % ", ".join(
        "%s=%d" % (k, v) for k, v in sorted(nv.items())))

    base = os.path.join(os.path.dirname(os.path.abspath(path)), "baseline.json")
    if os.path.exists(base):
        print("\n--- golden baseline ---")
        print(open(base).read().strip())

    print("\n" + "=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
