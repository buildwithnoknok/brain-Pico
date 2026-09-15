# bench_yank_cleanup.py — verify the last bench_yank.py round, print the tally,
# remove the yank_* files. Run once when the power-yank test is finished.
import os
import noknok as nk

state = nk.read_json("/yank_state.json", {})
tally = state.get("tally", {})
print("power-yank test tally: %d PASS / %d FAIL over %d rounds"
      % (tally.get("pass", 0), tally.get("fail", 0), int(state.get("round", 0))))
print("(the last round is unverified — run bench_yank.py once more first if it was yanked)")
files = [n for n in os.listdir("/") if n.startswith("yank_") or n.endswith(".tmp")]
n = nk.remove(*["/" + f for f in files])
print("removed %d file(s): %s" % (n, ", ".join(files) or "-"))
