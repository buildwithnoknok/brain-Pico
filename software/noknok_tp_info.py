# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
"""noknok_thirdparty helper (not a driver): pull-up budget, warnings, app status rows, address hints.
Imported on demand by the core's wrappers and unloaded again, so it costs RAM only
while it runs (DEV-248, from the RDM-13 exploration)."""

HOST_PULLUP_OHMS = 3300     # noknok host pull-up per bus (MOD-0011 R23/R24, Ecosystem electrical §3)
MIN_BUS_OHMS     = 1000     # ~3 mA sink at 3.3 V: total pull-up must stay above this
NEAR_BUS_OHMS    = 1250     # below this: one more Adafruit-style board (5 k) crosses the limit
# PULLUP_OHMS = the load a board adds when the line is LOW. Boards with a BSS138 level shifter
# (most Adafruit Stemma QT boards) count BOTH resistor pairs in parallel.

HINTS = {0x3C: "SSD1306 OLED?", 0x3D: "SSD1306 OLED?", 0x40: "INA219 / HTU21D?",
         0x44: "SHT4x / SHT3x?", 0x50: "EEPROM / FRAM?", 0x29: "VL53L0X / TSL2591?",
         0x5A: "MPR121 / MLX90614?", 0x76: "BME280 / BMP280?", 0x77: "BME280 / BMP388?"}


# ── Pull-up budget ────────────────────────────────────────────────────────────

def _parallel(values):
    return 1.0 / sum(1.0 / v for v in values) if values else None


def pullup_budget(c):
    """{bus: {"total_ohms", "boards": [(name, ohms, removable)], "unknown": n}}."""
    out = {}
    for bus_name, _ in c._buses():
        boards = [m for m in c.thirdparty if m.i2c_bus == bus_name and m.PULLUP_OHMS]
        values = [HOST_PULLUP_OHMS] + [m.PULLUP_OHMS for m in boards]
        out[bus_name] = {
            "total_ohms": round(_parallel(values)),
            "boards": [(m.NAME, m.PULLUP_OHMS, m.PULLUP_REMOVABLE) for m in boards],
            "unknown": sum(1 for u in c.unknown_devices if u.i2c_bus == bus_name),
        }
    return out


def _total(c, bus_name, without=None, plus=None):
    vals = [HOST_PULLUP_OHMS] + [m.PULLUP_OHMS for m in c.thirdparty
                                 if m.i2c_bus == bus_name and m.PULLUP_OHMS and m is not without]
    if plus is not None:
        vals.append(plus.PULLUP_OHMS)
    return _parallel(vals)


def move_suggestion(c, bus_name):
    """The board to move from `bus_name` to another bus so BOTH buses end up within the
    limit, as (board, target_bus), or None. Skips a move when the board's fixed address
    is already taken on the target bus (two chips on one address = garbage, see
    ASSESSMENT #24). Prefers the move that leaves the most margin."""
    best = None
    for target, tbus in c._buses():
        if target == bus_name:
            continue
        taken = set(c._scan(tbus))
        for m in c.thirdparty:
            if m.i2c_bus != bus_name or not m.PULLUP_OHMS or m.address in taken:
                continue
            here, there = _total(c, bus_name, without=m), _total(c, target, plus=m)
            if here >= MIN_BUS_OHMS and there >= MIN_BUS_OHMS:
                margin = min(here, there)
                if best is None or margin > best[0]:
                    best = (margin, m, target)
    return None if best is None else (best[1], best[2])


def pullup_warnings(c):
    """Human-readable warnings for the app / log. Empty list = fine. Meant to run right
    after a product is installed / boards are plugged in (Christopher, 17 Nov 2026): the
    app shows it and tells the user what to move."""
    w = []
    for bus_name, b in pullup_budget(c).items():
        if b["total_ohms"] < MIN_BUS_OHMS:
            w.append("bus %s: pull-ups total %d ohm (< %d): too strong for 3 mA sink"
                     % (bus_name, b["total_ohms"], MIN_BUS_OHMS))
            mv = move_suggestion(c, bus_name)
            if mv:
                w.append("  -> move %s (0x%02X) to bus %s" % (mv[0].NAME, mv[0].address, mv[1]))
            for m in c.thirdparty:
                if m.i2c_bus == bus_name and m.PULLUP_REMOVABLE:
                    w.append("  -> %s: %s" % (m.NAME, m.PULLUP_HOWTO))
        elif b["total_ohms"] < NEAR_BUS_OHMS:
            w.append("bus %s: pull-ups total %d ohm - at the limit, the next board with pull-ups "
                     "should go on the other bus" % (bus_name, b["total_ohms"]))
        if b["unknown"]:
            w.append("bus %s: %d unknown board(s) - their pull-ups are not counted"
                     % (bus_name, b["unknown"]))
    return w


def describe(c):
    """List of dicts for the app's status reply (same keys as noknok modules + extras)."""
    rows = []
    for m in list(c.thirdparty) + list(c.unknown_devices):
        rows.append({"type": m.TYPE, "uid": m.identity, "fw": None, "bus": m.i2c_bus,
                     "address": m.address, "thirdparty": True,
                     "confidence": getattr(m, "CONFIDENCE", None),
                     "capabilities": getattr(m, "CAPABILITIES", {}),
                     "hint": getattr(m, "hint", None),
                     "candidates": getattr(m, "candidates", None)})
    return rows
