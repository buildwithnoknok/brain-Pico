# bench_store.py — DEV-18 check of the runtime Store (FRAM / nvm) and of
# stable module addresses. Run via `./pico.py run bench_store.py`.
#   1. Store backend detected, set/get/append/delete round-trip, record size
#   2. the record survives a fresh Store instance (re-read from the backend)
#   3. two enumerations in a row give every module the same address and the
#      second one writes nothing (state unchanged)
#   4. no *.tmp and no runtime files appear in / or /data during all this
import os, time, json
import noknok as nk

res = []
def check(name, ok, detail=""):
    res.append(ok); print(("[PASS] " if ok else "[FAIL] ") + name + (" - %s" % detail if detail else ""))

def listing():
    out = {}
    for d in ("/", nk.DATA_DIR):
        try:
            for n in os.listdir(d):
                p = d.rstrip("/") + "/" + n
                try: out[p] = os.stat(p)[6]
                except OSError: out[p] = -1
        except OSError:
            pass
    return out

before = listing()
st = nk.store()
print("store backend:", st.backend)
check("backend is fram or nvm", st.backend in ("fram", "nvm"), st.backend)

# 1. round trip
t0 = time.monotonic()
ok = st.set("bench", {"a": 1, "b": "two"})
t_set = (time.monotonic() - t0) * 1e3
check("set() returns True", ok, "%.1f ms" % t_set)
check("get() returns what was set", st.get("bench") == {"a": 1, "b": "two"})
for i in range(5):
    st.append("bench_ring", "line %d" % i, max_items=3)
check("append() keeps the last N", st.get("bench_ring") == ["line 2", "line 3", "line 4"])
check("set() of an unchanged value is a no-op", st.set("bench", {"a": 1, "b": "two"}))
rec = nk.Store._encode(0, st._data)
print("     record size now: %d B (cap %d)" % (len(rec), nk.FRAM_SLOT_BYTES if st.backend == "fram" else 4096 - nk.STORE_NVM_OFFSET))

# 2. survives a fresh instance
fresh = nk.Store()
check("fresh Store re-reads the same record", fresh.get("bench") == {"a": 1, "b": "two"} and fresh.backend == st.backend)

st.delete("bench"); st.delete("bench_ring")
check("delete() removes keys", st.get("bench") is None and st.get("bench_ring") is None)

# 3. stable addresses
from noknok import Conductor
c = Conductor()
c.enumerate()
first = {uid: m.address for uid, m in c._registry.items() if m is not None}
state1 = json.dumps(st.get("state"))
seq1 = st._seq
c.enumerate()
second = {uid: m.address for uid, m in c._registry.items() if m is not None}
check("same address for every module on re-enumeration", first == second and len(first) > 0,
      " ".join("%s..=0x%02X" % (u[:6], a) for u, a in sorted(first.items())))
check("second enumeration wrote nothing to the Store", st._seq == seq1 and json.dumps(st.get("state")) == state1)
print("     state:", st.get("state"))

# 4. no files appeared
after = listing()
new = sorted(p for p in after if p not in before)
check("no new files on the filesystem", not new, ", ".join(new))
check("no orphan .tmp", not any(p.endswith(".tmp") for p in after))
try:
    os.stat("/noknok_state.json"); legacy = "still present (legacy, read-only)"
except OSError:
    legacy = "absent"
print("     legacy noknok_state.json:", legacy)
print("SUMMARY: %d/%d passed" % (sum(res), len(res)), "- ALL GOOD" if all(res) else "- FAILURES ABOVE")
