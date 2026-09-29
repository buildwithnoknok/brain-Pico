# usb_sim.py - desktop test for noknok_usb.py (DEV-46), no hardware needed.
#
# Stubs CircuitPython's usb.core / usb_host / board with fake USB devices that
# answer like real noknok modules, then checks:
#   - discover() picks the driver from the 0xF0 type byte, not the PID
#     (8x -> NoknokLEDs, 16x -> NoknokLEDs16, unknown type skipped, bootloader
#     and foreign devices ignored, a slow first answer retried)
#   - the exact bytes every 16x call puts on the wire
#   - status() decoding of the 16-byte GET_STATUS reply (layout v1)
#   - the 8x driver's bytes are unchanged (24-byte 0x04 frame)
#
# Run from the brain-Pico repo root:  python tools/usb_sim.py
# (on the PC use Thonny's python.exe - there is no system Python)

import os
import struct
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "software"))

# -- fake CircuitPython USB stack --------------------------------------------

class FakeDev:
    def __init__(self, serial, mtype=None, pid=0x4E4E, vid=0x1209, silent_polls=0,
                 status=None):
        self.idVendor, self.idProduct = vid, pid
        self.serial_number = serial
        self.mtype = mtype
        self.silent_polls = silent_polls   # ignore the first N 0xF0 queries
        self.status_reply = status
        self.writes = []
        self._pending = b""

    def set_configuration(self):
        pass

    def write(self, ep, data, timeout=None):
        assert ep == 0x02, "wrong OUT endpoint"
        data = bytes(data)
        self.writes.append(data)
        if data == b"\xF0":
            if self.silent_polls:
                self.silent_polls -= 1
                self._pending = b""
            else:
                self._pending = bytes((0x4E, 0x4E, self.mtype))
        elif data == b"\xB1":
            self._pending = bytes((1, 2, 2, 0))
        elif data == b"\x30" and self.status_reply:
            self._pending = self.status_reply
        else:
            self._pending = b""

    def read(self, ep, buf, timeout=None):
        assert ep == 0x83, "wrong IN endpoint"
        if not self._pending:
            raise OSError("timeout")          # like usb.core.USBTimeoutError
        n = min(len(buf), len(self._pending))
        buf[:n] = self._pending[:n]
        self._pending = b""
        return n

BUS = []

usb = types.ModuleType("usb")
usb_core = types.ModuleType("usb.core")
usb_core.find = lambda find_all=False, idVendor=None, idProduct=None: list(BUS)
usb.core = usb_core
usb_host = types.ModuleType("usb_host")
usb_host.Port = lambda dp, dm: object()
board = types.ModuleType("board")
board.GP16, board.GP17 = "GP16", "GP17"
sys.modules.update({"usb": usb, "usb.core": usb_core, "usb_host": usb_host,
                    "board": board})

import noknok_usb as nu                      # noqa: E402
nu.time.sleep = lambda s: None               # run the discovery loop at full speed

# -- checks --------------------------------------------------------------------

passed = failed = 0

def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print("  ok   ", name)
    else:
        failed += 1
        print("  FAIL ", name)

# status reply: 42.6 C, 5040 mV, CC 400/0, budget 450, led 380, flags power+factory,
# thermal 100 %, vbus 100 %
STATUS = struct.pack("<BhHHHHHBBB", 1, 426, 5040, 400, 0, 450, 380, 0x09, 100, 100)
HOT    = struct.pack("<BhHHHHHBBB", 1, 612, 4980, 400, 0, 330, 330, 0x09, 73, 100)
NEG    = struct.pack("<BhHHHHHBBB", 1, -35, 5000, 0, 0, 0, 0, 0x02, 0, 100)

print("discover()")
d8   = FakeDev("AAAA8X", mtype=0x04)
d16  = FakeDev("BBBB16X", mtype=0x06, silent_polls=2, status=STATUS)
dnew = FakeDev("CCCCNEW", mtype=0x09)
dbl  = FakeDev("DDDDBL", pid=0x4E42)
dfor = FakeDev("EEEEFOREIGN", vid=0x2E8A, mtype=0x04)
BUS[:] = [d8, d16, dnew, dbl, dfor]
found = {s: (t, m) for s, t, m in nu.discover()}
check("8x found as NoknokLEDs",
      found.get("aaaa8x", (None, None))[0] == "noknokleds"
      and type(found["aaaa8x"][1]) is nu.NoknokLEDs)
check("16x found as NoknokLEDs16 (after 2 silent polls)",
      found.get("bbbb16x", (None, None))[0] == "noknokleds16"
      and type(found["bbbb16x"][1]) is nu.NoknokLEDs16)
check("unknown type 0x09 skipped", "ccccnew" not in found)
check("unknown type probed only once", dnew.writes.count(b"\xF0") == 1)
check("bootloader PID ignored", "ddddbl" not in found and not dbl.writes)
check("foreign VID ignored", "eeeeforeign" not in found and not dfor.writes)
check("exactly two modules", len(found) == 2)
check("firmware version read", found["bbbb16x"][1].firmware_version == "2.2.0")

print("find()")
BUS[:] = [FakeDev("AAAA8X", mtype=0x04), FakeDev("BBBB16X", mtype=0x06)]
check("NoknokLEDs.find() skips the 16x", nu.NoknokLEDs.find().__class__ is nu.NoknokLEDs
      and nu.NoknokLEDs.find()._dev.serial_number == "AAAA8X")
check("NoknokLEDs16.find() skips the 8x",
      nu.NoknokLEDs16.find()._dev.serial_number == "BBBB16X")
BUS[:] = [FakeDev("AAAA8X", mtype=0x04)]
check("NoknokLEDs16.find() -> None with only an 8x", nu.NoknokLEDs16.find() is None)

print("16x wire bytes")
dev = FakeDev("X", mtype=0x06, status=STATUS)
L = nu.NoknokLEDs16(dev)

def last():
    return dev.writes[-1]

L.set_all(1, 2, 3);                 check("set_all RGB -> 0x11 w=0", last() == bytes((0x11, 1, 2, 3, 0)))
L.set_all(1, 2, 3, w=4);            check("set_all RGBW -> 0x11",    last() == bytes((0x11, 1, 2, 3, 4)))
L.set_pixel(15, 9, 8, 7, w=300);    check("set_pixel clamps w",      last() == bytes((0x12, 15, 9, 8, 7, 255)))
L.fill(0xFF8800, w=5);              check("fill hex + w",            last() == bytes((0x11, 255, 0x88, 0, 5)))
L.white(200);                       check("white()",                 last() == bytes((0x11, 0, 0, 0, 200)))
L.set_all_pixels([(1, 2, 3), (4, 5, 6, 7)])
f = last()
check("set_all_pixels -> 0x14 + 64 bytes", f[0] == 0x14 and len(f) == 65)
check("set_all_pixels mixes RGB/RGBW + pads",
      f[1:9] == bytes((1, 2, 3, 0, 4, 5, 6, 7)) and f[9:] == bytes(56))
L.set_all_pixels([(9, 9, 9, 9)] * 20)
check("set_all_pixels truncates at 16", len(last()) == 65)
L.set_led("all", 1, 2, 3, brightness=128, duration_ms=1000, w=9)
check("set_led -> 0x18", last() == bytes((0x18, 0xFF, 1, 2, 3, 9, 128, 0xE8, 0x03)))
L.play_preset(L.PRESET_SUNDOWN, speed=30, w=255)
check("play_preset -> 0x21", last() == bytes((0x21, 6, 30, 0, 0, 0, 255)))
L.set_brightness(77);               check("set_brightness unchanged 0x03", last() == bytes((0x03, 77)))
L.off();                            check("off unchanged 0x00",      last() == bytes((0x00,)))
L.role_cue();                       check("role_cue = white die",    last() == bytes((0x11, 0, 0, 0, 255)))
check("identify() true for 0x06", L.identify())

print("status()")
s = L.status()
check("status sent 0x30", dev.writes[-1] == b"\x30")
check("temp 42.6", s["temp_c"] == 42.6)
check("vbus/cc/budget/led", (s["vbus_mv"], s["cc1_mv"], s["cc2_mv"], s["budget_ma"],
                             s["led_ma"]) == (5040, 400, 0, 450, 380))
check("flags", s["led_power"] and s["factory_cal"] and not s["thermal_cut"]
      and not s["vbus_cut"])
check("not limited at 100 %", s["limited"] is False)
check("temperature()", L.temperature() == 42.6)
dev.status_reply = HOT
s = L.status()
check("hot: limited when thermal < 100", s["limited"] and s["thermal_pct"] == 73
      and s["temp_c"] == 61.2)
dev.status_reply = NEG
s = L.status()
check("negative temp + thermal cut", s["temp_c"] == -3.5 and s["thermal_cut"]
      and s["limited"] and not s["led_power"])
dev.status_reply = STATUS[:10]
check("short reply -> None", L.status() is None)
dev.status_reply = None
check("no reply -> None", L.status() is None and L.temperature() is None)

print("8x unchanged")
dev8 = FakeDev("Y", mtype=0x04)
E = nu.NoknokLEDs(dev8)
E.set_all(1, 2, 3);                  check("8x set_all -> 0x01", dev8.writes[-1] == bytes((1, 1, 2, 3)))
E.set_all_pixels([(1, 2, 3)])
check("8x set_all_pixels -> 0x04 + 24 bytes", dev8.writes[-1][0] == 0x04
      and len(dev8.writes[-1]) == 25)
E.play_preset(6, 30, 0, 0, 255);     check("8x play_preset -> 0x20",
                                           dev8.writes[-1] == bytes((0x20, 6, 30, 0, 0, 255)))
check("8x has no status()", not hasattr(E, "status"))

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
