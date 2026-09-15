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
#   Rounds cycle: idle, store, idle, store (RUNTIME — must always survive),
#   then small JSON / 8 KB product.py / firmware image (SETUP-TIME — known
#   unsafe on this platform, kept to reproduce and to measure /data
#   containment). Replug, wait ~5 s, run again.
#
# Missed the tone? Just run again — that round becomes a "no yank" control.
# Clean up when done: `./pico.py run bench_yank.py cleanup` is not possible
# through pico.py's argv, so run bench_yank_cleanup.py instead (removes the
# yank_* files and prints the tally).

import os, time, json
import supervisor
supervisor.runtime.autoreload = False   # our own file writes must not soft-reboot us
import noknok as nk

STATE   = nk.DATA_DIR + "/yank_state.json"   # written BEFORE the tone, never during
SMALL   = nk.DATA_DIR + "/yank_small.json"
PRODUCT = nk.DATA_DIR + "/yank_product.py"
FW_BIN  = nk.DATA_DIR + "/yank_fw.bin"
FW_JSON = nk.DATA_DIR + "/yank_fw.json"
# Round types. RUNTIME rounds are what a product in the field is exposed to and
# must never lose a file: "idle" = the product loop (no writes at all), "store"
# = the runtime Store being written back-to-back (settings/state/events on
# FRAM or nvm). SETUP rounds write the FAT the way provisioning/OTA do; on this
# platform they are known to be unsafe (DEV-18) — they are kept to reproduce
# the finding and to measure /data containment, not as a pass/fail gate.
RUNTIME = ("idle", "store")
SETUP   = ("small", "product", "firmware")
PHASES  = RUNTIME + RUNTIME + SETUP       # 2 runtime rounds per setup round
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

def verify_idle():
    return True, "n/a (no writes)"

def verify_store():
    """The Store record must be intact (FRAM) or intact-or-empty (nvm: a cut
    mid-write loses the record, by design self-healing)."""
    st = nk.Store()
    v = st.get("yank_store")
    if v is None:
        return st.backend == "nvm", "record empty (%s)" % ("nvm self-heal, OK" if st.backend == "nvm" else "FRAM LOST DATA")
    return isinstance(v, dict) and isinstance(v.get("seq"), int), v.get("seq") if isinstance(v, dict) else None

VERIFY = {"small": verify_small, "product": verify_product, "firmware": verify_firmware,
          "idle": verify_idle, "store": verify_store}

def snapshot():
    """{path: size} of every readable entry in / and /data; garbage entries (a
    damaged directory block shows as names that cannot be stat'ed) counted."""
    snap, garbage = {}, 0
    for d in ("/", nk.DATA_DIR):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            p = d.rstrip("/") + "/" + n
            try:
                snap[p] = os.stat(p)[6]
            except OSError:
                garbage += 1
    return snap, garbage

# Evidence log on the Pi side is the console; on the Pico we keep a one-line
# summary per round in the state file so the tally survives.
state = nk.read_json(STATE, {})
rnd   = int(state.get("round", 0))
tally = state.get("tally", {"pass": 0, "fail": 0, "lost_files": 0})
history = state.get("history", [])
orphans = [p for p in snapshot()[0] if p.endswith(".tmp")]
print("=" * 60)
if rnd == 0:
    print("YANK TEST — first run, nothing to verify yet.")
    print("(if you DID yank before this: the state file itself was lost — that is a FAIL "
          "of the directory block; check the file list against the previous snapshot)")
else:
    phase = state.get("phase")
    ok, detail = VERIFY[phase]()
    existed_before = state.get("existed", False)
    # A file that never existed before this round may legitimately be absent.
    file_ok = ok or (not existed_before and detail is None)
    # Collateral damage: anything in the pre-tone snapshot that is gone now.
    before = state.get("snapshot", {})
    now, garbage = snapshot()
    lost = sorted(n for n in before if n not in now and "/yank_" not in n and not n.endswith(".tmp"))
    verdict = file_ok and not lost and garbage == 0
    kind = "runtime" if phase in RUNTIME else "setup"
    k = "%s_%s" % (kind, "pass" if verdict else "fail")
    tally[k] = tally.get(k, 0) + 1
    tally["lost_files"] = tally.get("lost_files", 0) + len(lost)
    print("ROUND %d (%s, %s): %s" % (rnd, phase, kind.upper(), "PASS" if verdict else "FAIL"))
    print("     target file intact:", "yes" if file_ok else "NO", "| last complete seq =", detail)
    print("     collateral: %d other file(s) LOST, %d garbage directory entries, %d orphan .tmp"
          % (len(lost), garbage, len(orphans)))
    for n in lost:
        print("        lost: %s (%d B)" % (n, before[n]))
    history.append({"round": rnd, "phase": phase, "ok": verdict, "lost": len(lost), "garbage": garbage})
print("tally so far — RUNTIME (must be clean): %d PASS / %d FAIL | SETUP-TIME (known unsafe): "
      "%d PASS / %d FAIL | %d files lost in total"
      % (tally.get("runtime_pass", 0), tally.get("runtime_fail", 0),
         tally.get("setup_pass", 0), tally.get("setup_fail", 0), tally.get("lost_files", 0)))
nk.clean_tmp()

# ── 2. start the next round ──────────────────────────────────────────────────
rnd += 1
phase = PHASES[(rnd - 1) % len(PHASES)]
target = {"small": SMALL, "product": PRODUCT, "firmware": FW_BIN}.get(phase)
try:
    existed = target is not None and os.stat(target) is not None
except OSError:
    existed = False
snap, _ = snapshot()
nk.write_json_atomic(STATE, {"round": rnd, "phase": phase, "existed": existed,
                             "tally": tally, "history": history[-30:], "snapshot": snap})
print("snapshot: %d files before the tone: %s" % (len(snap), " ".join(sorted(snap))))

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
print("ROUND %d — %s (%s)" % (rnd, phase, "RUNTIME: must survive" if phase in RUNTIME else "SETUP-TIME: known unsafe"))
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
    if phase == "idle":
        time.sleep(0.3)                  # a product loop: reads modules, writes nothing
        continue
    if phase == "store":
        nk.store().set("yank_store", {"seq": seq, "brightness": seq % 256})
        continue
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
