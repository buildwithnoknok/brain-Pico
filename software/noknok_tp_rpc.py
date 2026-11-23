# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
"""
noknok_tp_rpc - the /rpc ops for third-party boards (DEV-249), kept OUT of code.py.

Why a separate file (DEV-283): code.py is compiled on the Pico at every boot, and at about
170 KB of source the brain joined WiFi but then answered no ARP / ping / HTTP at all (Pi4RFID,
23 Nov 2026: code.py 0.49 at 167.5 KB worked 3/3 boots, the same file with these ops inside,
173.9 KB, failed 4/4 - also with the ops never registered). This module ships as .mpy
(precompiled), so code.py only grows by the lines that call register().

The app names boards the brain cannot identify, sets options a chip cannot report (OLED
height) and runs the pull-up check. Spec: exploration repo
rdm-13-third-party/proposal/APP-WORK-Sue.md; library: noknok_thirdparty (DEV-248).
IMPORT RULE: neither code.py nor this file imports noknok_thirdparty. Only a product whose
manifest lists a third-party board imports it; the ops use the copy the product already
loaded and answer "no library" otherwise.
What the user names is saved in the Store (key "thirdparty", nvm/FRAM, ADR-003), never the
FAT; tp.attach() applies it on every boot, so it survives a reboot and an OTA.
"""

import sys

__version__ = "1.0.0"

TP_BUSES = ("A", "B")
MIN_BUS_OHMS = 1000          # same limit as noknok_tp_info (3 mA sink at 3.3 V)

# Set by register(): code.py's noknok module, rpc module, log() and event().
_nk = _rpc = None
_log = _event = print


def register(d, nk, rpc, log, event):
    """Add the thirdparty.* ops to code.py's Dispatcher `d`."""
    global _nk, _rpc, _log, _event
    _nk, _rpc, _log, _event = nk, rpc, log, event
    d.register("thirdparty.assign", _h_assign)
    d.register("thirdparty.set", _h_set)
    d.register("thirdparty.forget", _h_forget)
    d.register("thirdparty.check", _h_check)


def _tp_or_error():
    """(library, Conductor) of the running product, or an RpcError for the app."""
    c = _nk.conductor()
    if c is None:
        raise _rpc.RpcError("no conductor")
    tp = sys.modules.get("noknok_thirdparty")
    if tp is None or not hasattr(c, "thirdparty"):
        raise _rpc.RpcError("no library")
    return tp, c


def _target(args):
    """Validated (bus, address) from the op args."""
    bus = args.get("bus")
    if bus not in TP_BUSES:
        raise _rpc.RpcError("bad bus", bus)
    addr = args.get("address")
    if isinstance(addr, bool) or not isinstance(addr, int) or not 0x08 <= addr <= 0x77:
        raise _rpc.RpcError("bad address", addr)
    return bus, addr


def _row(c, bus, addr):
    for r in getattr(c, "thirdparty_rows", None) or []:
        if r.get("bus") == bus and r.get("address") == addr:
            return r
    return None


def _remember(tp, bus, addr, **fields):
    try:
        tp.remember(bus, addr, **fields)
        return True
    except Exception as e:           # works now, but would be lost at the next reboot
        _log("[3p] remember failed: %r" % (e,))
        return False


def _h_assign(args, msg):
    """Name a board: {bus, address, type}. Any registered type is allowed, not only the
    candidates (the user may know better). Saved only after the board answered."""
    tp, c = _tp_or_error()
    bus, addr = _target(args)
    typ = args.get("type")
    if typ not in tp.TYPES:
        raise _rpc.RpcError("unknown type", typ)
    if addr in tp._noknok_addresses(c, bus):
        raise _rpc.RpcError("noknok module", "%s 0x%02x" % (bus, addr))
    if tp.assign(c, typ, bus, addr, log=_log) is None:
        raise _rpc.RpcError("no answer", "%s 0x%02x" % (bus, addr))
    saved = _remember(tp, bus, addr, type=typ)
    _event("[3P] %s 0x%02x named %s" % (bus, addr, typ))
    return {"row": _row(c, bus, addr), "saved": saved}


def _h_set(args, msg):
    """Options the chip cannot report. Today only the SSD1306 panel height (32 / 64)."""
    tp, c = _tp_or_error()
    bus, addr = _target(args)
    m = None
    for o in c.thirdparty:
        if o.i2c_bus == bus and o.address == addr:
            m = o
            break
    if m is None:
        raise _rpc.RpcError("not found", "%s 0x%02x" % (bus, addr))
    opts = [k for k in args if k not in ("bus", "address")]
    if opts != ["height"] or m.TYPE != "ssd1306":
        raise _rpc.RpcError("not supported", ",".join(opts) or None)
    h = args.get("height")
    if isinstance(h, bool) or h not in (32, 64):
        raise _rpc.RpcError("bad value", h)
    if h != m.HEIGHT:
        m.set_size(h)
    saved = _remember(tp, bus, addr, height=h)
    c.thirdparty_rows = tp.describe(c)
    return {"row": _row(c, bus, addr), "saved": saved}


def _h_forget(args, msg):
    """Drop what was saved for one board and re-detect, so it falls back to auto-detect
    (or unknown) at once."""
    tp, c = _tp_or_error()
    bus, addr = _target(args)
    had = tp.forget(bus, addr)
    if had:
        tp.attach(c, log=_log)
        _event("[3P] %s 0x%02x naming forgotten" % (bus, addr))
    return {"forgotten": had}


def _h_check(args, msg):
    """Pull-up load per bus + what to change (move a board, cut a jumper). pass = no bus
    below the 1000 ohm limit."""
    tp, c = _tp_or_error()
    budget = tp.pullup_budget(c)
    warnings = tp.pullup_warnings(c)
    buses = {}
    for name, b in budget.items():
        buses[name] = {"total_ohms": b["total_ohms"], "unknown": b["unknown"],
                       "boards": [list(x) for x in b["boards"]]}
    return {"pass": all(b["total_ohms"] >= MIN_BUS_OHMS for b in budget.values()),
            "buses": buses, "warnings": warnings}
