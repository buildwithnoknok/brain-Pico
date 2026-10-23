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
#   - DEV-150: SCAN_PASSES scans merged before the hotspot; a name refused as
#     not visible is tried for real when asked again in the same boot
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
    """What run_ap_provisioning() does before start_ap(): the station-mode scan
    (SCAN_PASSES passes, DEV-150)."""
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
P = ns["SCAN_PASSES"]
check("DEV-150: %d scan passes before the hotspot starts" % P, P >= 2 and Radio.scans == P)
r = ns["_h_wifi_scan"]({}, {})
names = [n["ssid"] for n in r["networks"]]
check("available, strongest first, deduplicated", r["available"] and names == ["Home", "Cafe"], names)
check("own hotspot and hidden (empty) names left out", "noknok-setup" not in names and "" not in names)
check("strongest reading kept for a repeated name", r["networks"][0]["rssi"] == -48)
check("open flag", r["networks"][1]["open"] is True and r["networks"][0]["open"] is False)
check("DEV-130: served from the cache: live false, age_s, no new scan",
      r["live"] is False and "age_s" in r and Radio.scans == P, r)
Radio.ap_active = False
r = ns["_h_wifi_scan"]({}, {})
check("home WiFi (no AP): live scan (one pass)", r["live"] is True and Radio.scans == P + 1, r)
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
check("no provision path ever scanned with the AP up", Radio.scans == scans + P)

print("\nDEV-136: one scan per boot, none after a failed join")
ns["_scan_at"] = None
ns["_scan_cache"] = None
Radio.scan_fails = False
Radio.scan = [Net("Home", -50), Net("Cafe", -60)]
Radio.ap_active = False                    # station mode, before the first start_ap()
n0 = Radio.scans
ns["_scan_before_ap"]()
check("first hotspot start of the boot: one scan (P passes)", Radio.scans == n0 + P)
Radio.scan = [Net("Other", -40)]           # what a new scan WOULD see now
ns["_scan_before_ap"]()                    # hotspot restart after a failed join
ns["_scan_before_ap"]()                    # ... and another one
check("restarts after a failed join: no scan at all", Radio.scans == n0 + P)
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
check("boot scan failed: no retry after a failed join either", Radio.scans == n0 + P)
Radio.ap_active = True
r = provision("Anything")
check("  ...no list -> pre-check skipped, nothing refused", r.get("accepted") is True, r)

print("\nDEV-150: a missed scan does not block setup")
real_start = Radio.start_scanning_networks
seq = []


def _start(self):
    """Each pass yields the next list from `seq`; None = that pass fails."""
    Radio.scans += 1
    lst = seq.pop(0) if seq else []
    if lst is None:
        raise RuntimeError("scan failed")
    return iter(lst)


ns["_refused_once"].clear()
Radio.scan_fails = False
Radio.start_scanning_networks = _start
Radio.ap_active = False
seq[:] = [[Net("Cafe", -60)], [Net("Home", -45), Net("Cafe", -70)]]
ns["_refresh_scan_cache"]()
names = [n["ssid"] for n in ns["_scan_cache"]]
check("network seen by only the 2nd pass is in the list", names == ["Home", "Cafe"], names)
check("  ...strongest reading per name across passes",
      ns["_scan_cache"][1]["rssi"] == -60, ns["_scan_cache"])
seq[:] = [None, [Net("Home", -45)]]
ns["_refresh_scan_cache"]()
check("one pass failing, the other OK -> its list is used",
      [n["ssid"] for n in (ns["_scan_cache"] or [])] == ["Home"], ns["_scan_cache"])
seq[:] = [None, None]
ns["_refresh_scan_cache"]()
check("both passes failing -> no list (pre-check skipped, as before)", ns["_scan_cache"] is None)
seq[:] = [[Net("Cafe", -60)], [Net("Cafe", -61)]]   # both passes miss "Home"
ns["_refresh_scan_cache"]()
Radio.start_scanning_networks = real_start
Radio.ap_active = True
scans = Radio.scans
r = provision("Home")
check("first ask for a missed name -> refused, network_not_found (as DEV-100)",
      r.get("accepted") is False and r.get("reason") == "network_not_found"
      and pending["ready"] is False, r)
r = provision("Home")
check("same name asked again -> tried for real (join loop armed)",
      r.get("accepted") is True and pending["ready"] is True and pending["ssid"] == "Home", r)
check("  ...still no scan with the AP up", Radio.scans == scans)
r = provision("Other")
check("a different unseen name is refused on its first ask", r.get("accepted") is False, r)
r = provision("Cafe")
check("a visible name is accepted on the first ask", r.get("accepted") is True, r)

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

print("\nDEV-141: app-chosen join_id, received, ack")
Radio.scan_fails = False
Radio.scan = [Net("Home", -50)]
before_ap()


def provision_with(join_id):
    pending["ready"] = False
    return ns["_do_provision"]("Home", "pw", "https://x/p.py", None, "lamp", None,
                               False, join_id)

sys.modules["microcontroller"].cpu = types.SimpleNamespace(uid=b"\x01\x02")
Radio.connected = False
for fn in ("_device_state", "_config_status", "load_wifi_credentials"):
    ns[fn] = lambda *a, **k: None

r = provision_with("app-1a2b3c4d")
check("app join_id is used and echoed", r.get("join_id") == "app-1a2b3c4d", r)
check("  ...join loop armed with it, not acked yet",
      pending["join_id"] == "app-1a2b3c4d" and pending["acked"] is False)
h = ns["_h_hello"]({}, {})
check("hello.setup.received = last accepted join_id",
      h["setup"]["received"] == "app-1a2b3c4d", h["setup"])
for bad in ("x" * 33, "a b", "a/../b", "", 42, None):
    r = provision_with(bad)
    check("unsafe/absent join_id %-12r -> brain makes its own" % (bad if bad != "x" * 33 else "33 chars",),
          r.get("join_id") and r["join_id"] != bad and r["join_id"].startswith(ns["_JOIN_BOOT"]), r)
r = provision_with("app-99")
ns["_h_hello"]({"ack": "app-other"}, {})
check("hello with another join_id does not ack", pending["acked"] is False)
ns["_h_hello"]({}, {})
check("plain hello does not ack", pending["acked"] is False)
ns["_h_hello"]({"ack": "app-99"}, {})
check("hello ack = our join_id -> acked", pending["acked"] is True)
r = provision_with("app-100")
check("next provision resets the ack", pending["acked"] is False)
pending["ready"] = False
ns["_h_hello"]({"ack": "app-100"}, {})
check("ack ignored when no join is pending", pending["acked"] is False)
check("REPLY_GRACE_S covers one lwIP retransmit (>= 4 s)", ns["REPLY_GRACE_S"] >= 4)

print("\nsetup-hotspot DHCP lease on the home network (DEV-113)")
class ApInfo:
    bssid, channel, rssi = bytes([0xC4, 0xEB, 0x43, 4, 0x46, 0x52]), 1, -55
leases = []
def _connect(ssid, pw, timeout=None):
    ip, gw = leases.pop(0)
    Radio.ipv4_address, Radio.ipv4_gateway, Radio.ipv4_dns = ip, gw, gw
Radio.connect = staticmethod(_connect)
Radio.ap_info = ApInfo()
Radio.station_stops = 0
def _stop_station():
    Radio.station_stops += 1
Radio.stop_station = staticmethod(_stop_station)
events_seen = []
ns["event"] = events_seen.append
ns["time"].sleep = lambda s: None
logged = []
ns["log"] = logged.append
Radio.ap_active = False
leases[:] = [("192.168.4.16", "192.168.4.1"), ("192.168.1.242", "192.168.1.1")]
ok = ns["connect_wifi"]("Home", "pw")
check("rogue lease -> rejoin, router lease kept", ok and Radio.ipv4_address == "192.168.1.242"
      and Radio.station_stops == 1 and not events_seen, Radio.ipv4_address)
check("gateway / DNS / AP bssid logged at connect",
      any("gateway 192.168.1.1" in l and "C4:EB:43:04:46:52" in l for l in logged))
class _NoApInfo:
    def __get__(self, obj, typ=None):
        raise NotImplementedError()
Radio.ap_info = _NoApInfo()                   # Pico 2 W, CircuitPython 10.3.1
del logged[:]
leases[:] = [("192.168.1.242", "192.168.1.1")]
ns["connect_wifi"]("Home", "pw")
check("Pico 2 W (ap_info not implemented): gateway + DNS still logged",
      any("gateway 192.168.1.1" in l and "DNS 192.168.1.1" in l for l in logged), logged[-1:])
Radio.ap_info = ApInfo()
leases[:] = [("192.168.4.16", "192.168.4.1")] * 3
Radio.station_stops = 0
ok = ns["connect_wifi"]("Home", "pw")
check("rogue lease every time -> joined, %d rejoins, one event" % Radio.station_stops,
      ok and Radio.station_stops == ns["ROGUE_LEASE_TRIES"] - 1 and len(events_seen) == 1)
leases[:] = [("192.168.4.20", "192.168.4.254")]
del events_seen[:]; Radio.station_stops = 0
ok = ns["connect_wifi"]("Home", "pw")
check("a home network that really is 192.168.4.x (other gateway) is accepted",
      ok and Radio.station_stops == 0 and not events_seen)
calls = []
Radio.stop_ap = staticmethod(lambda: calls.append("stop_ap"))
Radio.stop_dhcp_ap = staticmethod(lambda: calls.append("stop_dhcp_ap"))
ns["_stop_ap"]()
check("_stop_ap also stops the hotspot's DHCP server", calls == ["stop_ap", "stop_dhcp_ap"], calls)
del Radio.stop_dhcp_ap
calls[:] = []
ns["_stop_ap"]()
check("_stop_ap without stop_dhcp_ap (older CircuitPython) still stops the AP", calls == ["stop_ap"])

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
