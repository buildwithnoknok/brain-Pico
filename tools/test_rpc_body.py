# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_rpc_body.py - DEV-110, tier S (host Python, no hardware).
#
# HttpCarrier._full_body() reads the rest of a request body that did not arrive with the
# headers, within BODY_GRACE. Proves: a body that is "not there yet" (EAGAIN / timed out)
# for a while is still read in full; a body in several pieces is joined; a closed
# connection or a real error ends the wait early; a body that never comes is given up after
# BODY_GRACE (not before, not much after) with a log line that says why; a complete body
# never touches the connection.
#
# Run from the repo root:   python -I tools/test_rpc_body.py

import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))
for name in ("microcontroller", "wifi", "socketpool", "mdns"):
    sys.modules.setdefault(name, types.ModuleType(name))
import noknok_rpc   # noqa: E402

BODY = b'{"id": 1, "op": "hello", "args": {}}xx'          # 38 bytes, ~the 10 Oct request (39)
fails = 0


def check(name, cond, extra=""):
    global fails
    print("  %-62s %s %s" % (name, "PASS" if cond else "FAIL", extra if not cond else ""))
    if not cond:
        fails += 1


class Conn:
    """Script of what recv_into does: bytes, an OSError errno, or b'' (closed)."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def settimeout(self, t):
        pass

    def recv_into(self, buf):
        self.calls += 1
        if not self.script:
            raise OSError(11)                              # nothing ever arrives
        step = self.script.pop(0)
        if isinstance(step, int):
            raise OSError(step)
        if isinstance(step, Exception):
            raise step
        buf[:len(step)] = step
        return len(step)


def req(arrived, conn):
    return types.SimpleNamespace(body=arrived, headers={"Content-Length": str(len(BODY))},
                                 connection=conn)


logs = []
c = noknok_rpc.HttpCarrier(logfn=logs.append)

print("DEV-110: reading the rest of a late request body")
conn = Conn([11, 11, 116, 110, BODY])
check("'not there yet' x4, then the body -> full body", c._full_body(req(b"", conn)) == BODY)
check("  ...no 'short body' line", not logs, logs)
conn = Conn([BODY[:10], 11, BODY[10:25], BODY[25:]])
check("body in three pieces -> joined", c._full_body(req(b"", conn)) == BODY)
conn = Conn([])
check("complete body with the headers -> connection not touched",
      c._full_body(req(BODY, conn)) == BODY and conn.calls == 0)

logs.clear()
conn = Conn([BODY[:5], b""])
got = c._full_body(req(b"", conn))
check("peer closed the connection -> stops at once, short body logged",
      got == BODY[:5] and logs and "connection closed" in logs[-1], logs)
logs.clear()
conn = Conn([OSError(104)])
got = c._full_body(req(b"", conn))
check("real error (reset by peer) -> stops, logged with the error",
      got == b"" and logs and "error" in logs[-1], logs)

logs.clear()
c.BODY_GRACE = 0.3
conn = Conn([])                                            # EAGAIN forever
t0 = time.monotonic()
got = c._full_body(req(b"", conn))
dt = time.monotonic() - t0
check("never arrives -> gives up after BODY_GRACE, not before",
      got == b"" and 0.28 <= dt < 0.6, "%.2f s" % dt)
check("  ...log says 0 of N, the time waited and 'time up'",
      logs and ("0 of %d" % len(BODY)) in logs[-1] and "time up" in logs[-1] and "after 0.3" in logs[-1], logs)
check("  ...kept trying during the grace (not one attempt only)", conn.calls > 3, conn.calls)

print("\n%s (%d failure%s)" % ("PASS" if not fails else "FAIL", fails, "" if fails == 1 else "s"))
sys.exit(1 if fails else 0)
