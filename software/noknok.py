# noknok.py  v1.6
# CircuitPython library for the noknok modular ecosystem
# Raspberry Pi Pico — I2C master ("Conductor")
#
# v1.1 (Sam): added Conductor.check_factory_reset() — hold the knob button 5 s
#             to wipe creds/state and reboot into the noknok-setup AP.
# v1.2 (Sue): check_factory_reset(knob_status) now takes the KnobStatus the
#             product already read, instead of reading the knob itself — a second
#             read was eating the rotation delta and breaking knob control.
# v1.3 (Sue): factory reset no longer wipes noknok_state.json (the I2C address
#             map). A soft reset doesn't power-cycle modules, so they keep their
#             addresses; keeping the map lets the next product find them.
# v1.4 (Sam): app-driven role assignment ("PoC v1 Step 3"). Added
#             Conductor.detect_interaction() — watch a module type for a NEW
#             physical interaction (knob turn/press, LED-button press) and return
#             the UID of the module the customer touched. Added
#             Conductor.append_role() — write a single role->UID entry into
#             noknok_roles.json (compatible with load_roles()). Both are additive;
#             no existing methods changed.
# v1.5 (Sue): detect_interaction() now guides the customer with light + sound —
#             LED buttons go amber (waiting) / green (assigned), a buzzer "ready"
#             beep when a choice is requested, and a green flash + confirm beep on
#             the module they pick. Best-effort; never breaks detection.
# v1.6 (Sue): noknok Display support — TYPE_DISPLAY (0x05) threaded through
#             enumeration / state / roles, plus the NoknokDisplay driver, RGB565
#             colour helpers and a BUILT-IN 8x16 font, so text at ANY pixel size
#             works with no extra font files on the Pico.
#
# Quick start:
#   from noknok import Conductor
#   c = Conductor()
#   c.enumerate()                             # discover all modules (~3 s)
#   c.load_roles()                            # load noknok_roles.json (optional)
#   c.role["volume_knob"].value               # access by role name
#   c.buzzer[0].play(440, 500)                # or by type + index
#   c.ledbutton[0].set_color(255, 0, 0)       # red LED on LED button module
#   c.display[0].text("Hello", size=24)       # text on the display module

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
    TYPE_DISPLAY   = 0x05

    # Standard system commands — reserved ecosystem range 0xB0-0xBF, honoured by
    # every noknok module (see DEV-1 / the bootloader doc). 0xB0 = ENTER_BOOTLOADER.
    CMD_GET_VERSION  = 0xB1   # write 0xB1, read 4 bytes [PROTO, FW_MAJOR, MINOR, PATCH]
    PROTOCOL_VERSION = 0x01   # the standard-command protocol version this lib understands

    def __init__(self, sda=board.GP8, scl=board.GP9, frequency=100_000):
        self._sda, self._scl, self._freq = sda, scl, frequency
        self.i2c = None
        self._init_i2c()   # tolerant: warns + leaves i2c=None if no pull-ups / no bus
        self.buzzer    = []    # NoknokBuzzer instances, indexed by discovery order
        self.knob      = []    # NoknokKnob instances
        self.ledbutton = []    # NoknokLedButton instances
        self.display   = []    # NoknokDisplay instances
        self.leds      = []    # NoknokLEDs (USB) instances, populated by enumerate_usb()
        self.role      = {}    # role_name → module object, populated by load_roles()
        self._registry = {}    # identity (I2C uid_hex / USB serial) → module object

    # ── Low-level I2C ─────────────────────────────────────────────────────────

    def _init_i2c(self):
        """Bring up the I2C bus, tolerantly. If it can't initialise - e.g. no
        pull-ups because no I2C modules are connected (USB-only product, or a
        breakout without host pull-ups) - warn and leave self.i2c = None so the
        USB side still works. Returns True if the bus is up."""
        if self.i2c is not None:
            return True
        try:
            self.i2c = busio.I2C(self._scl, self._sda, frequency=self._freq)
            return True
        except Exception as e:
            print("I2C bus unavailable (%s) - I2C modules will be skipped." % e)
            self.i2c = None
            return False

    def _read(self, addr, n):
        if self.i2c is None:
            return None
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
        if self.i2c is None:
            return False
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.writeto(addr, bytes(data))
            return True
        except OSError:
            return False
        finally:
            self.i2c.unlock()

    # ── Standard system commands (GET_VERSION) ─────────────────────────────────

    def read_version(self, address):
        """
        Read a module's installed firmware version via the standard GET_VERSION
        command (0xB1) at its RUNTIME address. The module replies with 4 bytes:
        [PROTOCOL_VERSION, FW_MAJOR, FW_MINOR, FW_PATCH].

        Returns (protocol_version:int, "MAJOR.MINOR.PATCH":str) on a valid reply,
        or (None, None) if the module does not support the standard command.

        Old / third-party firmware that doesn't implement 0xB1 may return nothing
        OR garbage, so we only trust a reply whose first byte equals
        PROTOCOL_VERSION (0x01). Anything else => "unknown firmware".
        """
        if not self._write(address, [self.CMD_GET_VERSION]):
            return (None, None)
        time.sleep(0.003)                       # let the module latch the command
        buf = self._read(address, 4)
        if buf is None or buf[0] != self.PROTOCOL_VERSION:
            return (None, None)
        return (buf[0], "%d.%d.%d" % (buf[1], buf[2], buf[3]))

    def _apply_version(self, module, address):
        """Populate module.protocol_version / module.firmware_version (both None if
        the module doesn't speak GET_VERSION). Called for every enumerated module."""
        if module is None:
            return
        module.protocol_version, module.firmware_version = self.read_version(address)

    # ── Firmware version checking (against a product manifest) ─────────────────

    # module list attribute -> manifest module_firmware{} key
    # I2C modules carry a runtime address; USB modules are identified by serial.
    _FW_GROUPS     = (("buzzer", "buzzer"), ("knob", "knob"), ("ledbutton", "led_button"),
                      ("display", "display"))
    _USB_FW_GROUPS = (("leds", "usb_leds"),)

    @staticmethod
    def _parse_semver(s):
        """'3.3.0' -> (3, 3, 0). Returns None if missing/unparseable."""
        if not s:
            return None
        try:
            parts = [int(x) for x in str(s).split(".")]
        except ValueError:
            return None
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])

    def _update_decision(self, installed, proto, required):
        """
        Apply the DEV-3 safety policy and return (needs_update:bool, reason:str).
        Auto-update ONLY official, outdated firmware within the SAME major version;
        never silently overwrite firmware we can't positively identify (unknown
        protocol, unparseable version, or a major-version gap -> confirm first).
        """
        req = self._parse_semver(required)
        if req is None:
            return (False, "no required version in manifest")
        if proto != self.PROTOCOL_VERSION or installed is None:
            return (False, "unknown firmware (no GET_VERSION) - confirm before flashing")
        ins = self._parse_semver(installed)
        if ins is None:
            return (False, "unparseable installed version - confirm before flashing")
        if ins[0] != req[0]:
            return (False, "major-version gap %s vs %s - confirm before flashing"
                           % (installed, required))
        if ins < req:
            return (True, "update available %s -> %s" % (installed, required))
        return (False, "up to date (%s)" % installed)

    def _fw_entry(self, m, mf_key, spec, bus, address):
        """Build one firmware_report() entry for module `m`."""
        installed     = getattr(m, "firmware_version", None)
        proto         = getattr(m, "protocol_version", None)
        required      = spec.get("version")
        needs, reason = self._update_decision(installed, proto, required)
        return {
            "type":         mf_key,
            "bus":          bus,            # "i2c" or "usb" — routes update_module()
            "uid":          getattr(m, "_uid_hex", None),
            "address":      address,        # I2C runtime addr, or None for USB
            "installed":    installed,
            "protocol":     proto,
            "required":     required,
            "url":          spec.get("url"),
            "needs_update": needs,
            "reason":       reason,
        }

    def firmware_report(self, manifest_fw):
        """
        Compare every enumerated module's installed firmware against a manifest's
        module_firmware{} block, e.g.
            {"knob": {"version": "2.1.0", "url": "..."}, "usb_leds": {...}}
        Returns one dict per module (both I2C and USB):
            {type, bus, uid, address, installed, protocol, required, url,
             needs_update, reason}
        `bus` is "i2c" (carries `address`) or "usb" (identified by `uid`/serial).
        Single source of truth for PoC v1 (log) and PoC v2 (flash outdated ones).
        """
        manifest_fw = manifest_fw or {}
        report = []
        for list_attr, mf_key in self._FW_GROUPS:
            spec = manifest_fw.get(mf_key, {})
            for m in getattr(self, list_attr):
                report.append(self._fw_entry(m, mf_key, spec, "i2c", m.address))
        for list_attr, mf_key in self._USB_FW_GROUPS:
            spec = manifest_fw.get(mf_key, {})
            for m in getattr(self, list_attr):
                report.append(self._fw_entry(m, mf_key, spec, "usb", None))
        return report

    def log_firmware_report(self, manifest_fw, logfn=print):
        """PoC v1 convenience: run firmware_report() and log one line per module.
        Returns the report list so the caller can also act on needs_update."""
        report = self.firmware_report(manifest_fw)
        for r in report:
            flag  = "UPDATE AVAILABLE" if r["needs_update"] else "ok"
            where = ("0x%02X" % r["address"]) if r["address"] is not None \
                    else ("usb:%s" % (r["uid"] or "?"))
            logfn("  fw %-11s %-14s installed=%s required=%s  [%s] %s"
                  % (r["type"], where, r["installed"], r["required"],
                     flag, r["reason"]))
        return report

    # ── Firmware update (OTA) ──────────────────────────────────────────────────
    # Routes a firmware_report() entry to the right OTA flasher: the I2C
    # ModuleFlasher (module_flasher.py) or the USB UsbModuleFlasher (noknok_usb.py).
    # Both are lazily imported so a single-bus product never loads the other stack.

    def update_module(self, entry, image, progress=None):
        """
        Flash one module's firmware. `entry` is a firmware_report() dict (needs
        'bus' + 'address'/'uid'); `image` is the offset-linked app .bin as bytes.

        NOTE: the module RE-ENUMERATES after a flash (new I2C address / new USB
        handle), so the Conductor's existing instance for it goes stale — call
        enumerate()/enumerate_usb()/enumerate_all() afterwards to refresh. update_all()
        does this for you. Returns True on success; raises on failure.
        """
        bus = entry.get("bus")
        if bus == "i2c":
            from module_flasher import ModuleFlasher
            ModuleFlasher(self.i2c).flash(image, runtime_addr=entry.get("address"),
                                          progress=progress)
            return True
        if bus == "usb":
            import noknok_usb
            noknok_usb.UsbModuleFlasher().flash(image, serial=entry.get("uid"),
                                                progress=progress)
            return True
        raise ValueError("update_module: unknown bus %r" % bus)

    def update_all(self, manifest_fw, get_image, progress=None, logfn=print):
        """
        Flash every module that firmware_report() flags needs_update.

        `get_image(entry) -> bytes` supplies the app .bin for an entry — INJECT the
        source: a local-file reader on the bench, or a WiFi downloader of
        entry['url'] in provisioning. (Keeps this method network-agnostic and
        bench-testable.)

        Re-enumerates at the end so the Conductor's module instances are fresh.
        Returns the list of attempted entries, each with added 'updated':bool and
        'error':str|None.
        """
        todo = [r for r in self.firmware_report(manifest_fw) if r["needs_update"]]
        if not todo:
            logfn("Firmware: all modules up to date.")
            return []
        done = []
        for r in todo:
            r2 = dict(r)
            try:
                image = get_image(r)
                if not image:
                    raise ValueError("no image available")
                logfn("Updating %s (%s -> %s) over %s..."
                      % (r["type"], r["installed"], r["required"], r["bus"]))
                self.update_module(r, image, progress=progress)
                r2["updated"], r2["error"] = True, None
                logfn("  OK")
            except Exception as e:
                r2["updated"], r2["error"] = False, str(e)
                logfn("  FAILED: %s" % e)
            done.append(r2)
        logfn("Re-enumerating after updates...")
        self.enumerate_all()
        return done

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
        self.display   = []
        self._registry = {}
        self.role      = {}

        if not self._init_i2c():
            print("  No I2C bus (no pull-ups / no I2C modules) - skipping I2C enumeration.")
            return 0

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
            elif module_type == self.TYPE_DISPLAY:
                module    = NoknokDisplay(self.i2c, address=addr)
                type_name = "noknokdisplay"
                self.display.append(module)
            else:
                module    = None
                type_name = f"unknown(0x{module_type:02X})"

            if module is not None:
                module._uid_hex = uid_hex
                self._apply_version(module, addr)

            self._registry[uid_hex] = module
            new_found += 1
            print(f"  {type_name} → 0x{addr:02X}  UID: {uid_hex}  [new]")

            time.sleep(0.02)

        # ── Step 3: Save state ────────────────────────────────────────────────
        self._save_state()

        # ── Summary ───────────────────────────────────────────────────────────
        total = sum([len(self.buzzer), len(self.knob), len(self.ledbutton),
                     len(self.display)])
        if new_found == 0 and restored > 0:
            print(f"No new modules. {restored} module(s) already assigned:")
            for uid, m in self._registry.items():
                if m: print(f"  {type(m).__name__} at 0x{m.address:02X}  UID: {uid}")
        else:
            print(f"Done — {total} module(s) ({restored} restored, {new_found} new).")
        return total

    # ── USB module discovery (lazy: noknok_usb only loaded if used) ────────────

    def enumerate_usb(self, dp=None, dm=None):
        """
        Discover noknok USB modules (the LED ring, future USB modules) on the USB
        host port and fold them into this Conductor's registry, keyed by each
        module's serial — its chip-UID, the USB counterpart of the I2C UID — so
        by_uid() and the role system work the same across both buses.

        `noknok_usb` is imported HERE (not at module top) so I2C-only products
        never load the USB stack. Pins default to the noknok standard
        GP16 (D+) / GP17 (D-). Returns the number of USB modules found.
        """
        try:
            import noknok_usb
        except ImportError as e:
            print("USB stack unavailable (%s) - skipping USB enumeration." % e)
            return 0
        if not noknok_usb.available():
            print("No USB host support on this build - skipping USB enumeration.")
            return 0

        print("Enumerating noknok USB modules...")
        self.leds = []
        try:
            found = noknok_usb.discover(dp, dm)
        except Exception as e:
            print("  USB discovery failed:", e)
            return 0

        for serial, type_name, module in found:
            if type_name == "noknokleds":
                self.leds.append(module)
            self._registry[serial] = module     # serial is already lower-case
            print("  %s  serial: %s  fw: %s"
                  % (type_name, serial, module.firmware_version))
        print("USB: %d module(s)." % len(found))
        return len(found)

    def enumerate_all(self, dp=None, dm=None):
        """
        Enumerate BOTH buses for a mixed product: I2C modules first, then USB.
        USB modules join the SAME registry, so by_uid()/roles span both buses.
        Call this (instead of enumerate()) for products that mix I2C + USB.
        Returns the total module count.
        """
        n = self.enumerate()
        n += self.enumerate_usb(dp, dm)
        return n

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
                elif isinstance(module, NoknokDisplay):
                    t = self.TYPE_DISPLAY
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
            elif type_code == self.TYPE_DISPLAY:
                module = NoknokDisplay(self.i2c, address=addr)
                module._uid_hex = uid_hex
                self.display.append(module)
            else:
                module = None

            if module is not None:
                self._apply_version(module, addr)
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
            elif isinstance(module, NoknokDisplay):
                print("  → Lighting up the display so you can identify it...")
                module.role_cue(True)
                time.sleep(1.5)
                module.role_cue(False)

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

    # ── App-driven role assignment (v1.4) ──────────────────────────────────────
    # Added v1.4 (Sam): the noknok app drives role assignment over the AP HTTP
    # connection. The app asks the customer to interact with a specific module
    # ("press the button you want for OK"); detect_interaction() watches the
    # modules of that type and returns the UID of the one that was touched.
    # append_role() then persists that role->UID mapping to noknok_roles.json.

    # module_type strings accepted by detect_interaction(), mapped to the list
    # attribute on this Conductor that holds those module instances.
    _ROLE_TYPE_LISTS = {
        "knob":       "knob",
        "led_button": "ledbutton",
        "buzzer":     "buzzer",
        "leds":       "leds",
        "display":    "display",
    }

    # Role-assignment method per module type: "input" = the customer interacts
    # with the module (detect_interaction); "output" = the Conductor cues each
    # candidate and the customer confirms via the app (cue-and-confirm). Mirrors
    # each driver's ROLE_SELECT, kept here too so the mode is known even when no
    # module of that type is currently connected. Spans I2C and USB uniformly.
    _ROLE_SELECT = {
        "knob":       "input",
        "led_button": "input",
        "buzzer":     "output",
        "leds":       "output",
        "display":    "output",   # a display can't be "pressed" — cue-and-confirm
    }

    def _modules_for_type(self, module_type):
        """Return the module list for a module_type string, or None if unknown."""
        attr = self._ROLE_TYPE_LISTS.get(str(module_type).lower())
        if attr is None:
            return None
        return getattr(self, attr, None)

    def role_select_mode(self, module_type):
        """How a module type is role-assigned: "input" (customer interacts) or
        "output" (cue-and-confirm via the app), or None if the type is unknown.
        Prefers a live module's ROLE_SELECT, falling back to the static table."""
        mods = self._modules_for_type(module_type)
        if mods:
            mode = getattr(mods[0], "ROLE_SELECT", None)
            if mode:
                return mode
        return self._ROLE_SELECT.get(str(module_type).lower())

    def role_candidates(self, module_type, exclude=None):
        """Identities (uid_hex / USB serial) of all modules of a type, minus any
        in `exclude`. The app cycles through these for OUTPUT modules during
        cue-and-confirm. Order = discovery order."""
        mods = self._modules_for_type(module_type) or []
        excluded = set()
        if exclude:
            for u in exclude:
                if u:
                    excluded.add(str(u).lower().replace("-", "").replace(" ", ""))
        out = []
        for m in mods:
            uid = getattr(m, "_uid_hex", None)
            if uid is None:
                continue
            if uid.lower().replace("-", "").replace(" ", "") in excluded:
                continue
            out.append(uid)
        return out

    def role_cue(self, identity, on=True):
        """Activate (on=True) or clear (on=False) the role-assignment cue on a
        specific module — for OUTPUT modules (buzzer beeps, LED ring lights) during
        cue-and-confirm. `identity` = uid_hex / serial. Returns True if cued."""
        m = self.by_uid(identity)
        fn = getattr(m, "role_cue", None) if m is not None else None
        if fn is None:
            return False
        try:
            fn(on)
            return True
        except Exception:
            return False

    # Role-assignment feedback colours (LED button): amber = waiting for a role,
    # green = assigned. Best-effort cues so the customer is guided by light + sound.
    _ROLE_COLOR_PENDING  = (180, 120, 0)   # amber / yellow
    _ROLE_COLOR_ASSIGNED = (0, 180, 0)     # green

    def _role_cue_ready(self, module_type, modules, excluded):
        """Light the modules up for selection and play a 'make a choice' beep.
        LED buttons: already-assigned (excluded) -> green, the rest -> amber.
        All outputs best-effort (a missing LED/buzzer never breaks detection)."""
        if module_type == "led_button":
            for m in modules:
                uid  = getattr(m, "_uid_hex", "") or ""
                norm = uid.lower().replace("-", "").replace(" ", "")
                try:
                    if norm in excluded:
                        m.set_color(*self._ROLE_COLOR_ASSIGNED)
                    else:
                        m.set_color(*self._ROLE_COLOR_PENDING)
                except Exception:
                    pass
        if self.buzzer:
            try:
                self.buzzer[0].play(660, 150, 60)   # short "ready — choose now" beep
            except Exception:
                pass

    def _role_cue_confirm(self, module):
        """Confirm a just-picked module: turn it green + a confirmation beep."""
        try:
            module.set_color(*self._ROLE_COLOR_ASSIGNED)
        except Exception:
            pass   # not every module type has an LED (e.g. a knob)
        if self.buzzer:
            try:
                self.buzzer[0].tune(self.buzzer[0].BEEP_OK)
            except Exception:
                pass

    def detect_interaction(self, module_type, timeout=20.0, exclude=None):
        """
        Watch all modules of `module_type` and return the UID (hex string) of the
        first one the customer physically interacts with, or None on timeout.

        module_type : "knob", "led_button", or "buzzer".
        timeout     : seconds to wait for an interaction (default 20 s).
        exclude     : optional list/set of uid_hex strings to ignore (modules
                      that have already been assigned a role).

        Requires the Conductor to be enumerated already (the caller ensures this).
        Returns None if there are no modules of that type.

        What counts as a NEW interaction:
          knob       : a read with delta != 0 (rotation) OR a button press edge
                       (was not pressed, now pressed).
          led_button : a button press edge (was not pressed, now pressed).
        The press_event edge flag is unreliable, so we detect presses via the
        .pressed level edge instead.

        A baseline read of every candidate is taken first to clear any pending
        knob delta and capture the current pressed state, so a button already
        held when detection starts does not count as a new interaction.
        Robust to read() returning None (those samples are skipped).
        Non-destructive to enumeration/state.
        """
        modules = self._modules_for_type(module_type)
        if not modules:
            return None

        # Normalise the exclude set to comparable uid_hex strings.
        excluded = set()
        if exclude:
            for u in exclude:
                if u:
                    excluded.add(str(u).lower().replace("-", "").replace(" ", ""))

        # Candidate (uid_hex, module) pairs, skipping excluded modules.
        candidates = []
        for m in modules:
            uid = getattr(m, "_uid_hex", None)
            if uid is None:
                continue
            if uid.lower().replace("-", "").replace(" ", "") in excluded:
                continue
            candidates.append((uid, m))

        if not candidates:
            return None

        # ── Guide the customer: light up the candidates + a "ready" beep ──────
        self._role_cue_ready(module_type, modules, excluded)

        # ── Baseline: one read each to clear knob delta and capture pressed ──
        last_pressed = {}
        for uid, m in candidates:
            s = m.read()
            last_pressed[uid] = bool(s.pressed) if s is not None else False

        # ── Watch for a new interaction ──────────────────────────────────────
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for uid, m in candidates:
                s = m.read()
                if s is None:
                    continue  # transient I2C error — skip this sample

                # Knob rotation counts as an interaction.
                if getattr(s, "delta", 0):
                    self._role_cue_confirm(m)
                    return uid

                # Button press: rising edge on .pressed (was up, now down).
                pressed = bool(s.pressed)
                if pressed and not last_pressed.get(uid, False):
                    self._role_cue_confirm(m)
                    return uid
                last_pressed[uid] = pressed

            time.sleep(0.04)

        return None

    def append_role(self, role_id, uid_hex, filename="noknok_roles.json"):
        """
        Add or update a single role->UID entry in noknok_roles.json and write it
        back. Compatible with load_roles() ({role_name: uid_hex} format).

        Reads the existing file if present, sets data[role_id] = normalised uid
        (lowercase, '-' and spaces stripped), and writes the whole dict back.
        Creates the file if absent. All file IO is wrapped so a read-only
        filesystem or malformed file can't crash the caller. Returns True on a
        successful write, False otherwise.
        """
        uid = str(uid_hex).lower().replace("-", "").replace(" ", "")

        data = {}
        try:
            with open(filename, "r") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
        except (OSError, ValueError):
            data = {}   # file absent or malformed — start fresh

        data[role_id] = uid

        try:
            with open(filename, "w") as f:
                json.dump(data, f)
            return True
        except OSError:
            return False   # read-only filesystem — silently fail

    # ── Factory reset ─────────────────────────────────────────────────────────
    # Added v1.1 (Sam): hold the knob button for 5 s to wipe all credentials /
    # state and reboot into the noknok-setup provisioning AP. Call once per
    # product main-loop iteration: ks = knb.read(); c.check_factory_reset(ks)

    # Files removed on reset. We wipe the credentials (wifi.json), the product
    # script (product.py) and the product-level role map (noknok_roles.json).
    # We deliberately KEEP noknok_state.json — it's the I2C address map. A reset
    # reboots the Pico but does NOT power-cycle the modules, so they keep their
    # assigned addresses; the map lets the next product find them again. The
    # restore logic self-heals if the hardware changed (pings + skips missing,
    # discovers new at 0x7F), so keeping it is safe.
    _RESET_FILES = ("wifi.json", "product.py", "noknok_roles.json")

    def check_factory_reset(self, knob_status, hold_seconds=5.0):
        """
        Non-blocking factory-reset watchdog. Call ONCE per main-loop iteration,
        passing the KnobStatus you already read this loop:

            ks = knb.read()
            c.check_factory_reset(ks)

        Pass the status in (rather than reading the knob here) so there is only
        ONE knob read per loop. A second read would consume the rotation delta
        (it auto-clears on read) and the product would never see the knob turn.

        Hold the knob button continuously for `hold_seconds` (default 5 s) to
        wipe credentials/state and reboot into the noknok-setup AP. Releasing
        the button at any point resets the timer.

        Escalating, best-effort feedback (a missing buzzer/LED never breaks it):
          ~3 s held  → short warning beep + LED flash (once)
          5 s held   → confirmation beep + LED flash, then wipe & reboot
        """
        # Use the status the product already read — treat None as not-pressed.
        pressed = bool(knob_status is not None and knob_status.pressed)

        now = time.monotonic()

        # Released (or read failed) → reset the hold timer and bail.
        if not pressed:
            self._reset_hold_start = None
            self._reset_warned     = False
            return

        # First frame of a press → start the timer.
        if getattr(self, "_reset_hold_start", None) is None:
            self._reset_hold_start = now
            self._reset_warned     = False
            return

        held = now - self._reset_hold_start

        # ~3 s warning (fire once per hold).
        if held >= 3.0 and not getattr(self, "_reset_warned", False):
            self._reset_warned = True
            self._reset_feedback(warn=True)

        # Target reached → confirm and reset.
        if held >= hold_seconds:
            self._reset_feedback(warn=False)   # confirmation
            self._do_factory_reset()

    def _reset_feedback(self, warn):
        """Best-effort buzzer + LED feedback. Each output wrapped so a missing
        module can never break the reset path."""
        # Buzzer: low tone for warning, higher confirmation tone.
        if self.buzzer:
            try:
                if warn:
                    self.buzzer[0].play(220, 150, 60)   # low warn beep
                else:
                    self.buzzer[0].play(880, 250, 80)   # confirmation beep
            except Exception:
                pass
        # LED button flash.
        if self.ledbutton:
            try:
                if warn:
                    self.ledbutton[0].set_color(60, 30, 0)    # dim amber warn
                else:
                    self.ledbutton[0].set_color(120, 0, 0)    # red confirm
            except Exception:
                pass

    def _do_factory_reset(self):
        """Wipe credentials/state and reboot into the provisioning AP."""
        import os
        print("[reset] Factory reset triggered — wiping credentials and state.")
        for fname in self._RESET_FILES:
            try:
                os.remove(fname)
                print(f"[reset] removed {fname}")
            except OSError:
                pass   # already absent / read-only — ignore
        # Let the confirmation beep/flash finish before the board drops out.
        time.sleep(0.8)
        print("[reset] rebooting...")
        import microcontroller
        microcontroller.reset()


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

    ROLE_SELECT = "output"   # role assignment via cue-and-confirm (output-only module)

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

    def role_cue(self, on=True):
        """Role-assignment cue (output module): beep to identify THIS physical
        buzzer during cue-and-confirm. on=False stops it."""
        if on:
            self.play(880, 200, 80)
        else:
            self.stop()

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

    ROLE_SELECT = "input"   # role assignment by interaction (rotate or press)

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

    ROLE_SELECT = "input"   # role assignment by interaction (press the button)

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

# ═════════════════════════════════════════════════════════════════════════════
# COLOURS — shared by the display driver (and anything else that needs RGB565)
# ═════════════════════════════════════════════════════════════════════════════
#
# Everywhere a colour is accepted you may pass EITHER form — whichever you find
# easier to read:
#     0xFF8800            a 24-bit 0xRRGGBB int (same as CSS / HTML hex)
#     (255, 136, 0)       an (r, g, b) tuple, 0-255 each
# rgb565() converts to the 16-bit format the panel actually wants.

# Named colours (0xRRGGBB) — import them: from noknok import WHITE, RED, ...
BLACK      = 0x000000
WHITE      = 0xFFFFFF
RED        = 0xFF0000
GREEN      = 0x00FF00
BLUE       = 0x0000FF
YELLOW     = 0xFFFF00
CYAN       = 0x00FFFF
MAGENTA    = 0xFF00FF
ORANGE     = 0xFF8000
PURPLE     = 0x8000FF
PINK       = 0xFF4080
LIME       = 0x80FF00
GREY       = 0x808080
GRAY       = GREY          # both spellings, so neither one is "wrong"
DARK_GREY  = 0x303030
DARK_GRAY  = DARK_GREY
NOKNOK     = 0x0097FF      # noknok brand blue

# Handy lookup so a UI (or the test script) can offer colours by name.
COLORS = {
    "black": BLACK, "white": WHITE, "red": RED, "green": GREEN, "blue": BLUE,
    "yellow": YELLOW, "cyan": CYAN, "magenta": MAGENTA, "orange": ORANGE,
    "purple": PURPLE, "pink": PINK, "lime": LIME, "grey": GREY, "gray": GREY,
    "darkgrey": DARK_GREY, "darkgray": DARK_GREY, "noknok": NOKNOK,
}


def rgb565(color):
    """
    Convert a colour to the panel's 16-bit RGB565 value.

    Accepts:
        0xRRGGBB        24-bit int, e.g. 0xFF8800
        (r, g, b)       tuple/list, 0-255 each
        "red"           a name from COLORS
        already-565     ints are treated as 0xRRGGBB, so if you already have a
                        565 value pass it through rgb565_raw() instead.
    """
    if isinstance(color, str):
        c = COLORS.get(color.strip().lower().replace(" ", "").replace("_", ""))
        if c is None:
            raise ValueError("unknown colour name: %r" % color)
        color = c
    if isinstance(color, (tuple, list)):
        r, g, b = color[0], color[1], color[2]
    else:
        color = int(color)
        r = (color >> 16) & 0xFF
        g = (color >> 8) & 0xFF
        b = color & 0xFF
    r = 0 if r < 0 else (255 if r > 255 else int(r))
    g = 0 if g < 0 else (255 if g > 255 else int(g))
    b = 0 if b < 0 else (255 if b > 255 else int(b))
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def rgb565_raw(value):
    """Pass a colour through that is ALREADY a 16-bit RGB565 value."""
    return int(value) & 0xFFFF


# ═════════════════════════════════════════════════════════════════════════════
# BUILT-IN 8x16 FONT
# ═════════════════════════════════════════════════════════════════════════════
#
# A complete 8x16 pixel font (ASCII 32-126 + Latin-1 160-255, so umlauts and
# accents work) shipped INSIDE this library as base64. That is deliberate: the
# display module only stores a couple of small fonts in its own tiny flash, so
# when you ask for a text size the module can't do natively, the Pico scales
# THIS font and sends the finished pixels. No font file ever has to be copied
# onto the Pico — text at any pixel size just works, out of the box.
#
# Layout: 16 bytes per glyph, one byte per pixel row, MSB = leftmost pixel.

_FONT8X16_B64 = (
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAYGBgYGBgYABgYAAAAAAAAfn5+AAAAAAAAAAAAAAAAADY2"
    "Nv88LP9sbGwAAAAAAAw+aPh4eB4fHxb8MDAAAAAA897e/BgYP39vzwAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAYGBgAAAAAAAAAAAAAAAAGDBg4MDAwMDAwOBgMAAAAIDAYHAwMDAwMDBwYMAAA"
    "AAAYfjw8fhgAAAAAAAAAAAAAAAAAGBgY/xgYGAAAAAAAAAAAAAAAAAAAGBwcGAAAAAAAAAAA"
    "AAA+AAAAAAAAAAAAAAAAAAAAAAA4OAAAAAAAAAYGDgwMGBgwMDBgYAAAAAA8ZmfH3/vj5mY8"
    "AAAAAAAAGHhYGBgYGBgYfwAAAAAAADxmBgYGDBw4cH8AAAAAAAB8BgYOPAYGBgZ8AAAAAAAA"
    "Dh4eNmZmxv8GBgAAAAAAAH5gYGB8BgYGDnwAAAAAAAAeMGBgfuZjY2Y8AAAAAAAA/wcGDgwM"
    "GBgwMAAAAAAAAD5mZnY8fmfnZjwAAAAAAAA8ZsbHZ38GBgx4AAAAAAAAAAAAGBgAAAAYGAAA"
    "AAAAAAAAABgYAAAAGBwcGAAAAAAAAAYMOHBwOAwGAAAAAAAAAAAAAH8AfwAAAAAAAAAAAAAA"
    "YDgcDg4cOGAAAAAAAAA4DAYGBjwwADg4AAAAAAAAPndjw9///+///sDmAAAAABw8PD5mZmb/"
    "w8MAAAAAAAB8ZmZmfGZnZ2Z8AAAAAAAAPnJg4MDA4GByPgAAAAAAAPzGx8PDw8PHzvwAAAAA"
    "AAB+YGBgfmBgYGB+AAAAAAAAfmBgYGB+YGBgYAAAAAAAAD5zYMDAz8Pjcz8AAAAAAADDw8PD"
    "/8PDw8PDAAAAAAAAfhgYGBgYGBgYfgAAAAAAAH4ODg4ODg4OTHgAAAAAAABnbmx4cHB4bGZn"
    "AAAAAAAAYGBgYGBgYGBgfwAAAAAAAOfn5///29vDw8MAAAAAAADn9/f3///v7+/nAAAAAAAA"
    "PGbjw8PDw8NmPAAAAAAAAHxmZ2dmfGBgYGAAAAAAAAA8ZuPDw8PD52Y8GB0AAAAAfGZmZnxs"
    "bGZmZwAAAAAAAD5g4GB4HgYHBvwAAAAAAAD/GBgYGBgYGBgYAAAAAAAAw8PDw8PDw+dmPAAA"
    "AAAAAMPD42ZmZjw8PBgAAAAAAADDw8Pb29///+dnAAAAAAAA42Z+PBw8PH5mxwAAAAAAAMPn"
    "Zj48GBgYGBgAAAAAAAB/Bg4MGBgwMGB/AAAAAAA8MDAwMDAwMDAwMDAwAAAAAGBgMDAYGAwM"
    "DAYGAwAAADwMDAwMDAwMDAwMDAwAAAAAGDw8ZmYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AABwGAAAAAAAAAAAAAAAAAAAAAAAPGYGfmZmfgAAAAAAAGBgYH52Y2NnZnwAAAAAAAAAAAA+"
    "cGBgYHA+AAAAAAAABgYGPmbmxuZufgAAAAAAAAAAADxmZ//gYD4AAAAAAAAfGBgY/hgYGBgY"
    "AAAAAAAAAAAAP2ZmZnxgfuPnAAAAAGBgYH52ZmZmZmYAAAAAAAAYGAB4GBgYGBh/AAAAAAAA"
    "DAwAfAwMDAwMDAxMAAAAAGBgYGdseHh8bmcAAAAAAAB4GBgYGBgYGBh/AAAAAAAAAAAA///b"
    "29vb2wAAAAAAAAAAAH52ZmZmZmYAAAAAAAAAAAA8ZsPDw2Y8AAAAAAAAAAAAfnZjY2dmfGBg"
    "AAAAAAAAAD5m5sbmbn4GBgAAAAAAAAB+d2NgYGBgAAAAAAAAAAAAPmBwPAYGfAAAAAAAAAAw"
    "MP8wMDAwOB8AAAAAAAAAAABmZmZmZm5+AAAAAAAAAAAAw2ZmZjw8GAAAAAAAAAAAAMPD29v/"
    "fmYAAAAAAAAAAADndjwYPGbnAAAAAAAAAAAAw2ZmbDw8GBgwAAAAAAAAAH4GDBgwMH8AAAAA"
    "AA4YGBgYGGA4GBgYGBgAABgYGBgYGBgYGBgYGBgYAAAAcBgYGBgcBhwYGBgYGAAAAAAAAAAA"
    "c9vOAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABgYABgYGBgYGAAAAAwMPnxsbGh4eD4Y"
    "GBgAAAAAHjIwcHD+cHBw/gAAAAAAAADnfmZmZmZ+5wAAAAAAAADjZnY8PBh+GH4YAAAAABgY"
    "GBgYGAAAABgYGBgYAAAAAD5wcDh8Zmc+HAYGfAAAAAB+fgAAAAAAAAAAAAAAAAAAPGbD//Pz"
    "/8NmPAAAAAAAADwMPGw8AH4AAAAAAAAAAAAAAAAzNmxsNjMAAAAAAAAAAAAAAAAA/gYGAAAA"
    "AAAAAAAAAAAAAD4AAAAAAAAAAAA8Zv//ZjwAAAAAAAAAAAAAPAAAAAAAAAAAAAAAAAAAADxm"
    "ZmY8AAAAAAAAAAAAAAAAGBgY/xgYGAD/AAAAAAAAPCwMHDh+AAAAAAAAAAAAADwMPA4MPAAA"
    "AAAAAAAAAAAOHAAAAAAAAAAAAAAAAAAAAAAAZmZmZmZuf2BgAAAAAD9///9/PwcHBwZmPAAA"
    "AAAAAAAAABwYAAAAAAAAAAAAAAAAAAAAAAAAABgYAAAAADh4GBgYfgAAAAAAAAAAAAA8ZmZm"
    "PAB+AAAAAAAAAAAAAAAA7Gw2Nm7sAAAAAAAAAGPmZmwYGDdvb8MAAAAAAABj5mZsGBg/Y2bP"
    "AAAAAAAA4zb2PPgYN29vwwAAAAAAAAAAABwcAAw8YGBgMABwGAAcPDw+ZmZm/8PDAAAADhgA"
    "HDw8PmZmZv/DwwAAABh8ABw8PD5mZmb/w8MAAAB+bgAcPDw+ZmZm/8PDAAAAbm4AHDw8PmZm"
    "Zv/DwwAAADw8HBw8PD5mZmb/w8MAAAAAAAAfHDw8b2xs/MzPAAAAAAAAPnNg4MDA4GBzPhgY"
    "AHAYAH5gYGB+YGBgYH4AAAAOGAB+YGBgfmBgYGB+AAAAGHwAfmBgYH5gYGBgfgAAAG5uAH5g"
    "YGB+YGBgYH4AAABwGAB+GBgYGBgYGBh+AAAADhgAfhgYGBgYGBgYfgAAABh8AH4YGBgYGBgY"
    "GH4AAABubgB+GBgYGBgYGBh+AAAAAAAAfGZnY/NjY2ZufAAAAH5uAOf39/f//+/v7+cAAABw"
    "GAA8ZuPDw8PDw2Y8AAAADhgAPGbjw8PDw8NmPAAAABh8ADxm48PDw8PDZjwAAAB+bgA8ZuPD"
    "w8PDw2Y8AAAAbm4APGbjw8PDw8NmPAAAAAAAAAAAAGZ+PBw+ZwAAAAAABgQ8bu/L29vb93Y8"
    "MGAAcBgAw8PDw8PDw+dmPAAAAA4YAMPDw8PDw8PnZjwAAAAYfADDw8PDw8PD52Y8AAAAbm4A"
    "w8PDw8PDw+dmPAAAAA4YAMPnZj48GBgYGBgAAAAAAABgfGZnY2dmfGBgAAAAAAAAPGZmbHh8"
    "bmdnfgAAAAAAAHAYADxmBn5mZn4AAAAAAAAOHAA8ZgZ+ZmZ+AAAAAAAAGHwAPGYGfmZmfgAA"
    "AAAAAH5uADxmBn5mZn4AAAAAAAB+fgA8ZgZ+ZmZ+AAAAAAA8PDwAPGYGfmZmfgAAAAAAAAAA"
    "AH7bG//Y2P8AAAAAAAAAAAA+cGBgYHA+GBgAAAAAcBgAPGZn/+BgPgAAAAAAAA4cADxmZ//g"
    "YD4AAAAAAAAYfAA8Zmf/4GA+AAAAAAAAfn4APGZn/+BgPgAAAAAAAHAYAHgYGBgYGH8AAAAA"
    "AAAOHAB4GBgYGBh/AAAAAAAAGHwAeBgYGBgYfwAAAAAAAH5+AHgYGBgYGH8AAAAAAAA+fAw+"
    "ZubH5mY8AAAAAAAAfm4AfnZmZmZmZgAAAAAAAHAYADxmw8PDZjwAAAAAAAAOHAA8ZsPDw2Y8"
    "AAAAAAAAGHwAPGbDw8NmPAAAAAAAAH5uADxmw8PDZjwAAAAAAAB+fgA8ZsPDw2Y8AAAAAAAA"
    "AAAAGBgA/wAYGAAAAAAAAAAGDDxu29vbdjwwIAAAAABwGABmZmZmZm5+AAAAAAAADhwAZmZm"
    "ZmZufgAAAAAAABh8AGZmZmZmbn4AAAAAAAB+fgBmZmZmZm5+AAAAAAAADhwAw2ZmbDw8GBgw"
    "AAAAAGBgYH52Y2NnZnxgYAAAAAB+fgDDZmZsPDwYGDA="
)

_FONT8X16 = None          # decoded lazily on first use, then cached


def _b64decode(s):
    """Decode base64 -> bytes. Uses binascii when available (every normal
    CircuitPython build has it) and falls back to a tiny pure-Python decoder."""
    try:
        import binascii
        return binascii.a2b_base64(s)
    except Exception:
        pass
    alphabet = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "abcdefghijklmnopqrstuvwxyz0123456789+/")
    lut = {}
    for i, ch in enumerate(alphabet):
        lut[ch] = i
    out, acc, bits = bytearray(), 0, 0
    for ch in s:
        if ch == "=":
            break
        v = lut.get(ch)
        if v is None:
            continue
        acc = (acc << 6) | v
        bits += 6
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return bytes(out)


def _font8x16():
    """Return the built-in 8x16 font as bytes (decoded once, then cached)."""
    global _FONT8X16
    if _FONT8X16 is None:
        _FONT8X16 = _b64decode(_FONT8X16_B64)
    return _FONT8X16


def _glyph_index(ch):
    """Map a character to its slot in the built-in font.
    ASCII 32-126 -> 0..94, Latin-1 160-255 -> 95..190, anything else -> '?'."""
    c = ord(ch)
    if 32 <= c <= 126:
        return c - 32
    if 160 <= c <= 255:
        return 95 + (c - 160)
    return ord("?") - 32


class BdfFont:
    """
    Minimal BDF (bitmap font) reader, so a maker can use their OWN font:

        from noknok import BdfFont
        f = BdfFont("/fonts/myfont.bdf")
        d.text("Hello", size=28, font=f)

    Only what we need is parsed: per-glyph bitmaps keyed by character code.
    Glyphs are normalised into a fixed cell (the font's bounding box), then the
    display driver scales that cell to whatever pixel height you asked for.
    A .bdf PATH may also be passed straight to text(font="/fonts/myfont.bdf").
    """

    def __init__(self, path):
        self.path   = path
        self.width  = 8
        self.height = 16
        self.glyphs = {}      # char code -> list of ints, one per row (MSB left)
        self._parse(path)

    def _parse(self, path):
        code, bbw, bbh, bbx, bby = None, 0, 0, 0, 0
        rows, in_bitmap = [], False
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if in_bitmap:
                    if line.startswith("ENDCHAR"):
                        in_bitmap = False
                        if code is not None:
                            self.glyphs[code] = self._place(rows, bbw, bbh, bbx, bby)
                        rows, code = [], None
                    else:
                        try:
                            rows.append(int(line, 16))
                        except ValueError:
                            pass
                    continue
                if line.startswith("FONTBOUNDINGBOX"):
                    p = line.split()
                    self.width, self.height = int(p[1]), int(p[2])
                    self._base_x, self._base_y = int(p[3]), int(p[4])
                elif line.startswith("ENCODING"):
                    code = int(line.split()[1])
                elif line.startswith("BBX"):
                    p = line.split()
                    bbw, bbh, bbx, bby = int(p[1]), int(p[2]), int(p[3]), int(p[4])
                elif line.startswith("BITMAP"):
                    in_bitmap, rows = True, []
        if not self.glyphs:
            raise ValueError("no glyphs found in %s — is it a real .bdf?" % path)

    def _place(self, rows, bbw, bbh, bbx, bby):
        """Drop a glyph's own bounding box into the font's full cell, so every
        glyph ends up the same width/height and scaling stays simple."""
        cell = [0] * self.height
        src_bytes = (bbw + 7) // 8
        # BDF rows are top-to-bottom; the glyph sits bby pixels above the baseline.
        top = self.height + getattr(self, "_base_y", 0) - bby - bbh
        for i, raw in enumerate(rows[:bbh]):
            y = top + i
            if y < 0 or y >= self.height:
                continue
            # left-align the glyph row inside the cell, honouring its x offset
            shift = (src_bytes * 8) - bbw
            val   = (raw >> shift) if shift > 0 else raw
            xoff  = bbx - getattr(self, "_base_x", 0)
            row   = 0
            for bit in range(bbw):
                if val & (1 << (bbw - 1 - bit)):
                    px = xoff + bit
                    if 0 <= px < self.width:
                        row |= 1 << (self.width - 1 - px)
            cell[y] = row
        return cell

    def rows_for(self, ch):
        """Rows (ints, MSB = leftmost) for a character, or the blank cell."""
        g = self.glyphs.get(ord(ch))
        if g is None:
            g = self.glyphs.get(ord("?"))
        return g or [0] * self.height


# ═════════════════════════════════════════════════════════════════════════════
class NoknokDisplay:
    """
    Driver for the noknok Display Module (noknokdisplay, MODULE_TYPE 0x05).

    The module is a little GPU: the Pico sends drawing COMMANDS over I2C and the
    module's CH32V003 paints the panel. There is no frame buffer anywhere — the
    module draws straight into the panel — so anything you draw stays until you
    draw over it.

    Normally obtained via Conductor.enumerate():
        c = Conductor()
        c.enumerate()
        d = c.display[0]              # by discovery index
        d = c.role["screen"]          # by role name (after load_roles)

    Everyday use:
        d.clear(BLACK)                        # wipe the screen
        d.text("Hello", size=16)              # text, any pixel size you like
        d.text("22.5 C", size=32, x=4, y=40, color=YELLOW)
        d.fill_rect(0, 0, 80, 10, RED)        # a bar
        d.backlight(0.8)                      # 80 % brightness
        d.off() / d.on() / d.sleep()
        print(d.info())                       # 80x160, RGB565, ...

    About `size`
    ------------
    `size` is the PIXEL HEIGHT of the text and it is ALWAYS exact — ask for 27
    and you get 27 pixels tall. Behind the scenes the library picks the fastest
    of three routes automatically; you never have to think about it:
      1. sizes the module can draw itself (8, 16, 24, 32, 48, 64) -> one small
         command, the module renders it (~2 ms).
      2. any other size -> the Pico scales its BUILT-IN 8x16 font and sends the
         finished pixels as a 1-bit-per-pixel blit. No font file needed.
      3. font="/fonts/mine.bdf" -> the Pico uses YOUR font, same blit path.

    About backgrounds
    -----------------
    By default text is drawn on an OPAQUE box in the last colour you cleared to.
    That is what lets a clock tick over cleanly on a panel with no frame buffer:
    the old digits are covered as the new ones are drawn, so there is no flicker.
    Pass bg=None for transparent text, or bg=<colour> to pick the box colour.

    Colours
    -------
    Anywhere a colour is wanted, pass 0xRRGGBB, an (r, g, b) tuple or a name:
        d.text("Hi", color=0xFF8800)
        d.text("Hi", color=(255, 136, 0))
        d.text("Hi", color="orange")
    """

    ROLE_SELECT = "output"   # you can't press a screen — the app cues it instead

    # ── Command bytes (see the firmware header in module-I2C-1.42-display) ────
    _CMD_CLEAR         = 0x01   # [0x01, colHi, colLo]
    _CMD_FILL_RECT     = 0x02   # [0x02, x, y, w, h, colHi, colLo]
    _CMD_DRAW_TEXT     = 0x03   # [0x03, x, y, style, fgHi, fgLo, bgHi, bgLo, chars...]
    _CMD_DRAW_ICON     = 0x04   # [0x04, x, y, iconId, scale, fgHi, fgLo, bgHi, bgLo]
    _CMD_BLIT_BEGIN    = 0x05   # [0x05, x, y, w, h, fgHi, fgLo, bgHi, bgLo, flags]
    _CMD_BLIT_DATA     = 0x06   # [0x06, <=64 bytes of 1bpp rows]
    _CMD_SET_BACKLIGHT = 0x10   # [0x10, level 0-255]
    _CMD_DISPLAY       = 0x12   # [0x12, 0=off 1=on 2=sleep]
    _CMD_GET_INFO      = 0x15   # [0x15] then read 5 bytes

    # The module's I2C receive buffer is 72 bytes, so one command must fit in it.
    _MAX_BLIT_CHUNK = 64
    _MAX_TEXT_CHARS = 60

    # Module-side fonts: index -> (cell width, cell height). Index 0 = the small
    # 6x8 font, index 1 = the 8x16 font. Scale is an integer 1-4.
    _MODULE_FONTS = {0: (6, 8), 1: (8, 16)}

    # Pixel heights the MODULE can render on its own: height -> (font index, scale).
    # Anything not in here is rendered on the Pico and blitted instead.
    _NATIVE_SIZES = {
        8:  (0, 1),
        16: (1, 1),
        24: (0, 3),
        32: (1, 2),
        48: (1, 3),
        64: (1, 4),
    }

    # Used only if GET_INFO never answers (very old firmware / bus trouble), so
    # nothing crashes. Real geometry always comes from the module itself.
    _FALLBACK_W = 80
    _FALLBACK_H = 160

    def __init__(self, i2c, address=0x08):
        self.i2c        = i2c
        self.address    = address
        self._uid_hex   = None    # set by Conductor.enumerate()
        self._info      = None    # cached DisplayInfo from GET_INFO
        self._bg        = BLACK   # last clear() colour = default text background
        self._backlight = None    # last level we set (0-255), for role_cue()
        self.auto_wait  = True    # wait for the module to finish before each draw
        # Filled in by Conductor.enumerate() via GET_VERSION; safe defaults here
        # so a hand-built instance never raises AttributeError.
        self.protocol_version = None
        self.firmware_version = None

    # ── Low-level I2C (same try_lock/OSError pattern as the other drivers) ────

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

    def _draw(self, data):
        """Send a drawing command, first waiting for any previous draw to finish
        (the module paints over SPI and can't take a new command mid-draw)."""
        if self.auto_wait:
            self.wait_ready()
        return self._send(data)

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self):
        """Return (busy, last_error) from the module, or (False, None) on error."""
        buf = self._read_raw(2)
        if buf is None:
            return (False, None)
        return (bool(buf[0] & 0x01), buf[1])

    def wait_ready(self, timeout=1.0):
        """Block until the module has finished drawing. Returns True if it is
        idle, False on timeout or I2C error. Best-effort — never raises."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            buf = self._read_raw(2)
            if buf is None:
                return False
            if not (buf[0] & 0x01):
                return True
            time.sleep(0.002)
        return False

    def info(self, refresh=False):
        """
        Ask the module what it is: size, colour depth, how many built-in fonts
        and icons it has. Returns a DisplayInfo (cached after the first call),
        or None if the module didn't answer.

        This is why one driver can serve every noknok display — geometry is
        asked for, never hard-coded.
        """
        if self._info is not None and not refresh:
            return self._info
        if not self._send([self._CMD_GET_INFO]):
            return None
        time.sleep(0.003)                     # let the module load its reply
        buf = self._read_raw(5)
        if buf is None or buf[0] == 0 or buf[1] == 0:
            return None                       # no / invalid answer, keep fallbacks
        self._info = DisplayInfo(buf)
        return self._info

    @property
    def width(self):
        """Panel width in pixels (from the module; falls back to 80)."""
        i = self.info()
        return i.width if i else self._FALLBACK_W

    @property
    def height(self):
        """Panel height in pixels (from the module; falls back to 160)."""
        i = self.info()
        return i.height if i else self._FALLBACK_H

    # ── Screen control ────────────────────────────────────────────────────────

    def backlight(self, level):
        """
        Set backlight brightness.
            d.backlight(0.8)    # 80 %  (0.0 - 1.0 — the normal way)
            d.backlight(200)    # raw 0-255, if you prefer
        """
        level = float(level)
        raw = int(round(level * 255)) if level <= 1.0 else int(round(level))
        raw = 0 if raw < 0 else (255 if raw > 255 else raw)
        self._backlight = raw
        return self._send([self._CMD_SET_BACKLIGHT, raw])

    def display(self, mode):
        """Low-level power state: 0 = off, 1 = on, 2 = sleep. Prefer on/off/sleep."""
        return self._send([self._CMD_DISPLAY, int(mode) & 0xFF])

    def on(self):
        """Turn the panel on."""
        return self.display(1)

    def off(self):
        """Turn the panel off (backlight and panel dark)."""
        return self.display(0)

    def sleep(self):
        """Put the panel into its low-power sleep state."""
        return self.display(2)

    # ── Drawing ───────────────────────────────────────────────────────────────

    def clear(self, color=BLACK):
        """
        Fill the whole screen with one colour and remember it as the default
        text background (so text() can cover its own footprint cleanly).
        """
        self._bg = color
        c = rgb565(color)
        return self._draw([self._CMD_CLEAR, (c >> 8) & 0xFF, c & 0xFF])

    def fill_rect(self, x, y, w, h, color):
        """Fill a rectangle. Coordinates and sizes are pixels, clipped to the panel."""
        x, y, w, h = self._clip(x, y, w, h)
        if w <= 0 or h <= 0:
            return True
        c = rgb565(color)
        return self._draw([self._CMD_FILL_RECT, x, y, w, h,
                           (c >> 8) & 0xFF, c & 0xFF])

    def _clip(self, x, y, w, h):
        """Clamp a rectangle to the panel (and to the 0-255 byte range)."""
        dw, dh = self.width, self.height
        x, y, w, h = int(x), int(y), int(w), int(h)
        if x < 0:
            w += x
            x = 0
        if y < 0:
            h += y
            y = 0
        if x >= dw or y >= dh:
            return (0, 0, 0, 0)
        w = min(w, dw - x, 255)
        h = min(h, dh - y, 255)
        return (x, y, max(0, w), max(0, h))

    def icon(self, icon_id, x=0, y=0, scale=1, color=WHITE, bg="auto"):
        """
        Draw one of the module's built-in icons.
            d.icon(3, x=10, y=10, scale=2, color=GREEN)
        `bg="auto"` = the last clear() colour, None = transparent.
        (Needs display firmware with the icon set — see info().n_icons.)
        """
        fg = rgb565(color)
        bgc, _ = self._bg_value(bg)
        return self._draw([self._CMD_DRAW_ICON, int(x) & 0xFF, int(y) & 0xFF,
                           int(icon_id) & 0xFF, max(1, min(4, int(scale))),
                           (fg >> 8) & 0xFF, fg & 0xFF,
                           (bgc >> 8) & 0xFF, bgc & 0xFF])

    # ── Text ──────────────────────────────────────────────────────────────────

    def _bg_value(self, bg):
        """Work out the background colour to send. Returns (rgb565, transparent).
        bg="auto" -> last clear() colour; bg=None -> transparent; else that colour."""
        if bg is None:
            return (rgb565(self._bg), True)
        if isinstance(bg, str) and bg == "auto":
            return (rgb565(self._bg), False)
        return (rgb565(bg), False)

    @staticmethod
    def _pack_style(font_index, scale, transparent):
        """
        Build the DRAW_TEXT style byte:
            bits 0-1  font index   (0 = 6x8, 1 = 8x16)
            bits 2-4  scale - 1    (so 1-8; the module supports 1-4)
            bit  7    1 = transparent background (don't paint the box)
        Must match the firmware's decoding — keep the two in step.
        """
        return ((font_index & 0x03)
                | ((max(1, min(8, scale)) - 1) << 2)
                | (0x80 if transparent else 0x00))

    def text(self, s, size=16, color=WHITE, bg="auto", x=0, y=0,
             font=None, wrap=True, line_gap=None):
        """
        Draw text. `size` is the exact PIXEL HEIGHT — any size works.

            d.text("Hello World")                        # 16 px, white, from 0,0
            d.text("22.5", size=32, x=4, y=40, color=YELLOW)
            d.text("tiny", size=11)                      # non-native -> blitted
            d.text("mine", size=28, font="/fonts/a.bdf") # your own font
            d.text("ghost", bg=None)                     # transparent background

        Arguments:
            s        — the text. "\\n" starts a new line.
            size     — pixel height (default 16). Always exact.
            color    — text colour: 0xRRGGBB, (r,g,b) or a name.
            bg       — "auto" (default) = an opaque box in the last clear()
                       colour; None = transparent; or any colour.
            x, y     — top-left corner in pixels.
            font     — None = built-in font; or a BdfFont, or a path to a .bdf.
            wrap     — True (default) wraps to the panel width; False clips.
            line_gap — extra pixels between wrapped lines (default size // 8).

        Returns the y coordinate just BELOW the last line drawn, so you can
        stack text:
            y = d.text("Title", size=24)
            d.text("subtitle", size=12, y=y)
        """
        s = str(s)
        size = int(size)
        if size < 1:
            raise ValueError("size must be at least 1 pixel tall")

        fg = rgb565(color)
        bgc, transparent = self._bg_value(bg)

        # Load a .bdf if a path was passed instead of a BdfFont object.
        if isinstance(font, str):
            font = BdfFont(font)

        # Pick the render route (see the class docstring).
        native = None
        if font is None and size in self._NATIVE_SIZES \
                and self._is_ascii(s.replace("\n", "")):
            native = self._NATIVE_SIZES[size]

        if native is not None:
            font_index, scale = native
            cell_w = self._MODULE_FONTS[font_index][0] * scale
        elif font is not None:
            cell_w = max(1, int(round(font.width * size / float(font.height))))
        else:
            cell_w = max(1, (size + 1) // 2)      # built-in font is 8 wide x 16 high

        # Split into lines that fit the panel.
        avail = max(1, self.width - int(x))
        lines = self._layout(s, cell_w, avail, wrap)

        gap    = (size // 8) if line_gap is None else int(line_gap)
        line_h = size + max(0, gap)
        cur_y  = int(y)

        for line in lines:
            if cur_y >= self.height:
                break                              # off the bottom — stop
            if line:
                if native is not None:
                    self._text_native(int(x), cur_y, line, fg, bgc, transparent,
                                      native[0], native[1])
                else:
                    self._text_blit(int(x), cur_y, line, size, cell_w,
                                    fg, bgc, transparent, font)
            cur_y += line_h
        return cur_y

    @staticmethod
    def _is_ascii(s):
        """True if every character is in the module's own ASCII font range."""
        for ch in s:
            if not (32 <= ord(ch) <= 126):
                return False
        return True

    @staticmethod
    def _layout(s, cell_w, avail_px, wrap):
        """Break text into display lines. Honours "\\n"; wraps on spaces when it
        can and hard-breaks a too-long word when it can't."""
        max_chars = max(1, avail_px // cell_w)
        out = []
        for para in s.split("\n"):
            if not wrap:
                out.append(para[:max_chars])
                continue
            if not para:
                out.append("")
                continue
            while len(para) > max_chars:
                cut = para.rfind(" ", 0, max_chars + 1)
                if cut <= 0:
                    cut = max_chars           # one long word — break it
                out.append(para[:cut].rstrip())
                para = para[cut:].lstrip()
            out.append(para)
        return out

    def _text_native(self, x, y, line, fg, bg, transparent, font_index, scale):
        """Route 1: let the module render with its own font (fastest path)."""
        line  = line[:self._MAX_TEXT_CHARS]
        style = self._pack_style(font_index, scale, transparent)
        payload = [self._CMD_DRAW_TEXT, x & 0xFF, y & 0xFF, style,
                   (fg >> 8) & 0xFF, fg & 0xFF, (bg >> 8) & 0xFF, bg & 0xFF]
        payload.extend([ord(ch) & 0xFF for ch in line])
        return self._draw(payload)

    def _text_blit(self, x, y, line, size, cell_w, fg, bg, transparent, font):
        """Routes 2 and 3: render on the Pico, send the pixels.

        The glyphs are scaled with nearest-neighbour sampling so ANY pixel height
        is possible, packed 1 bit per pixel (rows padded to whole bytes) and sent
        as BLIT_BEGIN + as many BLIT_DATA chunks as it takes."""
        w = min(cell_w * len(line), max(0, self.width - x), 255)
        h = min(size, max(0, self.height - y), 255)
        if w <= 0 or h <= 0:
            return True

        # Source glyph cell dimensions (built-in font is 8x16).
        src_w  = font.width if font is not None else 8
        src_h  = font.height if font is not None else 16
        table  = None if font is not None else _font8x16()

        row_bytes = (w + 7) // 8
        buf = bytearray(row_bytes * h)

        for i, ch in enumerate(line):
            base_x = i * cell_w
            if base_x >= w:
                break
            if font is not None:
                rows = font.rows_for(ch)
            else:
                gi   = _glyph_index(ch)
                rows = None                       # read from `table` directly
                off  = gi * 16
            for dy in range(h):
                sy = (dy * src_h) // size
                if sy >= src_h:
                    sy = src_h - 1
                src = rows[sy] if font is not None else table[off + sy]
                if not src:
                    continue
                row_base = dy * row_bytes
                for dx in range(cell_w):
                    px = base_x + dx
                    if px >= w:
                        break
                    sx = (dx * src_w) // cell_w
                    if src & (1 << (src_w - 1 - sx)):
                        buf[row_base + (px >> 3)] |= 0x80 >> (px & 7)

        return self._blit(x, y, w, h, fg, bg, transparent, buf)

    def _blit(self, x, y, w, h, fg, bg, transparent, data):
        """Send a 1bpp bitmap to the module: BLIT_BEGIN then BLIT_DATA chunks."""
        ok = self._draw([self._CMD_BLIT_BEGIN, x & 0xFF, y & 0xFF,
                         w & 0xFF, h & 0xFF,
                         (fg >> 8) & 0xFF, fg & 0xFF,
                         (bg >> 8) & 0xFF, bg & 0xFF,
                         0x01 if transparent else 0x00])
        if not ok:
            return False
        step = self._MAX_BLIT_CHUNK
        for i in range(0, len(data), step):
            chunk = data[i:i + step]
            if not self._send(bytes([self._CMD_BLIT_DATA]) + bytes(chunk)):
                return False
        return True

    # ── Role assignment cue (the app's "which screen is this?" step) ──────────

    def role_cue(self, on=True):
        """
        Make THIS display obviously identifiable while the app assigns roles.
        Uses only the always-available primitives (backlight + clear), so it
        works on any display firmware.
        """
        if on:
            self.backlight(1.0)
            self.clear(NOKNOK)
        else:
            self.clear(BLACK)
        return True

    def __repr__(self):
        i = self._info
        geo = ("%dx%d" % (i.width, i.height)) if i else "?"
        return "NoknokDisplay(addr=0x%02X, %s)" % (self.address, geo)


# ─────────────────────────────────────────────────────────────────────────────
class DisplayInfo:
    """
    Result of NoknokDisplay.info() — what the module says about itself.

    Attributes:
        width, height (int)  — panel size in pixels
        depth_code    (int)  — colour format code (0x10 = RGB565)
        depth         (str)  — human-readable colour format
        n_fonts       (int)  — built-in fonts the module can render itself
        n_icons       (int)  — built-in icons
    """
    __slots__ = ("width", "height", "depth_code", "depth", "n_fonts", "n_icons")

    _DEPTHS = {0x10: "RGB565", 0x08: "8-bit", 0x01: "1-bit"}

    def __init__(self, buf):
        self.width      = buf[0]
        self.height     = buf[1]
        self.depth_code = buf[2]
        self.depth      = self._DEPTHS.get(buf[2], "0x%02X" % buf[2])
        self.n_fonts    = buf[3]
        self.n_icons    = buf[4]

    def __repr__(self):
        return ("DisplayInfo(%dx%d, %s, fonts=%d, icons=%d)"
                % (self.width, self.height, self.depth, self.n_fonts, self.n_icons))


# ============================================================================
# USB modules (noknok LEDs, future USB modules) live in noknok_usb.py and are
# driven by the Conductor via enumerate_usb() / enumerate_all(). noknok_usb is
# LAZILY imported (only when a product uses USB modules) so I2C-only products
# don't load the USB stack. See noknok_usb.py for NoknokLEDs + discover().
# ============================================================================


