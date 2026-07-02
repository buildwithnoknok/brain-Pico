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
    ROLE_SELECT = "output"   # role assignment via cue-and-confirm (output-only module)
    _EP_OUT     = 0x02     # CDC data OUT  (host -> module: commands)
    _EP_IN      = 0x83     # CDC data IN   (module -> host: responses)

    # Preset animation ids, run autonomously on the module.
    PRESET_RAINBOW = 1          # firmware v1.6+
    PRESET_BREATHE = 2          # firmware v1.6+
    PRESET_CHASE   = 3          # firmware v1.6+
    PRESET_WIPE    = 4          # firmware v1.6+
    PRESET_TWINKLE = 5          # firmware v1.6+
    PRESET_SUNDOWN = 6          # firmware v1.8.1+ - one-shot eased fade to off, see below

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
        Run one of the built-in animations on the module, fire-and-forget: the
        module animates on its own until the next command.

            preset : 1-6 (PRESET_RAINBOW/BREATHE/CHASE/WIPE/TWINKLE/SUNDOWN)
            speed  : ms per animation step (0 = module default ~40 ms)
                     ** EXCEPT for PRESET_SUNDOWN (6, firmware v1.8.1+), where
                     `speed` instead means MINUTES for the total fade duration
                     (0 = default 30 min) - a single ms/step byte can't encode
                     a useful multi-minute duration, so this preset repurposes
                     the field. See module-usb-led/firmware/readme.md. **
            r,g,b  : base colour (ignored by the rainbow preset)

        PRESET_SUNDOWN is also the only ONE-SHOT preset: it fades from full
        brightness to off (quadratic ease-out - fast dim at the start, slow
        crawl to zero at the end) over `speed` minutes and then stops itself
        with the LEDs off, instead of looping forever like the other presets.
        Typical use: leds.play_preset(leds.PRESET_SUNDOWN, speed=30, b=255)
        for a 30-minute blue wind-down light.

        Any other LED command (set_all, set_led, off, ...) stops the animation.
        """
        self._send((0x20, int(preset) & 0xFF, int(speed) & 0xFF,
                    _clamp(r), _clamp(g), _clamp(b)))

    def role_cue(self, on=True):
        """Role-assignment cue (output module): light up bright white to identify
        THIS physical module during cue-and-confirm. on=False clears it."""
        if on:
            self.set_all(255, 255, 255)
        else:
            self.off()

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


# ============================================================================
# USB OTA flasher — re-flash a noknok USB module's application over USB via the
# noknok USB bootloader (module-USB-bootloader). The USB counterpart of the I2C
# ModuleFlasher (module_flasher.py); the flow mirrors it deliberately.
#
# ONE fundamental difference from I2C: the module's USB PID CHANGES across the
# OTA, so the device re-enumerates and the old usb.core handle goes stale:
#
#     app (PID 4E4E) --0xB0--> bootloader (PID 4E42) --flash+BOOT--> app (4E4E)
#
# The I2C flasher holds one bus for the whole flow; this one cannot hold one
# device handle. Instead it RE-FINDS the device (usb.core.find again) at every
# transition, matching the module's chip-UID SERIAL — which the bootloader
# reports identically to the app — so the right physical module is targeted even
# with several USB modules on the bus.
#
# The .bin you pass is the OFFSET-LINKED application image (linked at 0x2000 via
# app.ld) — exactly what `make build` produces in module-usb-led/firmware/src.
#
# Bootloader CDC protocol (host waits for each [state, err] reply on EP 0x83):
#   0x01 ERASE                      app region + metadata
#   0x02 WRITE n <n bytes>          one chunk (n <= 32)
#   0x03 READ_STATUS                -> [state, err]
#   0x04 VERIFY crc32(4 LE)         -> writes the validity marker; state==READY
#   0x05 BOOT                       jump to the app (no reply; device resets)
#   state: 0 IDLE 1 BUSY 2 READY 3 ERROR ; err: 0 ok, 5 CRC mismatch, 6 region
#
# A running app is flipped into the bootloader with CDC command 0xB0.
# CRC32 = zlib (poly 0xEDB88320, init/final 0xFFFFFFFF) to match the bootloader.
# ============================================================================


def crc32(data):
    """zlib CRC32 (poly 0xEDB88320). Matches the bootloader and binascii.crc32."""
    try:
        import binascii
        return binascii.crc32(data) & 0xFFFFFFFF
    except (ImportError, AttributeError):
        pass
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if (crc & 1) else (crc >> 1)
    return crc ^ 0xFFFFFFFF


class UsbFlashError(Exception):
    pass


class UsbModuleFlasher:
    """
    OTA flasher for noknok USB modules over the noknok USB bootloader.

        from noknok_usb import UsbModuleFlasher
        f = UsbModuleFlasher()                       # brings up the host port
        with open("noknok_leds.bin", "rb") as fh:
            f.flash(fh.read())                       # auto: app->0xB0->BL or blank BL

    Pass `serial=` (a module's chip-UID hex, lower-case) to target ONE specific
    module when several USB modules are on the bus.
    """

    PID_APP        = 0x4E4E
    PID_BOOTLOADER = 0x4E42
    APP_CMD_ENTER  = 0xB0     # CDC command: app resets into the bootloader

    CMD_ERASE   = 0x01
    CMD_WRITE   = 0x02
    CMD_STATUS  = 0x03
    CMD_VERIFY  = 0x04
    CMD_BOOT    = 0x05

    ST_IDLE, ST_BUSY, ST_READY, ST_ERROR = 0, 1, 2, 3
    CHUNK = 32                # bootloader WRITE accepts up to 32 bytes per chunk

    _EP_OUT = 0x02            # CDC data OUT (host -> module: commands)
    _EP_IN  = 0x83            # CDC data IN  (module -> host: [state, err])

    _ERRMSG = {0: "none", 5: "CRC mismatch", 6: "region overflow"}

    def __init__(self, dp=None, dm=None):
        ensure_host_port(dp, dm)     # raises if this build has no USB host support
        self._dp, self._dm = dp, dm

    # ── device discovery (re-run at every PID transition) ────────────────────
    def _find(self, pid, serial=None):
        """Return the configured usb.core device for (NOKNOK_VID, pid), optionally
        matching a chip-UID serial, or None. set_configuration() is idempotent."""
        for d in usb.core.find(find_all=True):
            try:
                if d.idVendor != NOKNOK_VID or d.idProduct != pid:
                    continue
                if serial is not None:
                    s = d.serial_number
                    if not s or s.lower() != serial.lower():
                        continue
            except Exception:
                continue
            try:
                d.set_configuration()
            except Exception:
                pass        # usually already configured by the host stack
            return d
        return None

    def _wait_for(self, pid, serial=None, timeout=8.0):
        """Poll the bus until the device with `pid` (re-)appears. The PIO-USB host
        re-enumeration after a PID change is the known risk this loop rides out."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            d = self._find(pid, serial)
            if d is not None:
                return d
            time.sleep(0.25)
        return None

    # ── low-level CDC helpers ────────────────────────────────────────────────
    def _send(self, dev, data):
        dev.write(self._EP_OUT, bytes(data), timeout=1000)

    def _read_status(self, dev, timeout=3000):
        """Read the 2-byte [state, err] reply, or None on timeout. `timeout` ms."""
        buf = bytearray(2)
        try:
            n = dev.read(self._EP_IN, buf, timeout=timeout)
        except Exception:
            return None
        if n < 2:
            return None
        return buf[0], buf[1]

    def _check(self, st, what):
        if st is None:
            raise UsbFlashError("%s: no reply from bootloader" % what)
        state, err = st
        if err != 0:
            raise UsbFlashError("%s: bootloader error %s (code %d, state %d)"
                                % (what, self._ERRMSG.get(err, "unknown"), err, state))
        return state

    # ── high-level steps ─────────────────────────────────────────────────────
    def present(self, serial=None):
        """True if a noknok USB bootloader (PID 4E42) is on the bus."""
        return self._find(self.PID_BOOTLOADER, serial) is not None

    def enter_bootloader(self, serial=None, timeout=8.0):
        """Flip a running app into the bootloader: find it (PID 4E4E), send 0xB0,
        then re-find it as the bootloader (PID 4E42). Returns the bootloader dev."""
        app = self._find(self.PID_APP, serial)
        if app is None:
            raise UsbFlashError("running app (PID 4E4E) not found to enter bootloader")
        # Lock onto this module's serial so we re-find the SAME physical module
        # after it resets (matters when several USB modules are present).
        if serial is None:
            try:
                serial = (app.serial_number or "").lower() or None
            except Exception:
                serial = None
        try:
            self._send(app, [self.APP_CMD_ENTER])
        except Exception:
            pass        # the module resets immediately; the write need not complete
        bl = self._wait_for(self.PID_BOOTLOADER, serial, timeout=timeout)
        if bl is None:
            raise UsbFlashError("bootloader (PID 4E42) did not appear after 0xB0")
        return bl, serial

    def erase(self, bl):
        self._send(bl, [self.CMD_ERASE])
        self._check(self._read_status(bl, timeout=3000), "ERASE")   # ~300 ms+

    def write_chunk(self, bl, data):
        n = len(data)
        self._send(bl, bytes([self.CMD_WRITE, n]) + bytes(data))
        self._check(self._read_status(bl, timeout=1000), "WRITE")

    def verify(self, bl, crc):
        """VERIFY is crc32 only (4 LE) — unlike I2C, no length field; the
        bootloader knows app_len from the bytes written. state must be READY."""
        pkt = bytes([self.CMD_VERIFY,
                     crc & 0xFF, (crc >> 8) & 0xFF,
                     (crc >> 16) & 0xFF, (crc >> 24) & 0xFF])
        self._send(bl, pkt)
        state = self._check(self._read_status(bl, timeout=3000), "VERIFY")
        if state != self.ST_READY:
            raise UsbFlashError("VERIFY: app not accepted (state %d, expected READY)"
                                % state)

    def boot(self, bl):
        """Jump to the freshly-flashed app. No reply — the module resets and
        re-enumerates back to the application PID (4E4E)."""
        try:
            self._send(bl, [self.CMD_BOOT])
        except Exception:
            pass        # device resets into the app; the write need not complete

    # ── orchestration (mirrors module_flasher.ModuleFlasher.flash) ───────────
    def flash(self, data, serial=None, progress=None, confirm_app=True):
        """
        Flash an offset-linked app image (bytes) to a noknok USB module.

        serial      : chip-UID hex of the target module; None = first one found.
        progress    : optional callback(done_bytes, total_bytes).
        confirm_app : after BOOT, wait for the module to re-enumerate as the app
                      (PID 4E4E) and return its usb.core device (or None). Set
                      False to skip the wait.

        Returns the re-enumerated app device if confirm_app, else True.
        """
        total = len(data)
        if total == 0:
            raise UsbFlashError("empty image")

        # 1. Get to the bootloader (directly if already there, else via 0xB0).
        bl = self._find(self.PID_BOOTLOADER, serial)
        if bl is None:
            bl, serial = self.enter_bootloader(serial)
        elif serial is None:
            try:
                serial = (bl.serial_number or "").lower() or None
            except Exception:
                serial = None
        time.sleep(0.2)

        # 2. Erase.
        self.erase(bl)

        # 3. Stream the image in <=32-byte chunks.
        off = 0
        while off < total:
            self.write_chunk(bl, data[off:off + self.CHUNK])
            off += self.CHUNK
            if progress:
                progress(min(off, total), total)

        # 4. Verify (writes the validity marker only on a CRC match).
        self.verify(bl, crc32(data))

        # 5. Boot into the app.
        self.boot(bl)

        if confirm_app:
            return self._wait_for(self.PID_APP, serial, timeout=8.0)
        return True
