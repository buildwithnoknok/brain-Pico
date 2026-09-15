# bench_fs_policy.py — DEV-18 acceptance check for the filesystem policy.
# Run on the Pico via `./pico.py run bench_fs_policy.py` AFTER a power cycle /
# hard reset with the new boot.py + settings.toml in place (boot.py only runs
# on a hard reset, not on Ctrl-D).
#
# Proves, on the real board:
#   1. the filesystem is READ-ONLY to the program outside a window
#   2. noknok.writable() opens a window, nested windows share one remount,
#      and the filesystem is read-only again afterwards
#   3. write_atomic() replaces a file via .tmp + rename and leaves no .tmp
#   4. clean_tmp() removes an orphan .tmp
#   5. how long a window costs (remount + small atomic write + sync)
#   6. what boot.py decided (boot_out.txt) and what settings.toml says
# Prints PASS/FAIL per check and a summary line. Leaves no files behind.

import os, time, json
import noknok as nk

results = []
def check(name, ok, detail=""):
    results.append(ok)
    print(("[PASS] " if ok else "[FAIL] ") + name + ((" - " + str(detail)) if detail else ""))

print("DEV-18 filesystem policy check -", os.uname().version)
try:
    print("--- boot_out.txt ---")
    print(open("/boot_out.txt").read().strip())
    print("--------------------")
except OSError:
    print("(no boot_out.txt)")
print("settings.toml NOKNOK_USB_DRIVE =", nk.env("NOKNOK_USB_DRIVE"),
      "-> usb_drive_visible():", nk.usb_drive_visible())
print("pins: SDA", nk.env_pin("NOKNOK_I2C_SDA", None), "SCL", nk.env_pin("NOKNOK_I2C_SCL", None),
      "freq", nk.env("NOKNOK_I2C_FREQ"))

T = "/dev18_test.txt"

# 1. read-only outside a window
ro = False
try:
    open(T, "w").close()
except OSError as e:
    ro = True
check("filesystem read-only to the program outside a window", ro,
      "" if ro else "open('w') SUCCEEDED - boot.py still remounts rw, or drive mode wrong")

# 2 + 3. window + atomic replace
t0 = time.monotonic()
nk.write_atomic(T, "one")
t_first = time.monotonic() - t0
check("write_atomic creates the file inside a window", open(T).read() == "one")
t0 = time.monotonic()
nk.write_atomic(T, "two")
t_replace = time.monotonic() - t0
check("write_atomic replaces an existing file", open(T).read() == "two")
check("no .tmp left behind", not any(n == "dev18_test.txt.tmp" for n in os.listdir("/")))
print("     window cost: first write %.1f ms, replace %.1f ms" % (t_first * 1e3, t_replace * 1e3))

# 5b. where does the window cost go? (informational)
import storage
def _ms(fn):
    t = time.monotonic(); fn(); return (time.monotonic() - t) * 1e3
def _w():
    f = open(T + ".tmp", "w"); f.write("x" * 200); f.close()
steps = [("remount rw", lambda: storage.remount("/", readonly=False)),
         ("write 200 B", _w),
         ("os.sync", os.sync),
         ("rename", lambda: os.rename(T + ".tmp", T)),
         ("remount ro", lambda: storage.remount("/", readonly=True))]
print("     breakdown: " + ", ".join("%s %.0f ms" % (n, _ms(fn)) for n, fn in steps))

# nested windows: one remount, still writable inside, read-only after
nested_ok = True
try:
    with nk.writable():
        with nk.writable():
            nk.write_atomic(T, "three")
        with open(T, "a") as f:      # still inside the outer window
            f.write("+")
except OSError as e:
    nested_ok = False
check("nested windows stay writable until the outermost exits",
      nested_ok and open(T).read() == "three+")
ro_after = False
try:
    open(T, "a").close()
except OSError:
    ro_after = True
check("read-only again after the window", ro_after)

# 4. orphan .tmp sweep
with nk.writable():
    open("/dev18_orphan.json.tmp", "w").write("{partial")
n = nk.clean_tmp()
check("clean_tmp removes an orphan .tmp", n >= 1 and
      "dev18_orphan.json.tmp" not in os.listdir("/"), "removed %d" % n)

# json helpers
check("write_json_atomic / read_json round-trip",
      nk.write_json_atomic(T, {"a": 1}) and nk.read_json(T) == {"a": 1})

# append_line
nk.append_line(T, "L1")
check("append_line appends inside a window", open(T).read().endswith("L1\n"))

# cleanup
nk.remove(T)
check("remove() deletes inside a window", "dev18_test.txt" not in os.listdir("/"))

print("SUMMARY: %d/%d passed" % (sum(results), len(results)),
      "- ALL GOOD" if all(results) else "- FAILURES ABOVE")
