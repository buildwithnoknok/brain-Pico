# SPDX-License-Identifier: MIT
# DEV-45: RP2350-E9 check on the USB host data line. Needs a FRESH boot (no usb_host.Port yet).
# GP0 = D+ (module 1.5k pull-up), GP1 = D-. Internal pull-downs only, as PIO-USB uses them.
# The PicoHub X035 (bus A, address 0x30) switches J2 VBUS off -> the module and its pull-up are gone.
import time, board, busio, digitalio
try:
    import memorymap
    cid = memorymap.AddressRange(start=0x40000000, length=4)[0:4]
    v = cid[0] | cid[1] << 8 | cid[2] << 16 | cid[3] << 24
    print("SYSINFO CHIP_ID 0x%08x -> revision %d" % (v, v >> 28))
except Exception as e:
    print("chip id: n/a", e)
ADDR = 0x30
i2c = busio.I2C(board.GP11, board.GP10, frequency=100000)
while not i2c.try_lock(): pass
def crc8(b):
    c = 0
    for x in b:
        c ^= x
        for _ in range(8): c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c
if ADDR not in i2c.scan():
    t0 = time.monotonic()
    while time.monotonic() - t0 < 5:
        try:
            b = bytearray(10); i2c.readfrom_into(0x7F, b)
            if b[8] == 0x07 and crc8(b[:9]) == b[9]:
                i2c.writeto(0x7F, bytes([0x1D, ADDR])); break
        except OSError: pass
        time.sleep(0.05)
    time.sleep(0.1)
def j2(want):
    i2c.writeto(ADDR, bytes([0x20, 2, want])); time.sleep(0.01)
    r = bytearray(1); i2c.readfrom_into(ADDR, r); return r[0]
dp = digitalio.DigitalInOut(board.GP0); dp.direction = digitalio.Direction.INPUT; dp.pull = digitalio.Pull.DOWN
dm = digitalio.DigitalInOut(board.GP1); dm.direction = digitalio.Direction.INPUT; dm.pull = digitalio.Pull.DOWN
print("J2 on (module powered):   D+ %d  D- %d" % (dp.value, dm.value))
print("J2 off ->", j2(0))
for t in (0.05, 0.2, 1.0, 3.0):
    time.sleep(t)
    print("  after +%.2f s:          D+ %d  D- %d" % (t, dp.value, dm.value))
# drive D+ low for 1 ms, release to the pull-down again
dp.switch_to_output(False); time.sleep(0.001); dp.switch_to_input(pull=digitalio.Pull.DOWN)
time.sleep(0.05)
print("D+ briefly driven low, released:  D+ %d" % dp.value)
time.sleep(1.0)
print("  1 s later:                     D+ %d" % dp.value)
print("J2 auto ->", j2(2))
time.sleep(1.5)
print("J2 back on:               D+ %d  D- %d" % (dp.value, dm.value))
dp.deinit(); dm.deinit(); i2c.unlock()
