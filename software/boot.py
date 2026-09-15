# boot.py — runs once at power-up, before code.py. Its only job: decide who
# may write the CIRCUITPY filesystem (DEV-18).
#
# Why this matters: the CIRCUITPY drive is a FAT filesystem on raw flash with
# no journal. A power cut in the middle of a write can corrupt it — taking
# unrelated files (product.py) with it — and unplugging is how a noknok product
# is switched off. CircuitPython allows exactly one writer at a time: either a
# PC (drive visible) or the program (drive hidden). Policy:
#
#   Shipped brain (settings.toml: NOKNOK_USB_DRIVE = 0)
#     The drive is hidden from PCs. The filesystem stays READ-ONLY to the
#     program; code.py / noknok.py open short write windows (noknok.writable())
#     only for provisioning, OTA, roles and factory reset — never while
#     product.py runs. Bench file transfers go over the REPL (pico.py put) and
#     are unaffected. Serial console stays available.
#
#   Maker / bench brain (NOKNOK_USB_DRIVE = 1)
#     The drive is visible and a PC may write it. CircuitPython then refuses a
#     runtime remount, so program writes raise OSError — provisioning fails
#     loudly instead of two writers silently sharing one FAT volume.
#     Switch from the REPL:  >>> import noknok; noknok.set_usb_drive(True)
#     then power-cycle. Switch back by editing settings.toml on the PC.
#
#   Fail open
#     If settings.toml has no NOKNOK_USB_DRIVE key, or code.py / noknok.py are
#     missing, or anything here raises, the drive stays VISIBLE. A damaged
#     brain therefore always shows up on a PC and can be recovered (DEV-38:
#     one-file recovery image). This file can never lock a brain out.
#
# Nothing here writes to flash. CircuitPython only rewrites boot_out.txt when
# its content changes, so keep the prints below constant.

import os
import storage

try:
    v = os.getenv("NOKNOK_USB_DRIVE")                      # int 0/1, or None if absent
    hide = v is not None and int(v) == 0                   # absent key -> visible
    for required in ("code.py", "noknok.py"):
        os.stat(required)                                  # missing -> OSError -> visible
    if hide:
        storage.disable_usb_drive()
        # Bench-proven 15 Sep 2026: with the drive disabled CircuitPython makes
        # the filesystem WRITABLE to the program by default (read-only-to-code
        # only exists to let a PC write). So say it explicitly — this line is
        # the whole policy; noknok.writable() lifts it for a few hundred ms.
        storage.remount("/", readonly=True)
        print("noknok boot: CIRCUITPY drive hidden (NOKNOK_USB_DRIVE = 0), filesystem read-only to code")
    else:
        print("noknok boot: CIRCUITPY drive visible (maker mode)")
except Exception as e:
    print("noknok boot: fail-open, drive visible:", e)
