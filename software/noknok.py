# noknok.py
# CircuitPython library for the noknok modular ecosystem
# Raspberry Pi Pico — I2C master ("Conductor")
#
# Quick start:
#   from noknok import Conductor
#   c = Conductor()
#   c.enumerate()                             # discover all modules (~3 s)
#   c.load_roles()                            # load noknok_roles.json (optional)
#   c.role["volume_knob"].value               # access by role name
#   c.buzzer[0].play(440, 500)                # or by type + index
#   c.ledbutton[0].set_color(255, 0, 0)       # red LED on LED button module

import busio
import board
import time
import json


# ── CRC8 (polynomial 0x07) — matches firmware ────────────────────────────────
def _crc8(data):
    crc = 0x00
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


# ═════════════════════════════════════════════════════════════════════════════
class Conductor:
    """
    I2C master for the noknok ecosystem.
    Discovers all connected modules and assigns each a unique address.

    Typical usage:
        c = Conductor()
        c.enumerate()           # ~3 s, discovers all modules
        c.load_roles()          # load noknok_roles.json if it exists

        # Access by role (stable — same physical module every boot):
        c.role["volume_knob"].value
        c.role["alert_buzzer"].play(880, 200)
        c.role["ok_button"].set_color(0, 255, 0)

        # Access by type + index (order = discovery order):
        c.buzzer[0].play(440, 500)
        c.ledbutton[0].set_color(255, 0, 0)
    """

    ENUM_ADDR  = 0x7F
    ASSIGN_REG = 0x1D

    TYPE_BUZZER    = 0x01
    TYPE_KNOB      = 0x02
    TYPE_LEDBUTTON = 0x03

    def __init__(self, sda=board.GP8, scl=board.GP9, frequency=100_000):
        self.i2c       = busio.I2C(scl, sda, frequency=frequency)
        self.buzzer    = []    # NoknokBuzzer instances, indexed by discovery order
        self.knob      = []    # NoknokKnob instances
        self.ledbutton = []    # NoknokLedButton instances
        self.role      = {}    # role_name → module object, populated by load_roles()
        self._registry = {}    # uid_hex → module object

    # ── Low-level I2C ─────────────────────────────────────────────────────────

    def _read(self, addr, n):
        buf = bytearray(n)
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.readfrom_into(addr, buf)
            return buf
        except OSError:
            return None
        finally:
            self.i2c.unlock()

    def _write(self, addr, data):
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.writeto(addr, bytes(data))
            return True
        except OSError:
            return False
        finally:
            self.i2c.unlock()

    # ── Enumeration ───────────────────────────────────────────────────────────

    def enumerate(self, total_timeout_sec=10):
        """
        Discover all modules on the bus.
        Polls 0x7F every 20 ms. Stops after 3000 ms of no response.
        Returns total number of modules found.
        """
        print("Enumerating noknok modules...")
        self.buzzer    = []
        self.knob      = []
        self.ledbutton = []
        self._registry = {}
        self.role      = {}

        # ── Step 1: Restore already-assigned modules ──────────────────────────
        restored = self._restore_state()
        if restored > 0:
            print(f"  {restored} module(s) already assigned.")

        # Determine next free address (skip ones already in use)
        used = {m.address for m in self._registry.values() if m is not None}
        next_addr = 0x08
        while next_addr in used:
            next_addr += 1

        # ── Step 2: Scan 0x7F for new (unassigned) modules ───────────────────
        # Always wait the full 3000 ms so a new module with a long backoff
        # (up to 2799 ms) is never missed, even when a saved state was restored.
        no_resp_limit = 3000
        no_resp_ms    = 0
        deadline      = time.monotonic() + total_timeout_sec
        new_found     = 0

        while no_resp_ms < no_resp_limit and time.monotonic() < deadline:

            buf = self._read(self.ENUM_ADDR, 10)

            if buf is None:
                no_resp_ms += 20
                time.sleep(0.02)
                continue

            no_resp_ms = 0

            # Verify CRC
            if _crc8(buf[:9]) != buf[9]:
                print("  CRC mismatch — possible collision, retrying...")
                time.sleep(0.05)
                continue

            uid_hex     = bytes(buf[:8]).hex()
            module_type = buf[8]
            addr        = next_addr
            next_addr  += 1

            # Assign address
            self._write(self.ENUM_ADDR, [self.ASSIGN_REG, addr])
            time.sleep(0.05)

            # Instantiate correct class
            if module_type == self.TYPE_BUZZER:
                module    = NoknokBuzzer(self.i2c, address=addr)
                type_name = "noknokbuzzer"
                self.buzzer.append(module)
            elif module_type == self.TYPE_KNOB:
                module    = NoknokKnob(self.i2c, address=addr)
                type_name = "noknokknob"
                self.knob.append(module)
            elif module_type == self.TYPE_LEDBUTTON:
                module    = NoknokLedButton(self.i2c, address=addr)
                type_name = "noknokledbutton"
                self.ledbutton.append(module)
            else:
                module    = None
                type_name = f"unknown(0x{module_type:02X})"

            if module is not None:
                module._uid_hex = uid_hex

            self._registry[uid_hex] = module
            new_found += 1
            print(f"  {type_name} → 0x{addr:02X}  UID: {uid_hex}  [new]")

            time.sleep(0.02)

        # ── Step 3: Save state ────────────────────────────────────────────────
        self._save_state()

        # ── Summary ───────────────────────────────────────────────────────────
        total = sum([len(self.buzzer), len(self.knob), len(self.ledbutton)])
        if new_found == 0 and restored > 0:
            print(f"No new modules. {restored} module(s) already assigned:")
            for uid, m in self._registry.items():
                if m: print(f"  {type(m).__name__} at 0x{m.address:02X}  UID: {uid}")
        else:
            print(f"Done — {total} module(s) ({restored} restored, {new_found} new).")
        return total

    # ── State persistence ─────────────────────────────────────────────────────

    def _save_state(self, filename="noknok_state.json"):
        """Save current module assignments to JSON so next run can restore them."""
        data = {}
        for uid_hex, module in self._registry.items():
            if module is not None:
                if isinstance(module, NoknokBuzzer):
                    t = self.TYPE_BUZZER
                elif isinstance(module, NoknokKnob):
                    t = self.TYPE_KNOB
                elif isinstance(module, NoknokLedButton):
                    t = self.TYPE_LEDBUTTON
                else:
                    t = 0
                data[uid_hex] = {"address": module.address, "type": t}
        try:
            with open(filename, "w") as f:
                json.dump(data, f)
        except OSError:
            pass   # read-only filesystem — silently skip

    def _restore_state(self, filename="noknok_state.json"):
        """
        Load saved state and ping each module at its known address.
        Returns the number of modules successfully restored.
        """
        try:
            with open(filename, "r") as f:
                data = json.load(f)
        except OSError:
            return 0

        restored = 0
        for uid_hex, info in data.items():
            addr      = info.get("address", 0)
            type_code = info.get("type",    0)

            if self._read(addr, 1) is None:
                continue   # module not responding

            if type_code == self.TYPE_BUZZER:
                module = NoknokBuzzer(self.i2c, address=addr)
                module._uid_hex = uid_hex
                self.buzzer.append(module)
            elif type_code == self.TYPE_KNOB:
                module = NoknokKnob(self.i2c, address=addr)
                module._uid_hex = uid_hex
                self.knob.append(module)
            elif type_code == self.TYPE_LEDBUTTON:
                module = NoknokLedButton(self.i2c, address=addr)
                module._uid_hex = uid_hex
                self.ledbutton.append(module)
            else:
                module = None

            if module is not None:
                self._registry[uid_hex] = module
                restored += 1

        return restored

    # ── Role management ───────────────────────────────────────────────────────

    def load_roles(self, filename="noknok_roles.json"):
        """
        Load role assignments from a JSON file on the Pico's CIRCUITPY drive.
        Returns True if all roles were found, False if any are missing.
        """
        try:
            with open(filename, "r") as f:
                mapping = json.load(f)
        except OSError:
            print(f"  No roles file found at '{filename}'")
            print(f"  Run c.setup_roles() to create one.")
            return False

        print(f"Loading roles from '{filename}'...")
        self.role = {}
        missing   = []

        for role_name, uid_hex in mapping.items():
            uid_hex = uid_hex.lower().replace("-", "").replace(" ", "")
            module  = self._registry.get(uid_hex)
            if module is not None:
                self.role[role_name] = module
                type_name = type(module).__name__
                print(f"  '{role_name}' → {type_name} at 0x{module.address:02X}")
            else:
                self.role[role_name] = None
                missing.append(role_name)
                print(f"  '{role_name}' → NOT FOUND  (UID: {uid_hex})")

        if missing:
            print(f"  ⚠ {len(missing)} role(s) not found: {', '.join(missing)}")
        else:
            print(f"  All {len(self.role)} role(s) loaded.")

        return len(missing) == 0

    def save_roles(self, mapping, filename="noknok_roles.json"):
        """
        Save a role mapping dict to a JSON file.
        mapping = { "role_name": module_object, ... }
        """
        data = {}
        for role_name, module in mapping.items():
            if module is not None and hasattr(module, "_uid_hex"):
                data[role_name] = module._uid_hex
            else:
                print(f"  ⚠ Skipping '{role_name}' — no UID available")

        with open(filename, "w") as f:
            json.dump(data, f)

        print(f"Saved {len(data)} role(s) to '{filename}'")

    def setup_roles(self, filename="noknok_roles.json"):
        """
        Interactive role assignment wizard. Run once from the Thonny REPL.

        Walks through every discovered module, activates it so you can identify
        it physically, then asks you to type a role name.

          noknokbuzzer    → plays a beep
          noknokledbutton → flashes LED white for 1 s

        Example session:
            >>> c.enumerate()
            >>> c.setup_roles()
            Module 1/2: NoknokLedButton at 0x08  (UID: fc6eabcd65f3bdb8)
            Flashing LED so you can identify it...
            Role name (or Enter to skip): ok_button
            → assigned as 'ok_button'
            ...
            Saved 2 role(s) to 'noknok_roles.json'
        """
        all_modules = []
        for uid_hex, module in self._registry.items():
            if module is not None:
                all_modules.append((uid_hex, module))

        if not all_modules:
            print("No modules found. Run enumerate() first.")
            return

        print(f"\nRole setup wizard — {len(all_modules)} module(s) found.")
        print("For each module: identify it physically, then type a role name.")
        print("The role name is how you'll refer to it in code: c.role[\"name\"]\n")

        assignment = {}

        for i, (uid_hex, module) in enumerate(all_modules):
            type_name = type(module).__name__
            print(f"Module {i+1}/{len(all_modules)}: {type_name} at 0x{module.address:02X}  (UID: {uid_hex})")

            if isinstance(module, NoknokBuzzer):
                print("  → Playing a beep so you can identify it...")
                module.tune(module.BEEP_OK)
                time.sleep(0.5)
            elif isinstance(module, NoknokKnob):
                print("  → Turn the knob or press it to identify it.")
            elif isinstance(module, NoknokLedButton):
                print("  → Flashing LED white so you can identify it...")
                module.set_color(40, 40, 40)
                time.sleep(1.0)
                module.led_off()

            role = input("  Role name (or Enter to skip): ").strip()

            if role:
                assignment[role] = uid_hex
                print(f"  → assigned as '{role}'\n")
            else:
                print(f"  → skipped\n")

        if assignment:
            with open(filename, "w") as f:
                json.dump(assignment, f)
            print(f"Saved {len(assignment)} role(s) to '{filename}'")
            print(f"\nIn your app code:")
            print(f"  c.enumerate()")
            print(f"  c.load_roles()")
            for role_name in assignment:
                print(f"  c.role[\"{role_name}\"]  # always this physical module")
        else:
            print("No roles assigned. File not written.")

    # ── Lookup ────────────────────────────────────────────────────────────────

    def by_uid(self, uid_hex):
        """Return a module by its UID hex string (hyphens and spaces ignored)."""
        key = uid_hex.lower().replace("-", "").replace(" ", "")
        return self._registry.get(key)


# ═════════════════════════════════════════════════════════════════════════════
class NoknokBuzzer:
    """
    Driver for the noknok Buzzer Module (noknokbuzzer, CH32V003, firmware v3+).

    Normally obtained via Conductor.enumerate():
        c = Conductor()
        c.enumerate()
        b = c.buzzer[0]            # by discovery index
        b = c.role["alert_buzzer"] # by role name (after load_roles)
    """

    NOKIA           = 1
    HAPPY_BIRTHDAY  = 2
    BEEP_OK         = 3
    BEEP_ERROR      = 4
    STARTUP         = 5

    _CMD_STOP      = 0x00
    _CMD_PLAY_NOTE = 0x01
    _CMD_PLAY_TUNE = 0x02

    def __init__(self, i2c, address=0x08):
        self.i2c      = i2c
        self.address  = address
        self._uid_hex = None   # set by Conductor.enumerate()

    def _send(self, data):
        """Send bytes to the module. Returns True on success, False on I2C error."""
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.writeto(self.address, bytes(data))
            return True
        except OSError:
            return False
        finally:
            self.i2c.unlock()

    def _read(self, n=1):
        """Read n bytes from the module. Returns bytearray or None on I2C error."""
        buf = bytearray(n)
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.readfrom_into(self.address, buf)
            return buf
        except OSError:
            return None
        finally:
            self.i2c.unlock()

    def play(self, freq_hz, duration_ms, volume=100):
        """Play a single note. Fire and forget — returns immediately."""
        if freq_hz <= 0:
            return self.stop()
        dur     = max(1, int(duration_ms / 100))
        vol     = max(0, min(100, volume))
        freq_hi = (freq_hz >> 8) & 0xFF
        freq_lo =  freq_hz       & 0xFF
        self._send([self._CMD_PLAY_NOTE, freq_hi, freq_lo, dur, vol])

    beep = play   # backwards compatibility

    def note(self, freq_hz, duration_ms, volume=100, gap_ms=50):
        """Play a note and wait until it finishes. Use in melodies."""
        self.play(freq_hz, duration_ms, volume)
        time.sleep((duration_ms + gap_ms) / 1000)

    def tune(self, tune_id):
        """Play a preloaded tune. Fire and forget."""
        self._send([self._CMD_PLAY_TUNE, tune_id])

    def stop(self):
        """Stop playback immediately."""
        self._send([self._CMD_STOP])

    def is_playing(self):
        """Returns True if currently playing. Returns False on I2C error."""
        buf = self._read(1)
        if buf is None:
            return False
        return buf[0] == 0x01

    def wait(self, timeout_sec=30):
        """Block until idle or timeout. Returns True if idle."""
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if not self.is_playing():
                return True
            time.sleep(0.05)
        return False


# ═════════════════════════════════════════════════════════════════════════════
class NoknokKnob:
    """
    Driver for the noknok Knob Module (noknokknob, CH32V003J4M6, firmware v1+).

    Normally obtained via Conductor.enumerate():
        c = Conductor()
        c.enumerate()
        k = c.knob[0]              # by discovery index
        k = c.role["volume_knob"]  # by role name (after load_roles)

    Reading:
        s = k.read()
        s.position   # signed int, cumulative turns (each detent = ±1)
        s.delta      # signed int, change since last read (auto-clears)
        s.pressed    # True if button currently pressed

    Commands:
        k.reset()            # set position to 0
        k.set_position(42)   # set position to any signed value

    Simple polling example:
        while True:
            s = k.read()
            if s is not None and s.delta != 0:
                print("Position:", s.position)
            if s is not None and s.pressed:
                print("Button held")
            time.sleep(0.05)
    """

    _CMD_RESET    = 0x10
    _CMD_SET_POS  = 0x11

    def __init__(self, i2c, address=0x08):
        self.i2c      = i2c
        self.address  = address
        self._uid_hex = None   # set by Conductor.enumerate()

    def _send(self, data):
        """Send bytes to the module. Returns True on success, False on I2C error."""
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.writeto(self.address, bytes(data))
            return True
        except OSError:
            return False
        finally:
            self.i2c.unlock()

    def _read_raw(self, n=4):
        """Read n bytes from the module. Returns bytearray or None on I2C error."""
        buf = bytearray(n)
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.readfrom_into(self.address, buf)
            return buf
        except OSError:
            return None
        finally:
            self.i2c.unlock()

    def read(self):
        """
        Read position, delta, and button state from the module.
        Returns a KnobStatus object, or None on I2C error.

        delta auto-clears on the module after each read — you won't miss
        increments as long as you poll before the int8 overflows (±127 steps).
        """
        buf = self._read_raw(4)
        if buf is None:
            return None
        return KnobStatus(buf)

    def reset(self):
        """Set position to 0."""
        self._send([self._CMD_RESET])

    def set_position(self, value):
        """Set position to any signed 16-bit value (-32768 to 32767)."""
        value = max(-32768, min(32767, int(value)))
        hi = (value >> 8) & 0xFF
        lo = value & 0xFF
        self._send([self._CMD_SET_POS, hi, lo])

    @property
    def position(self):
        """Current cumulative position as a signed integer. Returns None on error."""
        s = self.read()
        return s.position if s is not None else None

    @property
    def is_pressed(self):
        """True if the button is currently held down. Returns False on error."""
        s = self.read()
        return s.pressed if s is not None else False


# ─────────────────────────────────────────────────────────────────────────────
class KnobStatus:
    """
    Result of NoknokKnob.read().

    Attributes:
        position  (int)  — signed 16-bit cumulative position
        delta     (int)  — signed 8-bit change since last read (cleared on read)
        pressed   (bool) — True if button is currently held down
    """
    __slots__ = ("position", "delta", "pressed")

    def __init__(self, buf):
        raw = (buf[0] << 8) | buf[1]
        self.position = raw if raw < 32768 else raw - 65536
        self.delta    = buf[2] if buf[2] < 128 else buf[2] - 256
        self.pressed  = bool(buf[3])

    def __repr__(self):
        return (f"KnobStatus(position={self.position}, "
                f"delta={self.delta}, pressed={self.pressed})")


# ═════════════════════════════════════════════════════════════════════════════
class NoknokLedButton:
    """
    Driver for the noknok LED Button Module (noknokledbutton, CH32V003F4U6, firmware v1+).

    Normally obtained via Conductor.enumerate():
        c = Conductor()
        c.enumerate()
        k = c.ledbutton[0]          # by discovery index
        k = c.role["ok_button"]     # by role name (after load_roles)

    LED control:
        k.set_color(255, 0, 0)      # red  (R, G, B — 0-255 each)
        k.set_color(0, 255, 0)      # green
        k.set_color(255, 255, 255)  # white
        k.led_off()                  # off

    Button reading:
        status = k.read()
        status.pressed       # True if button is held down right now
        status.press_event   # True if pressed since last read (edge, clears on read)
        status.release_event # True if released since last read (edge, clears on read)
        status.count         # cumulative press count (0-255, wraps)

        k.reset_count()      # reset cumulative counter to 0

    Simple polling example:
        while True:
            s = k.read()
            if s is not None and s.press_event:
                print("Button pressed!")
            time.sleep(0.05)
    """

    _CMD_LED_OFF   = 0x00
    _CMD_LED_SET   = 0x10
    _CMD_CNT_RESET = 0x11

    def __init__(self, i2c, address=0x08):
        self.i2c      = i2c
        self.address  = address
        self._uid_hex = None   # set by Conductor.enumerate()

    def _send(self, data):
        """Send bytes to the module. Returns True on success, False on I2C error."""
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.writeto(self.address, bytes(data))
            return True
        except OSError:
            return False
        finally:
            self.i2c.unlock()

    def _read_raw(self, n=2):
        """Read n bytes from the module. Returns bytearray or None on I2C error."""
        buf = bytearray(n)
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.readfrom_into(self.address, buf)
            return buf
        except OSError:
            return None
        finally:
            self.i2c.unlock()

    def set_color(self, r, g, b):
        """Set LED colour. R, G, B each 0-255. (SK6812MINI-E is RGB only.)"""
        self._send([self._CMD_LED_SET,
                    max(0, min(255, r)),
                    max(0, min(255, g)),
                    max(0, min(255, b)),
                    0])   # W byte kept for protocol compatibility — ignored by firmware

    def led_off(self):
        """Turn LED off."""
        self._send([self._CMD_LED_OFF])

    def read(self):
        """
        Read button state and press count from the module.
        Returns a LedButtonStatus object, or None on I2C error.

        Edge flags (press_event, release_event) are cleared on the module
        after each read — you won't miss events as long as you poll faster
        than the user can press and release (~50 ms is plenty).
        """
        buf = self._read_raw(2)
        if buf is None:
            return None
        return LedButtonStatus(buf[0], buf[1])

    def reset_count(self):
        """Reset the cumulative press counter to 0."""
        self._send([self._CMD_CNT_RESET])

    @property
    def is_pressed(self):
        """True if the button is currently held down. Returns False on error."""
        buf = self._read_raw(1)
        if buf is None:
            return False
        return bool(buf[0] & 0x01)


# ─────────────────────────────────────────────────────────────────────────────
class LedButtonStatus:
    """
    Result of NoknokLedButton.read().

    Attributes:
        pressed       (bool) — button is currently held down
        press_event   (bool) — button was pressed since last read
        release_event (bool) — button was released since last read
        count         (int)  — cumulative press count (0-255)
    """
    __slots__ = ("pressed", "press_event", "release_event", "count")

    def __init__(self, status_byte, count_byte):
        self.pressed       = bool(status_byte & 0x01)
        self.press_event   = bool(status_byte & 0x02)
        self.release_event = bool(status_byte & 0x04)
        self.count         = count_byte

    def __repr__(self):
        return (f"LedButtonStatus(pressed={self.pressed}, "
                f"press_event={self.press_event}, "
                f"release_event={self.release_event}, "
                f"count={self.count})")

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
class NoknokLEDs:
    """
    Driver for the noknok LEDs module (8Ã— WS2812b RGB over USB CDC).

    On Pico 2W (RP2350, CircuitPython 9+), obtain via Conductor.find_leds():
        c = Conductor()
        c.enumerate()
        leds = c.find_leds()       # auto-detects module on USB host
        leds = c.leds[0]           # or by discovery index

    Or instantiate directly if you already have a USB serial stream:
        leds = NoknokLEDs(serial_device)

    LED control:
        leds.set_all(255, 0, 0)          # all red
        leds.set_pixel(3, 0, 255, 0)     # LED 3 green
        leds.fill(0xFF8800)              # hex colour
        leds.set_brightness(128)         # half brightness
        leds.set_all_pixels([            # 8 individual colours
            (255,0,0),(0,255,0),(0,0,255),(255,255,0),
            (0,255,255),(255,0,255),(128,128,128),(0,0,0)
        ])
        leds.off()
    """

    LED_COUNT   = 8
    MODULE_TYPE = 0x04

    def __init__(self, device):
        """
        device: any object with a write(bytes) method.
        On CircuitPython USB host this is typically a usb.core Device wrapper.
        """
        self._dev = device

    # â”€â”€ Discovery (CircuitPython USB host) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @classmethod
    def find(cls):
        """
        Scan USB host for a noknok LEDs module.
        Requires CircuitPython 9+ on Pico 2W (RP2350) with USB host enabled.
        Returns a NoknokLEDs instance, or None if not found.

        Usage in boot.py:
            import usb_host
            import board
            usb_host.Port(board.USB_HOST_DP, board.USB_HOST_DM)
        """
        try:
            import usb.core
        except ImportError:
            raise RuntimeError(
                "usb.core not available â€” requires CircuitPython 9+ on Pico 2W "
                "with USB host enabled in boot.py"
            )

        for device in usb.core.find(find_all=True):
            ep_out, ep_in = cls._find_cdc_endpoints(device)
            if ep_out is None:
                continue
            try:
                # Try identity query
                ep_out.write(bytes([0xF0]))
                resp = bytes(ep_in.read(3, timeout=300))
                if resp == bytes([0x4E, 0x4E, cls.MODULE_TYPE]):
                    wrapper = _USBCDCWrapper(ep_out, ep_in)
                    print(f"noknok LEDs found (USB {device.bus}/{device.address})")
                    return cls(wrapper)
            except Exception:
                pass
        return None

    @staticmethod
    def _find_cdc_endpoints(device):
        """Return (ep_out, ep_in) for the CDC data interface, or (None, None)."""
        try:
            import usb.util
            for cfg in device:
                for intf in cfg:
                    if intf.bInterfaceClass == 0x0A:   # CDC Data interface
                        ep_out = ep_in = None
                        for ep in intf:
                            if usb.util.endpoint_direction(ep.bEndpointAddress) \
                                    == usb.util.ENDPOINT_OUT:
                                ep_out = ep
                            else:
                                ep_in = ep
                        if ep_out and ep_in:
                            return ep_out, ep_in
        except Exception:
            pass
        return None, None

    # â”€â”€ LED control â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def set_all(self, r, g, b):
        """Set all 8 LEDs to one colour. R, G, B each 0â€“255."""
        self._send(bytes([0x01, _clamp(r), _clamp(g), _clamp(b)]))

    def set_pixel(self, index, r, g, b):
        """Set a single LED (0â€“7) to a colour."""
        self._send(bytes([0x02, index & 0xFF, _clamp(r), _clamp(g), _clamp(b)]))

    def set_brightness(self, brightness):
        """Global brightness scale 0â€“255. Applied on top of individual colours."""
        self._send(bytes([0x03, _clamp(brightness)]))

    def fill(self, hex_color):
        """Set all LEDs to a hex colour, e.g. fill(0xFF0000) for red."""
        r = (hex_color >> 16) & 0xFF
        g = (hex_color >> 8)  & 0xFF
        b =  hex_color        & 0xFF
        self.set_all(r, g, b)

    def set_all_pixels(self, pixels):
        """
        Set all 8 LEDs individually in one call.
        pixels: list of 8 (r, g, b) tuples.
        """
        data = bytearray([0x04])
        for r, g, b in list(pixels)[:self.LED_COUNT]:
            data += bytes([_clamp(r), _clamp(g), _clamp(b)])
        while len(data) < 1 + self.LED_COUNT * 3:
            data += bytes([0, 0, 0])
        self._send(bytes(data))

    def show(self):
        """Explicit show (all commands already auto-show; use for timing sync)."""
        self._send(bytes([0x05]))

    def off(self):
        """Turn all LEDs off."""
        self._send(bytes([0x00]))

    def _send(self, data):
        self._dev.write(data)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class _USBCDCWrapper:
    """Thin wrapper so usb.core endpoints look like a write()-able device."""
    def __init__(self, ep_out, ep_in):
        self._ep_out = ep_out
        self._ep_in  = ep_in

    def write(self, data):
        self._ep_out.write(data)

    def read(self, n, timeout=500):
        return bytes(self._ep_in.read(n, timeout=timeout))


def _clamp(v):
    return max(0, min(255, int(v)))
