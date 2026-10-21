# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev122_load2.py (Bob, 21 Oct: + reset cause byte and input-current peak between changes) (from bench_dev122_load.py, from bench_dev121_log.py) - DEV-122 electronic-load test: also prints a sample every 3 s.
# bench_dev121_log.py - DEV-121 hands-on tests: logs every CHANGE of the MOD-0011 power manager's
# state with a timestamp (about 10 polls a second) while a person swaps chargers / plugs USB
# devices into J2. Runs ON the Pico (stops the product), talks raw I2C on bus A (GP10/GP11).
# After a power loss the X035 comes up unassigned (0x7F): the logger enumerates it again.
import time
import board
import busio

DURATION = 600
FALLBACK_ADDR = 0x60
i2c = busio.I2C(board.GP11, board.GP10, frequency=100000)
while not i2c.try_lock():
    pass

def crc8(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc

def u16(b, i):
    return b[i] | (b[i + 1] << 8)

def status(addr):
    try:
        i2c.writeto(addr, bytes([0x10]))
        b = bytearray(20)
        i2c.readfrom_into(addr, b)
    except OSError:
        return None
    if crc8(b[:19]) != b[19] or b[0] != 1:      # CRC ok AND format 1 (a Buzzer also answers 0x10 with zeros)
        return None
    return {"pd": b[1], "mv": u16(b, 2), "ma": u16(b, 4), "vin": u16(b, 6), "iin": u16(b, 8),
            "v5": u16(b, 10), "rails": b[14], "inputs": b[15], "faults": b[16], "notices": b[17], "reset": b[18]}

def enumerate_x035(addr):
    try:
        b = bytearray(10)
        i2c.readfrom_into(0x7F, b)
        if b[8] == 0x07 and crc8(b[:9]) == b[9]:
            i2c.writeto(0x7F, bytes([0x1D, addr]))
            return True
    except OSError:
        pass
    return False

def find(known):
    for a in ([known] if known else []) + list(range(0x08, 0x78)):
        if status(a) is not None:
            return a
    return None

def rails_text(r):
    return "".join(n if r & m else "-" for n, m in (("A", 1), ("B", 2), ("J", 4), ("M", 8), ("K", 16)))

t0 = time.monotonic()
addr = find(None)
last, silent, last_tick, peak = None, False, 0.0, 0
print("t=%.1f logger started, power manager at %s" % (0, hex(addr) if addr else "NOT FOUND"))
while time.monotonic() - t0 < DURATION:
    t = time.monotonic() - t0
    s = status(addr) if addr else None
    if s is None:
        if not silent:
            print("t=%.1f X035 silent" % t)
            silent = True
        if enumerate_x035(addr or FALLBACK_ADDR):
            addr = addr or FALLBACK_ADDR
            print("t=%.1f X035 re-enumerated at 0x%02X (unassigned, we gave it back an address)" % (t, addr))
            silent = False
        else:
            a = find(None)
            if a:
                addr = a
        time.sleep(0.05)
        continue
    silent = False
    peak = max(peak, s["iin"])
    if t - last_tick >= 3.0:
        last_tick = t
        print("t=%.1f SAMPLE contract=%dmV/%dmA vin=%dmV iin=%dmA v5=%dmV rails=%s faults=%d notices=0x%02X"
              % (t, s["mv"], s["ma"], s["vin"], s["iin"], s["v5"], rails_text(s["rails"]), s["faults"], s["notices"]))
    key = (s["pd"], s["mv"], s["rails"], s["inputs"], s["faults"], s["notices"], s["reset"])
    if key != last:
        print("t=%.1f pd=%d contract=%dmV/%dmA vin=%dmV v5=%dmV iin=%dmA rails=%s J2-device=%s inputs=0x%02X faults=%d notices=0x%02X reset=0x%02X iin_peak=%d"
              % (t, s["pd"], s["mv"], s["ma"], s["vin"], s["v5"], s["iin"], rails_text(s["rails"]),
                 bool(s["inputs"] & 1), s["inputs"], s["faults"], s["notices"], s["reset"], peak))
        last = key
        peak = 0
    time.sleep(0.05)
print("t=%.1f logger finished" % (time.monotonic() - t0))
