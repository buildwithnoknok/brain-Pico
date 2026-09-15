# bench_yank_cleanup.py — verify the last bench_yank.py round, print the tally,
# remove the yank_* files. Run once when the power-yank test is finished.
import os
import noknok as nk

state = nk.read_json(nk.DATA_DIR + "/yank_state.json", {})
tally = state.get("tally", {})
print("power-yank test tally: runtime %d PASS / %d FAIL, setup-time %d PASS / %d FAIL over %d rounds"
      % (tally.get("runtime_pass", 0), tally.get("runtime_fail", 0), tally.get("setup_pass", 0), tally.get("setup_fail", 0), int(state.get("round", 0))))
print("(the last round is unverified — run bench_yank.py once more first if it was yanked)")
files = []
for d in ("/", nk.DATA_DIR):
    try:
        files += [d.rstrip("/") + "/" + n for n in os.listdir(d) if n.startswith("yank_") or n.endswith(".tmp")]
    except OSError:
        pass
n = nk.remove(*files)
print("removed %d file(s): %s" % (n, ", ".join(files) or "-"))
nk.store().delete("yank_store")
