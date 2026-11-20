# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
"""
noknok_feeds - public data for products (departures, weather, power price). Exploration draft, 20 Nov 2026.

WHY A LIBRARY: a product must not do network I/O itself (poc validator: socketpool / ssl /
adafruit_requests are errors), and a reviewed library is the safe place for it: the providers below
are a fixed, reviewed list, they fetch only what is needed (the APIs' own field filters keep the
answers small: stationboard 25 KB -> 2.5 KB), cap the size, never raise into the product, and
keep the last good data when the network is down.

    from noknok import Conductor
    import noknok_feeds as nf

    c = Conductor()
    c.enumerate()
    feeds = nf.attach(c)                                   # nothing is fetched yet
    feeds.add("deps", nf.departures, every=30, station="Zuerich, Bellevue")
    while True:
        if feeds.poll():                                   # fetches AT MOST ONE due feed (blocks <= ~8 s)
            redraw(feeds.get("deps"))                      # last good data, None until the first success
        c.sleep(0.05)

Rules kept: no file writes (ADR-003), no secrets (all sources are keyless), one HTTPS request at a time,
response size capped (MAX_BYTES), a failed fetch keeps the old data and backs off, `poll()` never raises.
The blocking fetch means the app channel waits up to TIMEOUT_S: keep `every` >= 20 s.

Sources and terms (check before launch): see exploration repo data-sources/README.md. Every provider result
carries `src`, the attribution line the product must show (app info row or display).
"""

import gc
import json
import time

try:                                    # keep the app answered between requests (DEV-34)
    from noknok import _service
except Exception:                       # pragma: no cover - running without noknok
    def _service():
        pass

__version__ = "0.1.0"

MAX_BYTES = 6144          # never read more than this (the Pico has big blocks only early after boot)
TIMEOUT_S = 6
CHUNK = 512


class FeedError(Exception):
    pass


# ---------------------------------------------------------------------------------------------
# HTTP: one place, swappable for tests (_http[0] = fn(url, timeout, max_bytes) -> bytes)
# ---------------------------------------------------------------------------------------------
_http = [None]
_session = [None]


def _real_get(url, timeout, max_bytes):
    import wifi
    if not wifi.radio.connected:
        raise FeedError("wifi down")
    if _session[0] is None:
        import adafruit_connection_manager
        import adafruit_requests
        pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
        ctx = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
        _session[0] = adafruit_requests.Session(pool, ctx)
    gc.collect()
    try:
        r = _session[0].get(url, timeout=timeout)
    except OSError as e:
        raise FeedError("net %s" % (e,))
    except RuntimeError as e:
        raise FeedError("tls %s" % (e,))
    try:
        if r.status_code != 200:
            raise FeedError("http %d" % r.status_code)
        n = r.headers.get("content-length")
        if n and int(n) > max_bytes:
            raise FeedError("too big %s" % n)
        buf = bytearray()
        for chunk in r.iter_content(CHUNK):
            buf += chunk
            if len(buf) > max_bytes:
                raise FeedError("too big")
        return bytes(buf)
    finally:
        r.close()


def get_json(url, timeout=TIMEOUT_S, max_bytes=MAX_BYTES):
    """GET a URL, return the parsed JSON. Raises FeedError (never anything else)."""
    _service()
    try:
        raw = (_http[0] or _real_get)(url, timeout, max_bytes)
    except FeedError:
        raise
    except MemoryError:
        gc.collect()
        raise FeedError("memory")
    except Exception as e:
        raise FeedError("fetch %r" % (e,))
    try:
        return json.loads(str(raw, "utf-8"))
    except Exception:
        raise FeedError("bad json")


_SAFE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"


def quote(s):
    """Percent-encode a text for a URL query (UTF-8)."""
    out = ""
    for b in str(s).encode("utf-8"):
        ch = chr(b)
        out += ch if ch in _SAFE else "%%%02X" % b
    return out


# ---------------------------------------------------------------------------------------------
# Time (the RTC is UTC after the boot NTP sync; products compute local time here)
# ---------------------------------------------------------------------------------------------
def _days(y, m, d):
    """Days since 1970-01-01 of a calendar date (proleptic Gregorian)."""
    if m <= 2:
        y -= 1
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    return era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy - 719468


def civil(epoch):
    """UTC epoch -> (year, month, day, hour, minute)."""
    days, rem = divmod(int(epoch), 86400)
    z = days + 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return (y + (1 if m <= 2 else 0), m, d, rem // 3600, (rem % 3600) // 60)


def _last_sunday(y, m):
    wd = (_days(y, m, 31) + 4) % 7          # 0 = Sunday
    return 31 - wd


def utc_offset(epoch, std_hours=1):
    """Seconds to add to UTC for local time with the EU summer-time rule
    (last Sunday of March 01:00 UTC to last Sunday of October 01:00 UTC)."""
    y = civil(epoch)[0]
    start = _days(y, 3, _last_sunday(y, 3)) * 86400 + 3600
    end = _days(y, 10, _last_sunday(y, 10)) * 86400 + 3600
    return std_hours * 3600 + (3600 if start <= epoch < end else 0)


def local_hm(epoch, std_hours=1):
    """(hour, minute) of a UTC epoch in local time."""
    t = civil(epoch + utc_offset(epoch, std_hours))
    return t[3], t[4]


def clock_ok():
    """True once the RTC has been set (NTP at boot). Before that time.time() is near 2000."""
    return time.time() > 1700000000


# ---------------------------------------------------------------------------------------------
# Providers: each returns a small dict (or raises FeedError). `src` = attribution to show.
# ---------------------------------------------------------------------------------------------
_TR = "https://transport.opendata.ch/v1"
_DEP_FIELDS = ("station/name", "stationboard/category", "stationboard/number", "stationboard/to",
               "stationboard/stop/departureTimestamp", "stationboard/stop/delay", "stationboard/stop/platform")
SRC_DEPARTURES = "transport.opendata.ch / opentransportdata.swiss"


def departures(station, limit=15):
    """Next departures of a stop (Swiss public transport). Returns
    {"stop": resolved name, "rows": [{"cat", "line", "to", "ts", "delay", "pl"}], "src"}; `ts` is the
    scheduled departure (UTC epoch), `delay` minutes. Sorted by scheduled time."""
    station = str(station).strip()[:40]
    if not station:
        raise FeedError("no stop")
    url = _TR + "/stationboard?station=" + quote(station) + "&limit=%d" % int(limit)
    for f in _DEP_FIELDS:
        url += "&fields%5B%5D=" + f
    j = get_json(url)
    rows = j.get("stationboard") if isinstance(j, dict) else None
    if rows is None:
        raise FeedError("bad data")
    out = []
    for r in rows:
        try:
            st = r["stop"]
            out.append({"cat": str(r.get("category") or "")[:3], "line": str(r.get("number") or "")[:5],
                        "to": str(r.get("to") or "")[:30], "ts": int(st["departureTimestamp"]),
                        "delay": int(st.get("delay") or 0), "pl": str(st.get("platform") or "")[:4]})
        except (KeyError, TypeError, ValueError):
            continue
    if not out:
        raise FeedError("no departures")
    name = (j.get("station") or {}).get("name") or station
    return {"stop": str(name)[:40], "rows": out, "src": SRC_DEPARTURES}


_geo_cache = {}


def geocode(place):
    """Place name -> {"lat", "lon", "name", "cc"} (Open-Meteo geocoding). Cached in RAM."""
    place = str(place).strip()[:40]
    if not place:
        raise FeedError("no place")
    if place.lower() in _geo_cache:
        return _geo_cache[place.lower()]
    j = get_json("https://geocoding-api.open-meteo.com/v1/search?name=" + quote(place) + "&count=1&format=json")
    res = (j.get("results") if isinstance(j, dict) else None) or []
    if not res:
        raise FeedError("unknown place")
    r = res[0]
    out = {"lat": float(r["latitude"]), "lon": float(r["longitude"]), "name": str(r.get("name") or place)[:30],
           "cc": str(r.get("country_code") or "")}
    _geo_cache[place.lower()] = out
    return out


SRC_WEATHER = "Weather data by Open-Meteo.com"


def weather(place, hours=12):
    """Current weather and the next `hours` hours for a place name. Returns
    {"place", "temp", "code", "wind", "rain", "off", "h": [{"t", "p", "c"}], "src"}
    (temp C, wind km/h, rain mm, `off` = local UTC offset seconds, hourly t = temp, p = rain probability %,
    c = WMO weather code)."""
    g = geocode(place)
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&current=temperature_2m,weather_code,wind_speed_10m,precipitation"
           "&hourly=temperature_2m,precipitation_probability,weather_code&forecast_hours=%d&timezone=auto"
           % (g["lat"], g["lon"], int(hours)))
    j = get_json(url)
    try:
        cur, hr = j["current"], j["hourly"]
        h = []
        for i in range(len(hr["time"])):
            h.append({"t": hr["temperature_2m"][i], "p": hr["precipitation_probability"][i] or 0,
                      "c": hr["weather_code"][i]})
        return {"place": g["name"], "temp": cur["temperature_2m"], "code": int(cur["weather_code"]),
                "wind": cur["wind_speed_10m"], "rain": cur["precipitation"],
                "off": int(j.get("utc_offset_seconds") or 0), "h": h, "src": SRC_WEATHER}
    except (KeyError, TypeError, ValueError):
        raise FeedError("bad data")


ZONES = ("AT", "BE", "CH", "CZ", "DE-LU", "DK1", "DK2", "FR", "HU", "IT-North", "NL", "NO2", "PL", "SE4", "SI")
SRC_POWER = "energy-charts.info (CC BY 4.0)"


def power_price(zone="CH", std_hours=1):
    """Day-ahead wholesale electricity price (Energy-Charts) as hourly averages. Returns
    {"zone", "h0": UTC epoch of the first hour, "p": [ct/kWh per hour], "cur": ct/kWh now, "src"}.
    This is the exchange price (EUR/MWh / 10), NOT a consumer tariff."""
    if zone not in ZONES:
        raise FeedError("zone")
    if not clock_ok():
        raise FeedError("no clock")
    now = int(time.time())
    y, m, d = civil(now + utc_offset(now, std_hours))[:3]
    y2, m2, d2 = civil(now + utc_offset(now, std_hours) + 86400)[:3]
    j = get_json("https://api.energy-charts.info/price?bzn=%s&start=%04d-%02d-%02d&end=%04d-%02d-%02d"
                 % (zone, y, m, d, y2, m2, d2))
    try:
        ts, pr = j["unix_seconds"], j["price"]
        sums, cnt, cur = {}, {}, None
        for i in range(len(ts)):
            if pr[i] is None:
                continue
            v = pr[i] / 10.0
            hr = ts[i] // 3600
            sums[hr] = sums.get(hr, 0.0) + v
            cnt[hr] = cnt.get(hr, 0) + 1
            if ts[i] <= now < ts[i] + 900:
                cur = v
        if not sums:
            raise FeedError("no prices")
        first = min(sums)
        last = max(sums)
        p = []
        for hr in range(first, last + 1):
            p.append(round(sums[hr] / cnt[hr], 2) if hr in sums else None)
        return {"zone": zone, "h0": first * 3600, "p": p, "cur": None if cur is None else round(cur, 2), "src": SRC_POWER}
    except (KeyError, TypeError, ValueError, IndexError):
        raise FeedError("bad data")


# ---------------------------------------------------------------------------------------------
# Feed manager
# ---------------------------------------------------------------------------------------------
class Feed:
    def __init__(self, name, provider, every, params):
        self.name, self.provider, self.every, self.params = name, provider, every, params
        self.data = None            # last GOOD result
        self.ok_at = None           # monotonic time of the last good result
        self.error = None           # last error text (None after a success)
        self.fails = 0
        self.next_at = 0.0

    def age(self, now):
        return None if self.ok_at is None else now - self.ok_at


class Feeds:
    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self.feeds = {}
        self._order = []
        self._rr = 0

    def add(self, name, provider, every=60, **params):
        f = Feed(name, provider, max(10, every), params)
        self.feeds[name] = f
        if name not in self._order:
            self._order.append(name)
        return f

    def update(self, name, **params):
        """Change the parameters (e.g. the stop the user typed): fetch again at the next poll()."""
        f = self.feeds.get(name)
        if f is not None and any(f.params.get(k) != v for k, v in params.items()):
            f.params.update(params)
            f.next_at = 0.0
            f.fails = 0

    def refresh(self, name):
        f = self.feeds.get(name)
        if f is not None:
            f.next_at = 0.0

    def get(self, name):
        f = self.feeds.get(name)
        return None if f is None else f.data

    def error(self, name):
        f = self.feeds.get(name)
        return None if f is None else f.error

    def age(self, name):
        f = self.feeds.get(name)
        return None if f is None else f.age(self._clock())

    def poll(self):
        """Fetch ONE due feed (round robin). Returns its name if it got new data, else None. Never raises."""
        if not self._order:
            return None
        now = self._clock()
        for _ in range(len(self._order)):
            self._rr = (self._rr + 1) % len(self._order)
            f = self.feeds[self._order[self._rr]]
            if now < f.next_at:
                continue
            try:
                f.data = f.provider(**f.params)
                f.ok_at = self._clock()
                f.error = None
                f.fails = 0
                f.next_at = self._clock() + f.every
                return f.name
            except FeedError as e:
                f.error = str(e)
            except Exception as e:                      # a bug in a provider must not stop the product
                f.error = "bug %r" % (e,)
            f.fails += 1
            f.next_at = self._clock() + min(f.every, 15 * (2 ** min(f.fails - 1, 4)))   # 15, 30, 60, ... s
            return None
        return None


def attach(c=None):
    """The feed manager for this product. `c` is the Conductor (unused today, kept for symmetry with tp.attach)."""
    return Feeds()
