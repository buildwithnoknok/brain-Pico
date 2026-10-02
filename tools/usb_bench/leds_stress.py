# SPDX-License-Identifier: MIT
# leds_stress.py - run ON THE PICO: USB receive stress test for the noknok LEDs (8x RGB).
# Sends N 25-byte 0x04 frames back-to-back, packed into large bulk writes (many USB
# packets per write), then prints the frame the ring MUST show. Firmware <= 1.8.1
# drops bytes under this load (dark / scrambled ring); >= 1.8.2 must match exactly.
import time
import noknok_usb

N = 300
PER_WRITE = 20                      # frames per bulk write = 500 bytes = 8 USB packets

mods = noknok_usb.discover()
serial, kind, m = mods[0]
print("module", serial, "version", m.version())
m._send((0x03, 60))                 # ~25 % brightness
time.sleep(0.05)

cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 160, 0)]
names = ["red", "green", "blue", "yellow"]

def frame(k):
    f = bytearray([0x04])
    for i in range(8):
        f += bytes(cols[(i // 2 + k) % 4])
    return f

t0 = time.monotonic()
k = 0
while k < N:
    blob = bytearray()
    for _ in range(min(PER_WRITE, N - k)):
        blob += frame(k)
        k += 1
    m._send(blob)
dt = time.monotonic() - t0
time.sleep(0.2)
print("sent %d frames in %.2f s, version after: %s" % (N, dt, m.version()))
last = N - 1
print("EXPECTED: " + ", ".join("LEDs %d-%d %s" % (2 * b, 2 * b + 1, names[(b + last) % 4]) for b in range(4)))
