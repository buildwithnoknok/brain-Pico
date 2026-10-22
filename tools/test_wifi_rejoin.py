# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_wifi_rejoin.py - DEV-79 host test of noknok_rpc's link watch: a brain that
# booted offline joins once the router is back, within 15 min, without long
# product stalls; on_up runs once; an offline boot is not counted as a drop.
#
# The radio is simulated with a fake clock. Two radio models, because which one
# the CYW43 on CircuitPython 10.3 follows is a bench question (DEV-79 HANDOFF):
#   "background" - a join started by connect() goes on after connect() times out
#   "foreground" - a join only completes inside connect() (needs >= JOIN_TIME s)
#
#   python tools/test_wifi_rejoin.py      -> "PASS (0 failures)", exit 1 otherwise
import os, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))

JOIN_TIME = 3.6        # s a real join took on the bench (17 Sep 2026)

class Clock:
    t = 0.0
    def monotonic(self):
        return self.t
    def sleep(self, s):
        self.t += s
clock = Clock()

class Radio:
    def __init__(self, model):
        self.model = model
        self.net_at = None          # fake time the router comes back
        self.join_started = None
        self.connected = False
        self.ipv4_address = None
        self.calls = []             # (time, timeout) of each connect()
        self.max_block = 0.0
    def _done(self):
        self.connected = True
        self.ipv4_address = "192.168.1.194"
    def tick(self):                 # the radio's own background work
        # a join started while the router is off gives up after ~2 s (no network)
        if (self.join_started is not None and not self.connected
                and (self.net_at is None or self.net_at > self.join_started + 2)
                and clock.t >= self.join_started + 2):
            self.join_started = None
        if (self.model == "background" and not self.connected and self.join_started is not None
                and self.net_at is not None and clock.t >= max(self.join_started, self.net_at) + JOIN_TIME):
            self._done()
    def connect(self, ssid, password, timeout=None):
        self.calls.append((clock.t, timeout))
        self.join_started = clock.t             # connect() restarts any join
        net = self.net_at is not None and clock.t >= self.net_at
        if net and timeout >= JOIN_TIME:
            clock.t += JOIN_TIME; self.max_block = max(self.max_block, JOIN_TIME)
            self._done(); return
        clock.t += timeout; self.max_block = max(self.max_block, timeout)
        raise ConnectionError("Unknown failure 2" if net else "No network with that ssid")

def fresh(model):
    wifi = types.ModuleType("wifi")
    wifi.radio = Radio(model)
    sys.modules["wifi"] = wifi
    sys.modules.pop("noknok_rpc", None)
    import noknok_rpc as rpc
    rpc.time = clock
    rpc.start_http = lambda *a, **k: None
    rpc._restart_http = lambda: logs.append("carrier restarted")
    rpc._logfn = logs.append
    return rpc, wifi.radio

fails = []
logs = []
def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)

def run(model, net_back_s, minutes=40):
    """Brain boots offline at t=0 (product runs), router back at net_back_s."""
    clock.t = 0.0
    del logs[:]
    rpc, radio = fresh(model)
    ups = []
    rpc.set_wifi_credentials("home", "pw", 80, on_up=lambda: ups.append(clock.t))
    radio.net_at = net_back_s
    joined_at = None
    while clock.t < minutes * 60:
        radio.tick()
        rpc.service(force=True)
        if joined_at is None and rpc._link["was_up"]:
            joined_at = clock.t
        clock.t += 0.05
    return rpc, radio, joined_at, ups

for model in ("background", "foreground"):
    for back in (0.0, 45.0, 400.0, 1000.0):
        rpc, radio, joined, ups = run(model, back)
        late = None if joined is None else joined - back
        check("%s, router back at %4.0f s: joined within 15 min (%s s)"
              % (model, back, "never" if late is None else "%.0f" % late),
              late is not None and late <= 15 * 60)
        check("  ... on_up ran once", len(ups) == 1)
        check("  ... offline boot not counted as a drop", rpc.link_stats()["drops"] == 0)
        if model == "background":
            after = [t for at, t in radio.calls if at >= back]
            check("  ... joined at the first attempt after the router came back",
                  len(after) == 1)

# attempts are spaced by the backoff and at most every 5 min, never in a burst
rpc, radio, joined, ups = run("foreground", 10 ** 9, minutes=60)
gaps = [b[0] - a[0] for a, b in zip(radio.calls, radio.calls[1:])]
check("router never back: attempts spaced 30 s .. 300 s (%s)" % [int(g) for g in gaps[:6]],
      gaps and min(gaps) >= 29 and max(gaps) <= 306)
fulls = sum(1 for _, t in radio.calls if t == rpc.LINK_JOIN_S)
check("router never back: only every 3rd attempt is a full one (%d of %d)" % (fulls, len(radio.calls)),
      fulls == len(radio.calls) // 3)
stall = sum(t for _, t in radio.calls)
# before DEV-79: 15 attempts x 5 s = 75 s per hour, each a 5 s freeze
check("router never back: product stalled %.1f s in 60 min (<= 30 s, was 75 s)" % stall, stall <= 30.0 + 1e-6)
rpc.LINK_FULL_EVERY = 0
rpc2, radio2 = rpc, radio
clock.t = 0.0; del logs[:]
rpc, radio = fresh("background"); rpc.LINK_FULL_EVERY = 0
rpc.set_wifi_credentials("home", "pw", 80)
radio.net_at = 10 ** 9
while clock.t < 3600:
    radio.tick(); rpc.service(force=True); clock.t += 0.05
check("LINK_FULL_EVERY = 0: longest stall %.1f s (kicks only)" % radio.max_block,
      radio.max_block == rpc.LINK_KICK_S)

# a link that drops later is counted and rejoined, on_up runs again
clock.t = 0.0
rpc, radio = fresh("background")
radio._done()
ups = []
rpc.set_wifi_credentials("home", "pw", 80, on_up=lambda: ups.append(clock.t))
radio.net_at = 0.0
for _ in range(1000):
    radio.tick(); rpc.service(force=True); clock.t += 0.05
radio.connected = False; radio.ipv4_address = None; radio.join_started = None
radio.net_at = clock.t + 20
t_drop = clock.t
while clock.t < t_drop + 600:
    radio.tick(); rpc.service(force=True); clock.t += 0.05
check("drop while online: counted once", rpc.link_stats()["drops"] == 1)
check("drop while online: rejoined", radio.connected and rpc._link["was_up"])
check("drop while online: on_up ran once (after the rejoin)", len(ups) == 1)

print("PASS (0 failures)" if not fails else "FAIL (%d failures)" % len(fails))
sys.exit(1 if fails else 0)
