# SPDX-License-Identifier: MIT
# ota_loop.py - run ON THE PICO (DEV-45 acceptance): CYCLES full USB OTA round trips in ONE host
# session, no reset in between:
#
#   app (4E4E) --0xB0--> bootloader (4E42) --ERASE/WRITE/VERIFY/BOOT--> app (4E4E), then 0xB1
#
# Uses noknok_usb.UsbModuleFlasher (on the Pico) with confirm_app=True, so a missed
# re-enumeration shows as "app NOT back". Every cycle erases and rewrites the module's app
# region (~10k erase cycles per page, so 10 cycles cost nothing). Exactly one noknok USB module.
#   ./pico.py put noknok_leds.bin /leds16.bin    (the module's release app image)
#   ./pico.py run ota_loop.py 900
import time
import board
import noknok_usb

IMG = "/leds16.bin"          # <- app image on the Pico (linked at 0x2000, from firmware/bin/)
CYCLES = 10
DP, DM = board.GP0, board.GP1   # MOD-0011 PIO-USB pins; None, None = settings.toml

data = open(IMG, "rb").read()
crc = noknok_usb.crc32(data)
print("image %s: %d bytes, crc32 %08x" % (IMG, len(data), crc))
fl = noknok_usb.UsbModuleFlasher(DP, DM)

def version(dev):
    """0xB1 -> 'maj.min.pat' from the re-enumerated app, or None."""
    try:
        dev.write(0x02, bytes([0xB1]), timeout=1000)
        buf = bytearray(4)
        n = dev.read(0x83, buf, timeout=500)
        return "%d.%d.%d" % (buf[1], buf[2], buf[3]) if n == 4 else None
    except Exception:
        return None

t_dead = time.monotonic() + 15
while fl._find(fl.PID_APP) is None and fl._find(fl.PID_BOOTLOADER) is None:
    if time.monotonic() > t_dead:
        raise SystemExit("no noknok USB module on the host port")
    time.sleep(0.2)

ok = 0
for n in range(1, CYCLES + 1):
    t0 = time.monotonic()
    try:
        dev = fl.flash(data, confirm_app=True)
    except Exception as e:
        print("cycle %d: FAILED after %.1f s: %s" % (n, time.monotonic() - t0, e))
        break
    dt = time.monotonic() - t0
    if dev is None:
        print("cycle %d: flashed + verified, app NOT back within 8 s (%.1f s)" % (n, dt))
        break
    v = version(dev)
    print("cycle %d: OK %.1f s, app back, firmware %s" % (n, dt, v))
    ok += 1
    time.sleep(0.5)
print("RESULT %d/%d OTA round trips in one host session" % (ok, CYCLES))
