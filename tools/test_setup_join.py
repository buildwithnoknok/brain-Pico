# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_setup_join.py - DEV-100, tier S (host Python, no hardware).
#
# The setup-time WiFi join feedback in code.py:
#   - wifi.scan lists visible networks, strongest first, deduplicated, without
#     the brain's own hotspot; "available": false when scanning is impossible
#   - provision on the hotspot refuses a network that is not visible
#     (network_not_found, credentials NOT handed to the join loop), accepts a
#     visible one with a join_id, skips the check with force=True, and does not
#     refuse anything when the scan itself fails
#   - DEV-130: NO live scan while the hotspot runs; the list is scanned once
#     before start_ap() and wifi.scan / the provision pre-check read that cache
#     (live false + age_s); a scan that trickles results stops after its budget
#   - join errors are classified: auth / not_found / timeout / other
#   - a failed join is remembered (RAM) with the request's join_id and alerts
#
# Run from the repo root:   python -I tools/test_setup_join.py
# code.py is loaded with its hardware/network imports stubbed and WITHOUT the
# final main() call, so nothing boots.

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SW = os.path.join(HERE, "..", "software")
sys.path.insert(0, SW)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class _Any:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Any()
    def __getattr__(self, n): return _Any()


class Net:
    def __init__(self, ssid, rssi, open_=False):
        self.ssid, self.rssi = ssid, rssi
        self.authmode = (AuthMode.OPEN,) if open_ else (AuthMode.WPA2,)


class AuthMode:
    OPEN, WPA2 = "OPEN", "WPA2"


class Radio:
    ap_active = True
    scan = []                # what start_scanning_networks yields
    scan_fails = False
    stopped = 0

    scans = 0

    def start_scanning_networks(self):
        Radio.scans += 1
        if Radio.scan_fails:
            raise RuntimeError("Wifi is in access point mode.")
        return iter(Radio.scan)

    def stop_scanning_networks(self):
        Radio.stopped += 1


for name in ("busio", "board", "digitalio", "socketpool", "ssl", "adafruit_requests"):
    stub(name)
stub("microcontroller", nvm=bytearray(4096))
stub("supervisor", reload=lambda: None, runtime=_Any())
stub("wifi", radio=Radio(), AuthMode=AuthMode)
stub("adafruit_connection_manager")
stub("adafruit_httpserver", Server=_Any, Request=_Any, Response=_Any, POST="POST")

src = open(os.path.join(SW, "code.py"), encoding="utf-8").read().rstrip()
assert src.endswith("main()"), "code.py no longer ends with main() - update this test"
ns = {"__name__": "code_under_test"}
exec(compile(src[: -len("main()")], "code.py", "exec"), ns)
ns["log"] = lambda msg: None

fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-64s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails += 1


def before_ap():
    """What run_ap_provisioning() does before start_ap(): one station-mode scan."""
    Radio.ap_active = False
    ns["_refresh_scan_cache"]()
    Radio.ap_active = True


print("wifi.scan")
Radio.scan = [Net("Home", -70), Net("Home", -48), Net("noknok-setup", -20, True),
              Net("Cafe", -60, True), Net("", -30)]
Radio.scan_fails = False
Radio.ap_active = True
Radio.scans = 0
r = ns["_scan_networks"]()
check("DEV-130: _scan_networks refuses while the hotspot runs", r is None and Radio.scans == 0, r)
r = ns["_h_wifi_scan"]({}, {})
check("DEV-130: no list yet + AP up -> available false, radio untouched",
      r["available"] is False and Radio.scans == 0, r)
before_ap()
check("one scan before the hotspot starts", Radio.scans == 1)
r = ns["_h_wifi_scan"]({}, {})
names = [n["ssid"] for n in r["networks"]]
check("available, strongest first, deduplicated", r["available"] and names == ["Home", "Cafe"], names)
check("own hotspot and hidden (empty) names left out", "noknok-setup" not in names and "" not in names)
check("strongest reading kept for a repeated name", r["networks"][0]["rssi"] == -48)
check("open flag", r["networks"][1]["open"] is True and r["networks"][0]["open"] is False)
check("DEV-130: served from the cache: live false, age_s, no new scan",
      r["live"] is False and "age_s" in r and Radio.scans == 1, r)
Radio.ap_active = False
r = ns["_h_wifi_scan"]({}, {})
check("home WiFi (no AP): live scan", r["live"] is True and Radio.scans == 2, r)
Radio.scan_fails = True
before_ap()
r = ns["_h_wifi_scan"]({}, {})
check("scan impossible -> available false, empty list",
      r["available"] is False and r["networks"] == [], r)

print("\nscan time budget (DEV-130)")


class _Clock:
    t = 0.0

    @staticmethod
    def monotonic():
        return _Clock.t


def trickle():
    for i in range(100):
        _Clock.t += 1.0                    # each result arrives one second later
        yield Net("N%d" % i, -50 - i)


real_time = ns["time"]
ns["time"] = _Clock
Radio.scan_fails = False
Radio.ap_active = False
Radio.scan = trickle()
r = ns["_scan_networks"]()
check("results that keep trickling stop after SCAN_BUDGET_S",
      r is not None and 0 < len(r) <= ns["SCAN_BUDGET_S"] + 1, len(r or []))
ns["time"] = real_time
Radio.ap_active = True

print("\nprovision on the hotspot")
pending = ns["pending"]


def provision(ssid, force=False):
    pending["ready"] = False
    pending.pop("join_id", None)
    return ns["_do_provision"](ssid, "pw", "https://x/p.py", None, "lamp", None, force)

Radio.scan_fails = False
Radio.scan = [Net("Home", -50)]
before_ap()
scans = Radio.scans
r = provision("Hoem")
check("typo / not visible -> accepted false, network_not_found",
      r.get("accepted") is False and r.get("reason") == "network_not_found", r)
check("  ...credentials NOT handed to the join loop", pending["ready"] is False)
check("  ...the visible networks come back for the app", [n["ssid"] for n in r["networks"]] == ["Home"])
check("  ...DEV-130: checked against the cached list, no scan with the AP up", Radio.scans == scans)
r = provision("Home")
check("visible -> accepted with a join_id + uptime",
      r.get("accepted") is True and r.get("join_id") and "uptime" in r, r)
check("  ...join loop armed with the same join_id",
      pending["ready"] is True and pending["join_id"] == r["join_id"])
first = r["join_id"]
r2 = provision("Home")
check("each request gets a new join_id", r2["join_id"] != first)
r = provision("HiddenNet", force=True)
check("force=True skips the check (hidden network)", r.get("accepted") is True)
Radio.scan_fails = True
before_ap()
r = provision("Anything")
check("scan impossible -> nothing refused", r.get("accepted") is True)
check("no provision path ever scanned with the AP up", Radio.scans == scans + 1)

print("\nDEV-136: one scan per boot, none after a failed join")
ns["_scan_at"] = None
ns["_scan_cache"] = None
Radio.scan_fails = False
Radio.scan = [Net("Home", -50), Net("Cafe", -60)]
Radio.ap_active = False                    # station mode, before the first start_ap()
n0 = Radio.scans
ns["_scan_before_ap"]()
check("first hotspot start of the boot: one scan", Radio.scans == n0 + 1)
Radio.scan = [Net("Other", -40)]           # what a new scan WOULD see now
ns["_scan_before_ap"]()                    # hotspot restart after a failed join
ns["_scan_before_ap"]()                    # ... and another one
check("restarts after a failed join: no scan at all", Radio.scans == n0 + 1)
Radio.ap_active = True
r = ns["_h_wifi_scan"]({}, {})
check("  ...the boot list is still served", [n["ssid"] for n in r["networks"]] == ["Home", "Cafe"], r)
ns["_scan_at"] = None
ns["_scan_cache"] = None
Radio.scan_fails = True
Radio.ap_active = False
n0 = Radio.scans
ns["_scan_before_ap"]()
Radio.scan_fails = False
ns["_scan_before_ap"]()
check("boot scan failed: no retry after a failed join either", Radio.scans == n0 + 1)
Radio.ap_active = True
r = provision("Anything")
check("  ...no list -> pre-check skipped, nothing refused", r.get("accepted") is True, r)

print("\njoin errors")
jr = ns["_join_reason"]
cases = [("ConnectionError: Authentication failure", "auth"),
         ("ConnectionError: No network with that ssid", "not_found"),
         ("TimeoutError: timed out", "timeout"),
         ("ConnectionError: Unknown failure 205", "other"),
         (None, "other")]
for err, want in cases:
    check("%-44r -> %s" % (err, want), jr(err) == want, jr(err))

print("\na failed join is remembered and signalled")
alerts = []
ns["alert_customer"] = lambda: alerts.append(list(ns["_field_alerts"]))
blinks = []
ns["_blink_onboard_led"] = lambda *a, **k: blinks.append(1)
pending["join_id"] = "abcd-7"
ns["_join_failed"]("Home", "auth", 1, "ConnectionError: Authentication failure")
lj = ns["_last_join"]
check("last_join carries the request's join_id, ok false, reason",
      lj["join_id"] == "abcd-7" and lj["ok"] is False and lj["reason"] == "auth", lj)
check("module alert + onboard LED", alerts and "wifi join failed: auth" in alerts[-1] and blinks)

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
