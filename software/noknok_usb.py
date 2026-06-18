# noknok_usb.py - USB transport + USB module drivers for the noknok Conductor.
#
# Counterpart to the I2C side of noknok.py. USB modules (e.g. the noknok LEDs
# ring) attach to the Pico as USB devices on a PIO-USB host port (GP16/GP17).
# This module is LAZILY imported by noknok.py's Conductor (enumerate_usb) only
# when a product actually uses USB modules, so I2C-only products never load the
# USB stack and pay no RAM/flash cost.
#
# Setup (Pico 2 W, RP2350, CircuitPython with USB host support):
#   - D+ -> GP16, D- -> GP17 (consecutive pair, D+ the lower pin); modules behind
#     a POWERED hub (the DataHub). discover() brings the host port up itself.
#
# Identity: each module reports a unique iSerialNumber built from its chip UID
# (firmware v1.6+) - the USB counterpart of the I2C hardware UID. The Conductor
# keys USB modules in its registry by this serial, exactly like I2C UIDs.

import time

try:
    import usb.core
    import usb_host
    import board
    _USB_OK = True
except ImportError:
    _USB_OK = False

NOKNOK_VID = 0x1209

# Default PIO-USB host pins (noknok standard). D+ must be the lower GPIO number.
DEFAULT_DP = board.GP16 if _USB_OK else None
DEFAULT_DM = board.GP17 if _USB_OK else None


def _clamp(v):
    v = int(v)
    return 0 if v < 0 else 255 if v > 255 else v


class NoknokLEDs:
    """
    Driver for the noknok LEDs module (8x WS2812b RGB) over USB.

    The module is a USB CDC device; the Pico drives it as a USB host via
    CircuitPython's usb.core (raw bulk writes to the data endpoint). The API
    mirrors the I2C module drivers so it feels the same to use.

        leds.set_all(255, 0, 0)            # all red
        leds.set_pixel(3, 0, 255, 0)       # LED 3 green
        leds.set_brightness(128)           # half brightness
        leds.set_led(0xFF, 255, 0, 0, brightness=128, duration_ms=1000)  # all red 1 s
        leds.play_preset(leds.PRESET_RAINBOW, speed=40)                  # rainbow
        leds.off()

    Normally you don't construct these directly - the Conductor's enumerate_usb()
    discovers them. For a quick one-off, NoknokLEDs.find() grabs the first one.
    """

    LED_COUNT   = 8
    MODULE_TYPE = 0x04
    PID         = 0x4E4E
    _EP_OUT     = 0x02     # CDC data OUT  (host -> module: commands)
    _EP_IN      = 0x83     # CDC data IN   (module -> host: responses)

    # Preset animation ids (firmware v1.6+), run autonomously on the module.
    PRESET_RAINBOW = 1
    PRESET_BREATHE = 2
    PRESET_CHASE   = 3
    PRESET_WIPE    = 4
    PRESET_TWINKLE = 5

    def __init__(self, device):
        self._dev = device
        self._uid_hex = None            # USB serial (set by discover()); the identity
        self.protocol_version = None
        self.firmware_version = None

    # -- discovery -------------------------------------------------------------

    @classmethod
    def find(cls, vid=NOKNOK_VID, pid=PID, dp=None, dm=None):
        """
        Convenience: bring up the host port and return the FIRST LED module found
        (or None). For multiple modules use the Conductor's enumerate_usb().
        """
        ensure_host_port(dp, dm)
        dev = usb.core.find(idVendor=vid, idProduct=pid)
        if dev is None:
            return None
        try:
            dev.set_configuration()
        except Exception:
            pass  # often already configured by the host stack
        return cls(dev)

    # -- LED control -----------------------------------------------------------

    def off(self):
        """Turn all LEDs off."""
        self._send((0x00,))

    def set_all(self, r, g, b):
        """Set all 8 LEDs to one colour. R, G, B each 0-255."""
        self._send((0x01, _clamp(r), _clamp(g), _clamp(b)))

    def set_pixel(self, index, r, g, b):
        """Set a single LED (0-7) to a colour."""
        self._send((0x02, int(index) & 0xFF, _clamp(r), _clamp(g), _clamp(b)))

    def set_brightness(self, brightness):
        """Global brightness 0-255, applied on top of the per-LED colours."""
        self._send((0x03, _clamp(brightness)))

    def fill(self, hex_color):
        """Set all LEDs to a 24-bit hex colour, e.g. fill(0xFF8800)."""
        self.set_all((hex_color >> 16) & 0xFF, (hex_color >> 8) & 0xFF, hex_color & 0xFF)

    def set_all_pixels(self, pixels):
        """
        Set all 8 LEDs at once. `pixels` is an iterable of up to 8 (r, g, b)
        tuples; any missing LEDs are turned off.
        """
        data = bytearray((0x04,))
        for px in list(pixels)[:self.LED_COUNT]:
            data += bytes((_clamp(px[0]), _clamp(px[1]), _clamp(px[2])))
        while len(data) < 1 + self.LED_COUNT * 3:
            data += b"\x00\x00\x00"
        self._send(data)

    def show(self):
        """Explicit show (setters already auto-show; here for completeness)."""
        self._send((0x05,))

    def set_led(self, index, r, g, b, brightness=255, duration_ms=0):
        """
        Full control of one LED (or all) in a single command (firmware v1.6+):
        colour, brightness and an optional auto-off duration.

            index       : 0-7 for one LED, or 0xFF / "all" for all 8
            r, g, b     : colour 0-255
            brightness  : global brightness 0-255
            duration_ms : 0 = hold indefinitely; otherwise the LED(s) turn off
                          automatically after this many milliseconds (max 65535)

        The module runs the timing itself (non-blocking), so this is fire-and-forget.
        """
        idx = 0xFF if index == "all" else (int(index) & 0xFF)
        d = int(duration_ms) & 0xFFFF
        self._send((0x10, idx, _clamp(r), _clamp(g), _clamp(b),
                    _clamp(brightness), d & 0xFF, (d >> 8) & 0xFF))

    def play_preset(self, preset, speed=0, r=0, g=0, b=0):
        """
        Run one of the 5 built-in animations on the module (firmware v1.6+),
        fire-and-forget: the module animates on its own until the next command.

            preset : 1-5 (PRESET_RAINBOW/BREATHE/CHASE/WIPE/TWINKLE)
            speed  : ms per animation step (0 = module default ~40 ms)
            r,g,b  : base colour (ignored by the rainbow preset)

        Any other LED command (set_all, set_led, off, ...) stops the animation.
        """
        self._send((0x20, int(preset) & 0xFF, int(speed) & 0xFF,
                    _clamp(r), _clamp(g), _clamp(b)))

    def identify(self):
        """
        Send the identity query (0xF0) and check for the [0x4E,0x4E,0x04] reply.
        Returns True if the module identifies correctly.
        """
        try:
            self._send((0xF0,))
            # CircuitPython usb.core.read() needs a buffer to read INTO and
            # returns the byte count (unlike desktop PyUSB's size-int form).
            buf = bytearray(3)
            n = self._dev.read(self._EP_IN, buf, timeout=300)
            return bytes(buf[:n]) == bytes((0x4E, 0x4E, self.MODULE_TYPE))
        except Exception:
            return False

    def version(self):
        """
        Send GET_VERSION (0xB1) and return (protocol, major, minor, patch),
        or None on error. Requires module firmware v1.5+.
        """
        try:
            self._send((0xB1,))
            buf = bytearray(4)
            n = self._dev.read(self._EP_IN, buf, timeout=300)
            return tuple(buf[:n]) if n == 4 else None
        except Exception:
            return None

    # -- low level -------------------------------------------------------------

    def _send(self, data):
        # Raw bulk OUT to the CDC data endpoint. No CDC line-coding required.
        self._dev.write(self._EP_OUT, bytes(data), timeout=1000)


# PID -> (driver class, type_name). Add future USB modules here.
_USB_MODULES = {
    NoknokLEDs.PID: (NoknokLEDs, "noknokleds"),
}

_host_port = None


def available():
    """True if this CircuitPython build has USB host support (usb.core + usb_host).
    Lets the Conductor skip USB enumeration cleanly on I2C-only builds."""
    return _USB_OK


def ensure_host_port(dp=None, dm=None):
    """Bring up the PIO-USB host port once (idempotent). Returns the Port."""
    global _host_port
    if not _USB_OK:
        raise RuntimeError(
            "usb.core / usb_host not available - need a CircuitPython build with "
            "USB host support.")
    if _host_port is None:
        _host_port = usb_host.Port(dp or DEFAULT_DP, dm or DEFAULT_DM)
    return _host_port


def discover(dp=None, dm=None, settle_sec=3, max_sec=20, empty_grace=6):
    """
    Bring up the USB host port and discover every noknok USB module on the bus.
    Returns a list of (serial_hex_lower, type_name, module_instance) - empty if
    no USB modules are connected (e.g. an I2C-only assembly), so the caller skips
    USB cleanly.

    Modules enumerate sequentially through a hub (~3 s each). Timing:
      - wait up to `empty_grace` s for the FIRST module to appear; if none does,
        assume there are no USB modules and stop (fast skip for I2C-only setups);
      - once at least one is found, stop `settle_sec` s after the last NEW module;
      - hard cap at `max_sec` s.
    Each module's serial (chip-UID hex) is the stable identity; the Conductor
    keys its registry by it, exactly like an I2C UID.
    """
    ensure_host_port(dp, dm)
    found = {}                          # serial(lower) -> (type_name, module)
    start = time.monotonic()
    last_new = None
    while True:
        now = time.monotonic()
        if now - start >= max_sec:
            break
        if last_new is None:
            if now - start >= empty_grace:
                break                   # nothing appeared -> no USB modules
        elif now - last_new >= settle_sec:
            break                       # found some, settled
        for d in usb.core.find(find_all=True):
            try:
                if d.idVendor != NOKNOK_VID:
                    continue
                pid = d.idProduct
            except Exception:
                continue
            entry = _USB_MODULES.get(pid)
            if entry is None:
                continue
            try:
                serial = d.serial_number
            except Exception:
                serial = None
            if not serial:
                continue
            key = serial.lower()
            if key in found:
                continue
            cls, type_name = entry
            try:
                d.set_configuration()
            except Exception:
                pass
            mod = cls(d)
            mod._uid_hex = key
            v = mod.version()
            if v:
                mod.protocol_version = v[0]
                mod.firmware_version = "%d.%d.%d" % (v[1], v[2], v[3])
            found[key] = (type_name, mod)
            last_new = now
        time.sleep(0.3)
    return [(k, t, m) for k, (t, m) in found.items()]
