# SPDX-License-Identifier: MIT
# j2_cycle_loop.py - run ON THE PICO (DEV-45): does the PIO-USB host see a USB module come back
# after it LOSES POWER, in one host session? Like a user unplugging and re-plugging it, or the
# MOD-0011 power manager switching J2 off (DEV-134). Needs MOD-0011 (X035 on bus A GP10/11).
#
#   CYCLES x: module listed (4E4E) -> CYCLE_RAIL J2 off for OFF_MS -> gone -> back (4E4E)
#
# Only VID/PID are read (no string descriptors). Pass = every cycle sees "gone" AND "back".
#   ./pico.py run j2_cycle_loop.py 300
import time
import board
import busio
import usb.core
import usb_host

CYCLES = 5
OFF_MS = 500
X035 = 0x30                       # address given to the X035 if it still waits at 0x7F
DP, DM = board.GP0, board.GP1

port = usb_host.Port(DP, DM)
i2c = busio.I2C(board.GP11, board.GP10, frequency=100000)
while not i2c.try_lock():
    pass

def crc8(b):
    c = 0
    for x in b:
        c ^= x
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c

if X035 not in i2c.scan():        # unenumerated: answer its 0x7F announce with an address
    t0 = time.monotonic()
    while time.monotonic() - t0 < 5:
        try:
            b = bytearray(10)
            i2c.readfrom_into(0x7F, b)
            if b[8] == 0x07 and crc8(b[:9]) == b[9]:
                i2c.writeto(0x7F, bytes([0x1D, X035]))
                break
        except OSError:
            pass
        time.sleep(0.05)
    time.sleep(0.1)

def listed():
    return [d.idProduct for d in usb.core.find(find_all=True) if d.idVendor == 0x1209]

def wait(cond, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond(listed()):
            return time.monotonic() - t0
        time.sleep(0.05)
    return None

if wait(lambda p: 0x4E4E in p, 15) is None:
    raise SystemExit("no noknok app on J2")
ok = 0
for n in range(1, CYCLES + 1):
    i2c.writeto(X035, bytes([0x21, 2, OFF_MS & 0xFF, OFF_MS >> 8, 0x5A]))   # CYCLE_RAIL J2
    gone = wait(lambda p: not p, 5)
    back = wait(lambda p: 0x4E4E in p, 10) if gone is not None else None
    print("cycle %d: gone %s, back %s" % (n, "%.2f s" % gone if gone is not None else "NOT SEEN",
                                         "%.2f s" % back if back is not None else "NOT SEEN"))
    if back is None:
        break
    ok += 1
    time.sleep(1)
i2c.unlock()
print("RESULT %d/%d power cycles seen in one host session" % (ok, CYCLES))
