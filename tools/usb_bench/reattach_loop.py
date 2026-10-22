# SPDX-License-Identifier: MIT
# reattach_loop.py - run ON THE PICO (DEV-45): does the PIO-USB host see a noknok USB module
# re-enumerate within ONE host session? Minimal loop, no flash writes:
#
#   app (PID 4E4E) --0xB0--> bootloader (4E42) --BOOT 0x05--> app (4E4E)   x CYCLES
#
# BOOT jumps straight back to the app already in flash, so the module's flash is never touched.
# Only VID/PID are looked at (cached by the host at enumeration): the script NEVER reads a
# string descriptor (serial/product), because on CircuitPython 10.3.x such a read waits with
# no timeout and is the call that hung the host in DEV-45. So this separates "re-attach not
# seen" (a step times out) from "host hang" (the script stops printing).
#
# Exactly one noknok USB module on the host port. Pins from settings.toml (NOKNOK_USB_DP/DM),
# or set DP/DM below. Prints one line per step with the time it took; ends with a summary.
#   ./pico.py run reattach_loop.py 600
import time
import board
import usb.core
import usb_host

CYCLES = 10
STEP_TIMEOUT = 10.0           # s to wait for the module to re-appear with the new PID
DP, DM = None, None           # e.g. board.GP0, board.GP1; None = settings.toml

VID, PID_APP, PID_BL = 0x1209, 0x4E4E, 0x4E42
EP_OUT = 0x02

def _pins():
    if DP is not None:
        return DP, DM
    import os
    dp, dm = os.getenv("NOKNOK_USB_DP"), os.getenv("NOKNOK_USB_DM")
    if not dp or not dm:
        raise SystemExit("no USB host pins: set NOKNOK_USB_DP/DM in settings.toml or DP/DM here")
    return getattr(board, dp), getattr(board, dm)

port = usb_host.Port(*_pins())     # keep referenced: devices drop if the Port is collected

def pids():
    """PIDs of the noknok devices the host currently lists (no transfers)."""
    out = []
    for d in usb.core.find(find_all=True):
        try:
            if d.idVendor == VID:
                out.append(d.idProduct)
        except Exception:
            pass
    return out

def find(pid):
    for d in usb.core.find(find_all=True):
        try:
            if d.idVendor == VID and d.idProduct == pid:
                return d
        except Exception:
            pass
    return None

def wait_for(pid, timeout):
    """Seconds until a device with `pid` is listed AND the other PID is gone, else None.
    Prints every change of the listed PIDs so a stale entry (detach never seen) shows."""
    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 < timeout:
        now = pids()
        if now != last:
            print("      %5.2f s  listed: %s" % (time.monotonic() - t0,
                                                 " ".join("%04X" % p for p in now) or "-"))
            last = now
        if pid in now and len(now) == 1:
            return time.monotonic() - t0
        time.sleep(0.05)
    return None

def send(pid, byte):
    d = find(pid)
    if d is None:
        return False
    try:
        d.set_configuration()
    except Exception:
        pass
    try:
        d.write(EP_OUT, bytes([byte]), timeout=1000)
    except Exception:
        pass        # the module resets at once; the write need not complete
    return True

print("waiting for the app (PID 4E4E) ...")
if wait_for(PID_APP, 15) is None:
    raise SystemExit("no noknok app on the host port - nothing to test")

ok = 0
for n in range(1, CYCLES + 1):
    if not send(PID_APP, 0xB0):
        print("cycle %d: app not listed before 0xB0 - stop" % n)
        break
    t_bl = wait_for(PID_BL, STEP_TIMEOUT)
    print("cycle %d: app -> bootloader %s" % (n, "%.2f s" % t_bl if t_bl is not None else "NOT SEEN"))
    if t_bl is None:
        break
    time.sleep(0.2)
    send(PID_BL, 0x05)
    t_app = wait_for(PID_APP, STEP_TIMEOUT)
    print("cycle %d: bootloader -> app %s" % (n, "%.2f s" % t_app if t_app is not None else "NOT SEEN"))
    if t_app is None:
        break
    ok += 1
    time.sleep(0.5)
print("RESULT %d/%d round trips seen in one host session" % (ok, CYCLES))
