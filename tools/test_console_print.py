# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# test_console_print.py - DEV-178, tier S (host Python, no hardware).
#
# noknok._print() is the brain's console output that never blocks (DEV-172
# workaround B). A fake usb_cdc.console stands in for the USB port. Proves:
#   - import sets write_timeout = 0 (write what fits, never wait)
#   - text is joined like print() (sep, end) and "\n" goes out as "\r\n"
#   - a jammed console (write takes nothing) or one that raises costs no time
#     and raises nothing: the line is just lost
#   - no console (boot.py disabled it: usb_cdc.console is None) -> silently dropped
#   - console_file works as file= for a traceback (what code.py passes)
#   - no print() call is left in noknok.py / noknok_usb.py / noknok_rpc.py code
#     (docstrings and d.print() don't count)
#
# Run from the repo root:   python -I tools/test_console_print.py

import importlib
import io
import os
import sys
import time
import tokenize
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SW = os.path.join(HERE, "..", "software")
sys.path.insert(0, SW)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

board = types.ModuleType("board")
for n in range(29):
    setattr(board, "GP%d" % n, "GP%d" % n)
sys.modules["board"] = board
mc = types.ModuleType("microcontroller")
mc.nvm = bytearray(4096)
sys.modules["microcontroller"] = mc
for name in ("digitalio", "supervisor", "busio"):
    sys.modules[name] = types.ModuleType(name)

failures = 0


def check(what, ok, detail=""):
    global failures
    print("  %s %s%s" % (what, "PASS" if ok else "FAIL", "" if ok else "  (%s)" % (detail,)))
    if not ok:
        failures += 1


class Console:
    def __init__(self, mode="ok"):
        self.mode, self.out, self.write_timeout = mode, b"", None

    def write(self, b):
        if self.mode == "jammed":
            return 0                       # host not reading: nothing fits
        if self.mode == "raise":
            raise OSError(5, "I/O error")
        self.out += bytes(b)
        return len(b)


def load(console):
    cdc = types.ModuleType("usb_cdc")
    cdc.console = console
    sys.modules["usb_cdc"] = cdc
    sys.modules.pop("noknok", None)
    return importlib.import_module("noknok")


print("\nnoknok._print: a working console")
con = Console()
nk = load(con)
check("import sets write_timeout = 0", con.write_timeout == 0, con.write_timeout)
nk._print("a", 1, 2.5)
nk._print("x", "y", sep="-", end="")
nk._print("\nz")
check("joined like print(), LF -> CRLF", con.out == b"a 1 2.5\r\nx-y\r\nz\r\n", con.out)

print("\nnoknok._print: a jammed or broken console never blocks or raises")
for mode in ("jammed", "raise"):
    con = Console(mode)
    nk = load(con)
    t0 = time.monotonic()
    try:
        for i in range(1000):
            nk._print("line %d" % i)
        ok = True
    except Exception as e:
        ok = e
    check("%s: 1000 lines, no exception" % mode, ok is True, ok)
    check("%s: and no time spent" % mode, time.monotonic() - t0 < 0.5, time.monotonic() - t0)

print("\nnoknok._print: no console")
nk = load(None)
try:
    nk._print("dropped")
    ok = True
except Exception as e:
    ok = e
check("usb_cdc.console None -> dropped, no exception", ok is True, ok)

print("\nnoknok.console_file: a traceback through the writer")
con = Console()
nk = load(con)
import traceback
try:
    raise ValueError("boom")
except ValueError as e:
    traceback.print_exception(type(e), e, e.__traceback__, file=nk.console_file)
check("traceback text arrives on the console",
      b"ValueError: boom\r\n" in con.out and b"Traceback" in con.out, con.out[-80:])

print("\nno print() left in the library code")
for f in ("noknok.py", "noknok_usb.py", "noknok_rpc.py"):
    src = open(os.path.join(SW, f), encoding="utf-8").read()
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    left = []
    for i, t in enumerate(toks):
        if t.type == tokenize.NAME and t.string == "print" and i + 1 < len(toks) \
                and toks[i + 1].string == "(" and toks[i - 1].string not in (".", "def"):
            left.append(t.start[0])
    # the one allowed: the host fallback inside _print itself
    allowed = 1 if f in ("noknok.py", "noknok_usb.py") else 0
    check("%s: only the host fallback calls print()" % f, len(left) == allowed, left)

print("\n%s (%d failures)" % ("PASS" if not failures else "FAIL", failures))
sys.exit(1 if failures else 0)
