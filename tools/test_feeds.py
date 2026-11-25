# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
"""Host test (tier S) for software/noknok_feeds.py: Python 3 standard library only.

    python tools/test_feeds.py

Real API answers recorded on 20 Nov 2026 live in tools/fixtures/feeds/. The HTTP layer is replaced by a
fake that serves them, so this proves parsing, size caps, error handling, time maths and the poll loop,
NOT the network, TLS or RAM on a real Pico (that is a bench test).
"""
import datetime
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))
import noknok_feeds as nf                                                    # noqa: E402

FIX = os.path.join(HERE, "fixtures", "feeds")
FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def fixture(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


class FakeNet:
    def __init__(self):
        self.urls = []
        self.fail = None             # exception to raise
        self.override = {}           # url substring -> bytes

    def __call__(self, url, timeout, max_bytes):
        self.urls.append(url)
        if self.fail:
            raise self.fail
        for k, v in self.override.items():
            if k in url:
                if len(v) > max_bytes:
                    raise nf.FeedError("too big")
                return v
        if "stationboard" in url:
            return fixture("departures_bern.json")
        if "geocoding" in url:
            return fixture("geocode_bern.json")
        if "open-meteo.com/v1/forecast" in url:
            return fixture("weather_bern.json")
        if "energy-charts" in url:
            return fixture("power_ch.json")
        raise nf.FeedError("http 404")


net = FakeNet()
nf._http[0] = net


def raises(fn, *a, **k):
    try:
        fn(*a, **k)
    except nf.FeedError as e:
        return str(e)
    except Exception as e:
        return "WRONG %r" % (e,)
    return None


print("url and text helpers")
check(nf.quote("Zürich, Bellevue") == "Z%C3%BCrich%2C%20Bellevue", "quote umlauts, comma and space")
check(nf.quote("a-b_c.d~") == "a-b_c.d~", "quote keeps safe characters")

print("time maths (against datetime)")
ok = True
random.seed(5)
for _ in range(400):
    e = random.randint(0, 4102444800)
    d = datetime.datetime.fromtimestamp(e, datetime.timezone.utc)
    if nf.civil(e) != (d.year, d.month, d.day, d.hour, d.minute):
        ok = False
        break
check(ok, "civil() equals datetime for 400 random epochs 1970-2100")
ok = all(nf._days(y, m, 1) == (datetime.date(y, m, 1) - datetime.date(1970, 1, 1)).days
         for y in (1999, 2000, 2024, 2026, 2100) for m in range(1, 13))
check(ok, "_days() equals datetime")
for y in (2025, 2026, 2027):
    ls = nf._last_sunday(y, 3)
    check(datetime.date(y, 3, ls).weekday() == 6 and ls + 7 > 31, "last Sunday of March %d is %d" % (y, ls))
    lo = nf._last_sunday(y, 10)
    check(datetime.date(y, 10, lo).weekday() == 6 and lo + 7 > 31, "last Sunday of October %d is %d" % (y, lo))
utc = lambda *a: int(datetime.datetime(*a, tzinfo=datetime.timezone.utc).timestamp())
check(nf.utc_offset(utc(2026, 3, 29, 0, 59)) == 3600, "29 Mar 2026 00:59 UTC = CET (+1)")
check(nf.utc_offset(utc(2026, 3, 29, 1, 0)) == 7200, "29 Mar 2026 01:00 UTC = CEST (+2)")
check(nf.utc_offset(utc(2026, 10, 25, 0, 59)) == 7200, "25 Oct 2026 00:59 UTC = still CEST")
check(nf.utc_offset(utc(2026, 10, 25, 1, 0)) == 3600, "25 Oct 2026 01:00 UTC = CET")
check(nf.utc_offset(utc(2026, 7, 1, 12, 0), 0) == 3600, "UK/Portugal rule: summer = +1")
check(nf.local_hm(utc(2026, 11, 20, 7, 25)) == (8, 25), "07:25 UTC in November = 08:25 local")

print("departures")
r = nf.departures("Bern, Bahnhof")
check(r["stop"] == "Bern, Bahnhof" and len(r["rows"]) == 15, "15 rows, resolved stop name")
row = r["rows"][0]
check(set(row) == {"cat", "line", "to", "ts", "delay", "pl"} and isinstance(row["ts"], int), "row fields: %s" % row)
check(r["src"].startswith("transport.opendata.ch"), "attribution carried")
u = net.urls[-1]
check("fields%5B%5D=stationboard/stop/departureTimestamp" in u and "limit=15" in u and "station=Bern%2C%20Bahnhof" in u,
      "request uses the field filter (small answer): " + u[:90])
check(len(fixture("departures_bern.json")) < 3000, "recorded answer is %d bytes for 15 departures" % len(fixture("departures_bern.json")))
check(raises(nf.departures, "") == "no stop", "empty stop refused before any request")
net.override["stationboard"] = b'{"stationboard": []}'
check(raises(nf.departures, "Nowhere") == "no departures", "empty board = error")
net.override["stationboard"] = b'{"oops": 1}'
check(raises(nf.departures, "X") == "bad data", "unexpected shape = bad data")
net.override["stationboard"] = b"<html>502</html>"
check(raises(nf.departures, "X") == "bad json", "html answer = bad json")
net.override["stationboard"] = b"x" * (nf.MAX_BYTES + 1)
check(raises(nf.departures, "X") == "too big", "answer over MAX_BYTES refused")
net.override.clear()
net.fail = OSError(110)
check(raises(nf.departures, "X").startswith("fetch"), "OSError becomes FeedError")
net.fail = MemoryError()
check(raises(nf.departures, "X") == "memory", "MemoryError becomes FeedError")
net.fail = None

print("weather")
w = nf.weather("Bern")
check(w["place"] == "Bern" and w["temp"] == 7.4 and w["code"] == 61 and len(w["h"]) == 12, "Bern: %s C, code %s, 12 hours" % (w["temp"], w["code"]))
check(w["off"] == 3600 and set(w["h"][0]) == {"t", "p", "c"}, "utc offset and hourly fields")
n = len([x for x in net.urls if "geocoding" in x])
nf.weather("Bern")
check(len([x for x in net.urls if "geocoding" in x]) == n, "place is geocoded once (cached)")
check(raises(nf.weather, "") == "no place", "empty place refused")
net.override["geocoding"] = b'{"generationtime_ms": 0.3}'
nf._geo_cache.clear()
check(raises(nf.weather, "Qqqq") == "unknown place", "unknown place")
net.override.clear()
nf._geo_cache.clear()

print("power price")
real_time = time.time
time.time = lambda: float(utc(2026, 11, 20, 7, 25))
p = nf.power_price("CH")
check(p["zone"] == "CH" and len(p["p"]) == 24 and p["h0"] == utc(2026, 11, 19, 23, 0), "24 hourly averages from local midnight")
check(p["cur"] is not None and 5 < p["cur"] < 40, "current price %s ct/kWh" % p["cur"])
raw = json.loads(fixture("power_ch.json"))
check(abs(p["p"][0] - round(sum(raw["price"][:4]) / 40.0, 2)) < 0.011, "hour 0 = mean of 4 quarter hours / 10")
check("start=2026-11-20&end=2026-11-21" in net.urls[-1], "request dates are local days: " + net.urls[-1][-40:])
check(raises(nf.power_price, "XX") == "zone", "unknown zone refused")
time.time = lambda: 100.0
check(raises(nf.power_price, "CH") == "no clock", "no clock (RTC not synced) = error, no request")
time.time = real_time

print("real-fetch guard (heap)")
import types
fake_wifi = types.ModuleType("wifi")
fake_wifi.radio = types.SimpleNamespace(connected=True)
sys.modules["wifi"] = fake_wifi
nf._http[0] = None
real_gc = nf.gc
nf.gc = types.SimpleNamespace(collect=lambda: None, mem_free=lambda: 30000)
check(raises(nf._real_get, "https://example.invalid/", 6, 6144) == "low memory 30000", "less than MIN_FREE heap: FeedError('low memory'), no request attempted")
nf.gc = real_gc
nf._http[0] = net
del sys.modules["wifi"]

print("feed manager")
clock = [1000.0]
fm = nf.Feeds(clock=lambda: clock[0])
calls = []


def good(x):
    calls.append(x)
    return {"v": x}


f = fm.add("a", good, every=30, x=1)
check(fm.get("a") is None and fm.age("a") is None, "nothing before the first poll")
check(fm.poll() == "a" and fm.get("a") == {"v": 1} and fm.error("a") is None, "first poll fetches")
check(fm.poll() is None and len(calls) == 1, "not due again yet")
clock[0] += 31
check(fm.poll() == "a" and len(calls) == 2, "due after `every`")
check(fm.age("a") == 0, "age counts from the last good fetch")
fm.update("a", x=7)
check(fm.poll() == "a" and fm.get("a") == {"v": 7}, "update() fetches again at once")
fm.update("a", x=7)
check(fm.poll() is None, "update() with the same value does nothing")

state = {"fail": True}


def flaky():
    if state["fail"]:
        raise nf.FeedError("net down")
    return {"ok": 1}


fm2 = nf.Feeds(clock=lambda: clock[0])
fm2.add("f", flaky, every=60)
check(fm2.poll() is None and fm2.error("f") == "net down" and fm2.get("f") is None, "failure: no data, error kept")
t0 = clock[0]
check(fm2.poll() is None, "backoff: not retried at once")
clock[0] += 16
state["fail"] = False
check(fm2.poll() == "f" and fm2.get("f") == {"ok": 1}, "retry after 15 s works")
state["fail"] = True
clock[0] += 61
fm2.poll()
check(fm2.get("f") == {"ok": 1} and fm2.error("f") == "net down", "later failure keeps the last good data")


def buggy():
    raise ValueError("boom")


fm3 = nf.Feeds(clock=lambda: clock[0])
fm3.add("b", buggy, every=30)
check(fm3.poll() is None and fm3.error("b").startswith("bug"), "a bug in a provider never raises into the product")
fm4 = nf.Feeds(clock=lambda: clock[0])
fm4.add("one", good, every=30, x=1)
fm4.add("two", good, every=30, x=2)
got = [fm4.poll(), fm4.poll(), fm4.poll()]
check(sorted(x for x in got[:2] if x) == ["one", "two"] and got[2] is None, "two feeds: one fetch per poll, round robin")
check(nf.attach(None).poll() is None, "attach() with nothing added is harmless")

print("\nFAILED: %d" % len(FAILS) if FAILS else "\nALL OK")
for f_ in FAILS:
    print(" -", f_)
sys.exit(1 if FAILS else 0)
