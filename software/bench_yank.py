# bench_yank.py — DEV-18 power-yank test, guided by the board itself.
#
# What it does, every time you run it (`./pico.py run bench_yank.py`):
#   1. VERIFIES the previous round: reads /yank_state.json to learn which file
#      type was being written when you pulled the plug, checks that file is
#      intact (JSON parses / product.py compiles / firmware CRC matches) or is
#      the previous complete version, counts orphan *.tmp files, prints
#      PASS/FAIL. A corrupted filesystem shows up here as OSError or as
#      CircuitPython not even reaching the REPL.
#   2. STARTS the next round: cue = 3 short beeps (LED Button amber), then a
#      CONTINUOUS TONE (LED red) for ~8 s while the Pico writes that file type
#      back-to-back through noknok.write_atomic(). Every instant of the tone is
#      inside a write window — PULL THE CABLE DURING THE TONE. Serial prints the
#      same cue if no buzzer is on the bench.
#   Rounds cycle: small JSON (wifi.json-like) -> 8 KB product.py-like ->
#   firmware image + sidecar (fw cache). Replug, wait ~5 s, run again.
#
# Missed the tone? Just run again — that round becomes a "no yank" control.
# Clean up when done: `./pico.py run bench_yank.py cleanup` is not possible
# through pico.py's argv, so run bench_yank_cleanup.py instead (removes the
# yank_* files and prints the tally).

import os, time, json
import supervisor
supervisor.runtime.autoreload = False   # our own file writes must not soft-reboot us
import noknok as nk

STATE   = "/yank_state.json"
SMALL   = "/yank_small.json"
PRODUCT = "/yank_product.py"
FW_BIN  = "/yank_fw.bin"
FW_JSON = "/yank_fw.json"
PHASES  = ("small", "product", "firmware")
TONE_S  = 8

from module_flasher import crc32

# ── 1. verify the previous round ─────────────────────────────────────────────
def verify_small():
    d = nk.read_json(SMALL)
    return (isinstance(d, dict) and isinstance(d.get("seq"), int)
            and d.get("pad") == "x" * 200), d and d.get("seq")

def verify_product():
    try:
        src = open(PRODUCT).read()
    except OSError:
        return False, None
    try:
        compile(src, PRODUCT, "exec")
    except SyntaxError:
        return False, None
    last = src.rstrip().split("\n")[-1]
    return last.startswith("SEQ = ") and src.startswith("# yank"), last

def verify_firmware():
    meta = nk.read_json(FW_JSON)
    try:
        img = open(FW_BIN, "rb").read()
    except OSError:
        return False, None
    if not isinstance(meta, dict):
        return False, None
    ok = len(img) == meta.get("size") and ("%08x" % crc32(img)) == meta.get("crc32")
    return ok, meta.get("seq")

VERIFY = {"small": verify_small, "product": verify_product, "firmware": verify_firmware}

state = nk.read_json(STATE, {})
rnd   = int(state.get("round", 0))
tally = state.get("tally", {"pass": 0, "fail": 0})
orphans = [n for n in os.listdir("/") if n.endswith(".tmp")]
print("=" * 60)
if rnd == 0:
    print("YANK TEST — first run, nothing to verify yet.")
else:
    phase = state.get("phase")
    ok, detail = VERIFY[phase]()
    existed_before = state.get("existed", False)
    # A file that never existed before this round may legitimately be absent.
    verdict = ok or (not existed_before and detail is None)
    tally["pass" if verdict else "fail"] = tally.get("pass" if verdict else "fail", 0) + 1
    print("ROUND %d (%s): %s" % (rnd, phase, "PASS" if verdict else "FAIL"),
          "- file intact, last complete seq =", detail)
    print("     orphan .tmp files from the interrupted write:", len(orphans),
          "(expected 0 or 1; cleaned now)" if orphans else "")
    if not verdict:
        print("     !!! the file is damaged or missing although it existed before — report this")
print("tally so far: %d PASS / %d FAIL" % (tally.get("pass", 0), tally.get("fail", 0)))
nk.clean_tmp()

# ── 2. start the next round ──────────────────────────────────────────────────
rnd += 1
phase = PHASES[(rnd - 1) % len(PHASES)]
target = {"small": SMALL, "product": PRODUCT, "firmware": FW_BIN}[phase]
try:
    os.stat(target); existed = True
except OSError:
    existed = False
nk.write_json_atomic(STATE, {"round": rnd, "phase": phase, "existed": existed, "tally": tally})

# cue hardware: buzzer + LED Button if on the bench (best-effort)
buzzer = led = None
try:
    from noknok import Conductor
    c = Conductor()
    c.enumerate()
    buzzer = c.buzzer[0] if c.buzzer else None
    led    = c.ledbutton[0] if c.ledbutton else None
except Exception as e:
    print("(no modules for the cue: %r — watch this console instead)" % e)

def beep(ms=120, freq=880):
    if buzzer:
        try: buzzer.play(freq, ms, 70)
        except Exception: pass
def color(r, g, b):
    if led:
        try: led.set_color(r, g, b)
        except Exception: pass

print("-" * 60)
print("ROUND %d — writing: %s" % (rnd, phase))
print("GET READY: three beeps, then a continuous tone. PULL THE CABLE DURING THE TONE.")
for i in (3, 2, 1):
    color(60, 30, 0); beep(); print("   %d..." % i); time.sleep(1.0)

# the write loop = the tone. Every iteration is one atomic write (~0.2–0.6 s).
color(120, 0, 0)
print(">>> TONE ON — PULL NOW (writing %s back-to-back for ~%d s) <<<" % (phase, TONE_S))
seq = 0
t_end = time.monotonic() + TONE_S
while time.monotonic() < t_end:
    seq += 1
    beep(600, 440)                       # re-trigger so the tone is continuous
    if phase == "small":
        nk.write_json_atomic(SMALL, {"seq": seq, "pad": "x" * 200})
    elif phase == "product":
        body = "# yank test product, round %d\n" % rnd
        body += "".join("def f%d():\n    return %d  # filler line\n" % (i, i) for i in range(230))
        body += "SEQ = %d\n" % seq
        nk.write_atomic(PRODUCT, body)   # ~8 KB, like a real product.py
    else:
        img = bytes([(seq + i) & 0xFF for i in range(3000)])
        with nk.writable():
            nk.write_atomic(FW_BIN, img)
            nk.write_atomic(FW_JSON, json.dumps({"seq": seq, "size": len(img),
                                                  "crc32": "%08x" % crc32(img)}))
color(0, 40, 0)
print("<<< tone off — %d writes completed without a yank. Run again for the next round." % seq)
