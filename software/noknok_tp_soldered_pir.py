# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
"""noknok_thirdparty driver: Soldered PIR motion sensor with easyC (AM312 + ATtiny404).
NEVER auto-detected: the board has no ID and answers any read with its motion byte,
so it is only attached when the app / product names it:
    tp.assign(c, "soldered_pir", "A", 0x30)
(DEV-248, from the RDM-13 exploration)."""
import struct
from noknok_thirdparty import ThirdPartyModule


class SolderedPIR(ThirdPartyModule):
    """Protocol (Soldered MicroPython library, checked 17 Nov 2026): a plain 1-byte read
    = motion state (0/1); a 4-byte little-endian write = hold time in seconds (how long
    the output stays on after motion; default 2 s). No pull-ups on the board (its only
    resistor drives the LED, schematic V1.1.0). Address 0x30, switchable to 0x30-0x37."""
    TYPE, NAME, VENDOR = "soldered_pir", "Motion detector (PIR)", "Soldered easyC"
    ADDRESSES   = tuple(range(0x30, 0x38))
    CONFIDENCE  = "user"          # named by the user / app, never probed
    AUTO        = False
    PULLUP_OHMS = None
    CAPABILITIES = {"motion": "bool"}

    hold_s = None                 # None = leave the board's own setting

    @classmethod
    def identify(cls, i2c, address):
        return False              # cannot be identified from software

    def setup(self):
        if self.hold_s is not None:
            # Re-sent after every recovery: the ATtiny forgets it on power loss and we
            # cannot read it back, so a failed transfer is the only hint we get.
            return self._xfer(struct.pack("<I", int(self.hold_s)))
        return self._xfer(None, bytearray(1))

    def set_hold(self, seconds):
        """How long `motion` stays True after movement (whole seconds)."""
        self.hold_s = int(seconds)
        return self._xfer(struct.pack("<I", self.hold_s))

    @property
    def motion(self):
        if not self._check():
            return None
        b = bytearray(1)
        return bool(b[0]) if self._xfer(None, b) else None

    def read(self):
        m = self.motion
        return None if m is None else {"motion": m}


DRIVER = SolderedPIR
