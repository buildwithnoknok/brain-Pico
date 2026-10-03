# SPDX-License-Identifier: MIT
# test_maxi_tz.py - DEV-73: check Smart Lamp Maxi's on-device summer-time rules
# against the Linux tz database. Runs on the Pi (CPython), not on the Pico.
#   TZ=UTC python3 test_maxi_tz.py smart_lamp_maxi.py
# Loads only the pure helpers from the product script (everything above the
# "Behaviour tuning" banner), then compares utc_offset_s() with zoneinfo
# around every transition 2026-2030 and at random instants.
import os, sys, time, random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

assert os.environ.get("TZ") == "UTC", "run with TZ=UTC (the Pico's RTC is UTC)"
time.tzset()
src = open(sys.argv[1], encoding="utf-8").read()
src = src.split("# ── Behaviour tuning")[0].replace(
    "from noknok import Conductor, BLACK, WHITE, GREY, DARK_GREY, LANDSCAPE", "")
ns = {}
exec(src, ns)

IANA = {"utc": "UTC", "uk": "Europe/London", "cet": "Europe/Zurich",
        "eet": "Europe/Helsinki", "us_e": "America/New_York",
        "us_c": "America/Chicago", "us_m": "America/Denver",
        "us_p": "America/Los_Angeles", "jp": "Asia/Tokyo"}

def ref(t, zone):
    return int(datetime.fromtimestamp(t, timezone.utc)
               .astimezone(ZoneInfo(IANA[zone])).utcoffset().total_seconds())

fails = checks = 0
start, end = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()), \
             int(datetime(2031, 1, 1, tzinfo=timezone.utc).timestamp())
points = list(range(start, end, 3600))                 # every hour for 5 years
random.seed(73)
points += [random.randrange(start, end) for _ in range(20000)]
for zone in IANA:
    for t in points:
        checks += 1
        a, b = ns["utc_offset_s"](t, zone), ref(t, zone)
        if a != b:
            fails += 1
            if fails <= 10:
                print("FAIL", zone, datetime.fromtimestamp(t, timezone.utc), a, b)
print("hhmm:", ns["fmt_hhmm"](7 * 60 + 5), ns["parse_hhmm"]("23:59"), ns["parse_hhmm"]("bad"))
print("%d checks, %d failures -> %s" % (checks, fails, "PASS" if fails == 0 else "FAIL"))
