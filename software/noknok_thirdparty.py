# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
"""
noknok_thirdparty - third-party Qwiic / Stemma QT boards on a noknok brain (core).
DEV-248 (from the RDM-13 exploration). Needs noknok.py 1.30+ (DEV-245: the Conductor clears a
stuck bus before it opens it). Plug boards in with the brain off: hot-plug is not supported.

    from noknok import Conductor
    import noknok_thirdparty as tp

    c = Conductor()
    c.enumerate()            # 1. noknok modules (DEV-119 skips occupied addresses)
    tp.attach(c)             # 2. identify the leftover fixed-address boards

    c.veml7700[0].lux        # per-type lists, like c.buzzer
    for m in c.thirdparty:   # or all of them, with a generic read()
        print(m.TYPE, m.read())

Since 0.2.0 the library is SPLIT: this core holds the registry, attach() and the pull-up
budget; every driver lives in its own file noknok_tp_<type>.(m)py and is imported
only when a chip answers at one of its addresses. A driver that matches nothing is
unloaded again, so a product pays for the core plus the drivers of the boards it has.

Boards without any ID (e.g. the Soldered PIR) are never probed; attach() lists them as
unknown with `candidates`, and the app / product names them:
    tp.assign(c, "soldered_pir", "A", 0x30)            # or attach(c, assign={("A", 0x30): "soldered_pir"})

Identification confidence: "id" (ID register matched), "fingerprint" (read-only or
power-on register values matched), "family" (chip family only, e.g. SSD1306 size unknown),
"user" (named by the user, not probed).

Rules kept: no bit-banging (uses the Conductor's busio buses), no FS writes,
reads return None on an I2C hiccup or a bus that is gone (same contract as noknok modules),
and nothing here raises into the product loop.
"""

import sys
import time

try:                                    # keep the app answered during reads (DEV-34)
    from noknok import _service
except Exception:                       # pragma: no cover - running without noknok
    def _service():
        pass

__version__ = "0.3.1"   # 0.3.1: attach() survives a bad saved naming (unknown type, broken key);
                        #        a transfer on a bus that is gone returns None (ValueError /
                        #        RuntimeError too, not only OSError) (DEV-248 review)
                        # 0.3.0: RESERVED_ADDRESSES removed (no address pool: DEV-246 cancelled);
                        #        user namings + OLED height saved in the Store (remember/forget),
                        #        attach() applies them and caches compact status rows (c.thirdparty_rows);
                        #        bus_clear() / recover_buses() removed (the Conductor does it, DEV-245)
                        # 0.2.2: pull-up check suggests which board to move to the other bus



# ── Registry: type -> addresses. The driver code itself is NOT imported here. ──
# (type, addresses, auto) - auto=False: no ID, attached only by assign().
REGISTRY = (
    ("veml7700",     (0x10,), True),
    ("tmp102",       (0x48, 0x49, 0x4A, 0x4B), True),
    ("ads1115",      (0x48, 0x49, 0x4A, 0x4B), True),
    ("lsm6dsox",     (0x6A, 0x6B), True),
    ("msa311",       (0x62,), True),
    ("ssd1306",      (0x3C, 0x3D), True),
    ("soldered_pir", (0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37), False),
)
TYPES = tuple(t for t, _, _ in REGISTRY)

# No reserved addresses (DEV-246 cancelled, 17 Nov 2026): boards are plugged in with the
# brain off; at boot the Conductor skips every address that answers (DEV-119).


# What the user told the brain about a board (DEV-249 ops thirdparty.assign / .set / .forget):
# Store key "thirdparty" = {"A-30": {"type": "soldered_pir"}, "A-3c": {"height": 64}} (nvm/FRAM,
# never the FAT: ADR-003). attach() reads it by itself, so a product needs no extra code.
STORE_KEY = "thirdparty"


def _key(bus_name, address):
    return "%s-%02x" % (bus_name, address)


def saved():
    """The saved namings / options, {} without a Store (e.g. on a desktop)."""
    try:
        from noknok import store
        return dict(store().get(STORE_KEY) or {})
    except Exception:
        return {}


def remember(bus_name, address, **fields):
    """Save fields for one board (type=..., height=...). Returns the saved entry."""
    from noknok import store
    sv = saved()
    e = dict(sv.get(_key(bus_name, address)) or {})
    e.update(fields)
    sv[_key(bus_name, address)] = e
    store().set(STORE_KEY, sv)
    return e


def forget(bus_name, address):
    """Drop everything saved for one board. True if there was something."""
    from noknok import store
    sv = saved()
    had = sv.pop(_key(bus_name, address), None) is not None
    if had:
        store().set(STORE_KEY, sv)
    return had


def load(type_name):
    """The driver class for `type_name` (imports noknok_tp_<type> on first use)."""
    mod = __import__("noknok_tp_" + type_name)
    return mod.DRIVER


def _unload(type_name):
    sys.modules.pop("noknok_tp_" + type_name, None)


# ── Base class ────────────────────────────────────────────────────────────────

class ThirdPartyModule:
    """One third-party I2C chip at a fixed address. Subclass per chip."""

    TYPE        = "thirdparty"   # manifest / status type string (lower case, no spaces)
    NAME        = "?"            # human name for the app
    VENDOR      = "?"            # board vendor we tested with
    ADDRESSES   = ()             # every address the chip can be strapped to
    CONFIDENCE  = "address"
    AUTO        = True           # False: never probed, only assign()
    PULLUP_OHMS = None           # load on the bus when LOW (None = no pull-ups fitted)
    PULLUP_REMOVABLE = False
    PULLUP_HOWTO = ""            # what the customer does to remove it
    CAPABILITIES = {}            # name -> unit, for the app (manifest "capabilities")

    def __init__(self, i2c, address, bus_name="A"):
        self.i2c = i2c
        self.address = address
        self.i2c_bus = bus_name
        # Stable pseudo-identity (third-party chips have no UID): type + bus + address.
        self.identity = "tp-%s-%s-%02x" % (self.TYPE, bus_name, address)
        self.firmware_version = None

    @classmethod
    def identify(cls, i2c, address):
        """True if the chip at `address` is this driver's chip (read-only probing)."""
        return False

    def setup(self):
        """Configure the chip for read(). Return True on success."""
        return True

    def read(self):
        """Generic read: {capability: value} or None on an I2C error."""
        return {}

    # ── power-loss / identity-loss recovery ──
    # A chip forgets its configuration when its bus rail drops and comes back with
    # power-on defaults. A failed transfer marks it lost; verify() (at most every
    # VERIFY_S, one short read) catches outages that no transfer saw (bench 16 Nov:
    # VEML7700 came back shut down and read 0 lx forever). The next access re-runs setup().
    _lost = False
    _in_setup = False
    _next_verify = 0
    VERIFY_S = 1.0
    recoveries = 0

    def verify(self):
        """True: configuration still there. False: lost (re-run setup). None: nothing to lose."""
        return None

    def _recover(self):
        self._in_setup = True
        try:
            ok = self.setup()
        except Exception:
            ok = False
        finally:
            self._in_setup = False
        self._lost = not ok
        if ok:
            self.recoveries += 1
        return ok

    def _check(self):
        """Call at the top of every public read. Re-runs setup() after a power loss."""
        now = time.monotonic()
        if not self._lost and now >= self._next_verify:
            self._next_verify = now + self.VERIFY_S
            if self.verify() is False:
                self._lost = True
        if self._lost:
            return self._recover()
        return True

    def _xfer(self, out, inbuf=None):
        """out only: write. out + inbuf: write-then-read. out=None + inbuf: plain read."""
        if self._lost and not self._in_setup and not self._recover():
            return False
        _service()
        bus = self.i2c
        locked = False
        try:
            while not bus.try_lock():
                pass
            locked = True
            if inbuf is None:
                bus.writeto(self.address, out)
            elif out is None:
                bus.readfrom_into(self.address, inbuf)
            else:
                bus.writeto_then_readfrom(self.address, out, inbuf)
            return True
        except (OSError, ValueError, RuntimeError):
            # OSError: I2C hiccup / chip gone. ValueError / RuntimeError: the bus object was
            # deinit'ed under the driver. Either way: None to the product, chip marked lost.
            self._lost = True
            return False
        finally:
            if locked:
                try:
                    bus.unlock()
                except Exception:
                    pass

    def _read_reg(self, reg, n):
        buf = bytearray(n)
        return buf if self._xfer(bytes([reg]), buf) else None

    def _write_reg(self, reg, data):
        return self._xfer(bytes([reg]) + bytes(data))

    def __repr__(self):
        return "<%s %s 0x%02X bus %s %s>" % (type(self).__name__, self.VENDOR,
                                              self.address, self.i2c_bus, self.CONFIDENCE)


def _probe_reg(i2c, address, reg, n):
    """Read n bytes of register `reg` from a not-yet-identified chip. None on error."""
    buf = bytearray(n)
    locked = False
    try:
        while not i2c.try_lock():
            pass
        locked = True
        i2c.writeto_then_readfrom(address, bytes([reg]), buf)
        return buf
    except (OSError, ValueError, RuntimeError):
        return None
    finally:
        if locked:
            try:
                i2c.unlock()
            except Exception:
                pass


def _probe_status(i2c, address):
    """Plain 1-byte read (no register pointer) from a not-yet-identified chip, e.g. the
    SSD1306 status byte. None on error."""
    buf = bytearray(1)
    locked = False
    try:
        while not i2c.try_lock():
            pass
        locked = True
        i2c.readfrom_into(address, buf)
        return buf[0]
    except (OSError, ValueError, RuntimeError):
        return None
    finally:
        if locked:
            try:
                i2c.unlock()
            except Exception:
                pass


# ── Unknown device ────────────────────────────────────────────────────────────



class UnknownDevice:
    """Something answers at a fixed address but no driver claims it. Tolerated: it
    answered at boot, so the Conductor never gives its address to a module (DEV-119); the app shows it, and `candidates` lists the registered
    types the user could assign (boards without an ID, like the Soldered PIR)."""
    TYPE = "unknown"
    CAPABILITIES = {}
    PULLUP_OHMS = None      # we do not know - the budget check says so

    def __init__(self, address, bus_name):
        self.address, self.i2c_bus = address, bus_name
        self.hint = ""                      # set by attach() from noknok_tp_info.HINTS
        self.candidates = [t for t, addrs, _ in REGISTRY if address in addrs]
        self.identity = "tp-unknown-%s-%02x" % (bus_name, address)

    def read(self):
        return None

    def __repr__(self):
        return "<UnknownDevice 0x%02X bus %s %s %s>" % (self.address, self.i2c_bus,
                                                        self.hint, self.candidates)


# ── Attach ────────────────────────────────────────────────────────────────────

def _noknok_addresses(c, bus_name):
    """Addresses the Conductor owns on `bus_name`: live modules + driverless ones."""
    own = set()
    for m in c._registry.values():
        if m is not None and getattr(m, "i2c_bus", "A") == bus_name and hasattr(m, "address"):
            own.add(m.address)
    for info in (getattr(c, "_unknown", None) or {}).values():
        if info.get("i2c_bus", "A") == bus_name:
            own.add(info.get("address"))
    return own


def identify(i2c, address, bus_name="A", log=print):
    """Driver object (set up) or UnknownDevice for the chip at `address`. Imports only
    the drivers registered for this address."""
    for t, addrs, auto in REGISTRY:
        if not auto or address not in addrs:
            continue
        try:
            drv = load(t)
            if drv.identify(i2c, address):
                m = drv(i2c, address, bus_name)
                if m.setup():
                    return m
        except Exception as e:          # a driver bug must never stop the scan
            log("[3p] %s at 0x%02X: %r" % (t, address, e))
    return UnknownDevice(address, bus_name)


def _bus(c, bus_name):
    for name, bus in c._buses():
        if name == bus_name:
            return bus
    return None


def _add(c, m):
    c.thirdparty.append(m)
    getattr(c, m.TYPE).append(m)


def assign(c, type_name, bus_name, address, log=print):
    """Attach a board the brain cannot identify by itself (the app / user names it).
    Returns the driver object, or None if nothing answers there."""
    bus = _bus(c, bus_name)
    if bus is None or address not in c._scan(bus):
        return None
    drv = load(type_name)
    m = drv(bus, address, bus_name)
    if not m.setup():
        return None
    _apply_options(m, saved().get(_key(bus_name, address)))
    c.unknown_devices = [u for u in c.unknown_devices
                         if not (u.i2c_bus == bus_name and u.address == address)]
    for o in [o for o in c.thirdparty if o.i2c_bus == bus_name and o.address == address]:
        c.thirdparty.remove(o)
        getattr(c, o.TYPE).remove(o)
    _add(c, m)
    log("[3p] 0x%02X (bus %s): %s (%s, named by user)" % (address, bus_name, m.NAME, m.VENDOR))
    c.thirdparty_rows = _call("info", "describe", c)
    return m


def _apply_options(m, opts):
    """Saved per-board options. Today only the OLED height (the chip cannot report it)."""
    if not isinstance(opts, dict):          # nothing saved, or a broken entry: defaults
        return
    h = opts.get("height")
    if m.TYPE == "ssd1306" and h in (32, 64) and h != m.HEIGHT:
        m.set_size(h)


def attach(c, log=print, assign_map=None):
    """Identify fixed-address boards on every bus the Conductor has up. Call after
    c.enumerate() / c.enumerate_all(). Safe to call again (rebuilds the lists).
    Boards must be present at boot (plug them in with the brain off); hot-plug is not supported.
    assign_map: {(bus, address): type} for boards without an ID. Default: what the user saved
    through the app (Store key "thirdparty", see remember()).
    Also sets c.thirdparty_rows: compact rows for the app's status reply (cached here so a
    status poll never imports a helper).
    Adds c.thirdparty, c.unknown_devices and c.<type> lists (maybe empty)."""
    c.thirdparty, c.unknown_devices = [], []
    for t in TYPES:
        setattr(c, t, [])
    sv = saved()
    if assign_map is None:
        assign_map = _saved_namings(sv, log)
    for bus_name, bus in c._buses():
        own = _noknok_addresses(c, bus_name)
        for a in c._scan(bus):
            if a in own or 0x50 <= a <= 0x57 and _is_store(c, a):
                continue
            named = assign_map.get((bus_name, a))
            if named:
                try:
                    m = load(named)(bus, a, bus_name)
                    if m.setup():
                        _apply_options(m, sv.get(_key(bus_name, a)))
                        _add(c, m)
                        log("[3p] 0x%02X (bus %s): %s (named)" % (a, bus_name, m.NAME))
                        continue
                except Exception as e:      # renamed / removed driver, typo saved by the app
                    log("[3p] 0x%02X (bus %s): saved naming %r skipped: %r" % (a, bus_name, named, e))
            m = identify(bus, a, bus_name, log)
            if isinstance(m, UnknownDevice):
                c.unknown_devices.append(m)
                log("[3p] 0x%02X (bus %s): unknown board%s" % (
                    a, bus_name, (", could be: " + ", ".join(m.candidates)) if m.candidates else ""))
            else:
                _apply_options(m, sv.get(_key(bus_name, a)))
                _add(c, m)
                log("[3p] 0x%02X (bus %s): %s (%s, %s)" % (a, bus_name, m.NAME, m.VENDOR, m.CONFIDENCE))
    used = {m.TYPE for m in c.thirdparty}
    for t in TYPES:                       # drop drivers that matched nothing
        if t not in used:
            _unload(t)
    c.thirdparty_rows = []
    if c.unknown_devices or c.thirdparty:
        info = _helper("info")
        for u in c.unknown_devices:
            u.hint = info.HINTS.get(u.address, "")
        for w in info.pullup_warnings(c):
            log("[3p] " + w)
        c.thirdparty_rows = info.describe(c)
        _unload("info")
    return c.thirdparty


def _saved_namings(sv, log):
    """{(bus, address): type} from the Store entries that name a board. A broken entry
    (key that does not parse, type no driver has) is logged and skipped, never raised:
    it would crash the product at every boot until the Store is wiped (DEV-248 review)."""
    out = {}
    for k, e in sv.items():
        try:
            t = e.get("type")
            if not t:
                continue
            if t not in TYPES:
                raise ValueError("no driver for this type")
            b, a = k.split("-")
            out[(b, int(a, 16))] = t
        except Exception as ex:
            log("[3p] saved entry %r skipped: %r" % (k, ex))
    return out


def _is_store(c, address):
    """The brain's optional FRAM Store lives at 0x50 on bus A - not a third-party board."""
    try:
        from noknok import store
        return store().backend == "fram" and address == 0x50
    except Exception:
        return False


# ── Helpers in their own files (loaded on demand, unloaded after the call) ────

def _helper(name):
    return __import__("noknok_tp_" + name)


def _call(name, fn, *args):
    try:
        return getattr(_helper(name), fn)(*args)
    finally:
        _unload(name)


def pullup_budget(c):
    """{bus: {"total_ohms", "boards": [(name, ohms, removable)], "unknown": n}}."""
    return _call("info", "pullup_budget", c)


def pullup_warnings(c):
    """Human-readable pull-up warnings for the app / log. Empty list = fine."""
    return _call("info", "pullup_warnings", c)


def describe(c):
    """Rows for the app's status reply (same keys as noknok modules + extras)."""
    return _call("info", "describe", c)

