# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev195_coldstart.py - DEV-195: does the noknok Display (CH32V003 GPU) start after its 3.3 V rail
# is cut and restored? Run on a brain on a PicoHub (MOD-0011) with the Display on bus B (J5/J6), ideally
# alone there: ./pico.py run bench_dev195_coldstart.py 900 > log
#
# Per cycle: PicoHub cycle_rail("bus_b", OFF_MS) -> the Display loses power; then poll the hand-out address
# 0x7F on bus B every 50 ms (reading 10 bytes there is harmless, it assigns nothing). PASS = a type-0x05 module
# answers within FIND_S. FAIL = silent; then also look at 0x7E (parked bootloader) and the bus scan, and look
# once more after LATE_S to see whether it came up late. Logs the hub's vin/v5v/iin (rail off vs on) so the
# Display's own current is the difference when it is alone on bus B. Writes nothing to the Pico FS.
import time
import noknok

LABEL = "level"        # edited per run by the wrapper (the J1 voltage set on the bench supply)
N = 25
OFF_MS = 1000
FIND_S = 6.0
LATE_S = 10.0


def hx(b):
    return None if b is None else "".join("%02x" % x for x in b)


def st(ph):
    try:
        s = ph.status()
        return s.get("vin"), s.get("v5v"), s.get("iin")
    except Exception as e:  # noqa: BLE001
        return ("err", repr(e)[:40], None)


c = noknok.Conductor()
c.enumerate(total_timeout_sec=8)
ph = c.picohub
bus = c.i2c_b
print("##START label=%s n=%d off_ms=%d hub=%s busB=%s" % (LABEL, N, OFF_MS, ph is not None, bus is not None))
passes = fails = 0
for i in range(1, N + 1):
    on_before = st(ph)
    ph.cycle_rail("bus_b", OFF_MS)
    time.sleep(0.02 + OFF_MS / 2000)
    off = st(ph)
    time.sleep(OFF_MS / 2000)
    t_on = time.monotonic()
    seen = None
    uid = None
    iin_max = 0.0
    nxt_st = t_on
    while time.monotonic() - t_on < FIND_S:
        r = c._read(0x7F, 10, bus)
        if r is not None and r[8] == 0x05:
            seen = time.monotonic() - t_on
            uid = hx(r[:8])
            break
        if time.monotonic() >= nxt_st:
            s = st(ph)
            if isinstance(s[2], float):
                iin_max = max(iin_max, s[2])
            nxt_st += 0.25
        time.sleep(0.05)
    time.sleep(max(0.0, 2.5 - (time.monotonic() - t_on)))   # splash + backlight ramp done
    on = st(ph)
    extra = ""
    if seen is None:
        fails += 1
        p7e = hx(c._read(0x7E, 8, bus))
        scan = ["0x%02x" % a for a in c._scan(bus)]
        time.sleep(LATE_S)
        late = c._read(0x7F, 10, bus)
        extra = " r7e=%s scan=%s late=%s" % (p7e, scan, hx(late))
        res = "FAIL"
    else:
        passes += 1
        res = "PASS"
    print("##CYC %d %s t=%s uid=%s on_before=%s off=%s on=%s iin_max_boot=%.3f%s"
          % (i, res, "%.2f" % seen if seen is not None else "-", uid, on_before, off, on, iin_max, extra))
print("##SUMMARY label=%s pass=%d fail=%d" % (LABEL, passes, fails))
c.enumerate(total_timeout_sec=8)   # leave the Display assigned again
