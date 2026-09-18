# noknok.py  v1.8
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
# v1.7 (Sue): DEV-18 — no filesystem writes while a product runs. Bench-proven
#             15 Sep 2026: on RP2 flash ANY FAT write can destroy the whole
#             directory on a power cut (3 pulls, 3 losses, one total). So:
#             - the FS is read-only to the program by default (boot.py);
#               setup/OTA-time writes go through writable() + write_atomic()
#               into /data, whose directory block is not the one naming
#               code.py / noknok.py / lib;
#             - everything that changes at runtime lives in the Store —
#               I2C FRAM at 0x50 when present (power-safe, DEV-40), else
#               microcontroller.nvm (self-healing): module state, roles,
#               settings (DEV-34), event history, a credentials copy;
#             - enumeration gives a known module its previous address, so
#               the state stops changing from boot to boot.
#             Pins come from settings.toml (NOKNOK_I2C_SDA/SCL/FREQ).
# v1.8 (Sue): DEV-34 — reachable by the app while the product runs. Every
#             driver read/write and Conductor.sleep() call _service(), the
#             hook code.py points at noknok_rpc.service (non-blocking,
#             throttled; ~1 ms idle, bench-soaked 17 Sep 2026). conductor()
#             exposes the product's Conductor to the RPC handlers; the
#             factory-reset wipe is a module function shared with the
#             `factory_reset` op. No API removed; makers add zero lines.
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
import os

__version__ = "1.8"

try:
    import storage            # CircuitPython only; absent on a host Python
except ImportError:
    storage = None


# ── App channel servicing hook (DEV-34) ───────────────────────────────────────
# After setup the maker's product loop is the only Python running, so the app
# can only be answered when the product gives us a moment. Every module driver
# calls _service() at the top of its read/write — before it locks the bus — and
# Conductor.sleep() calls it while sleeping. code.py installs the real pump
# (noknok_rpc.service: non-blocking, throttled to 50 ms, ~1 ms when idle) with
# set_service_hook(); without a hook this is a no-op, so bench scripts and
# offline products pay nothing. Makers add zero lines.

_service_hook = None

def set_service_hook(fn):
    """Install (or clear with None) the function the drivers call to service
    the app channel between module transactions."""
    global _service_hook
    _service_hook = fn

def _service():
    if _service_hook is not None:
        try:
            _service_hook()
        except Exception:
            pass                    # the product loop must never die for the radio
    if _settings_by_key:            # deliver app-side changes, flush after idle
        _settings_tick()

_last_conductor = None

def conductor():
    """The most recently created Conductor, or None. Lets the RPC handlers in
    code.py reach the product's own Conductor (created inside product.py)
    without creating a second one on the same pins."""
    return _last_conductor


# ── settings.toml — the one maker-facing config file ──────────────────────────
# CircuitPython reads /settings.toml natively; os.getenv("KEY") returns the
# value (str, or int for a bare number) or None when the key is absent. Every
# key has a default here that equals the noknok ecosystem standard, so a brain
# without the file behaves exactly like a factory unit. Makers wiring their own
# Pico change the file, never this library. Keys read here:
#   NOKNOK_I2C_SDA  = "GP8"      NOKNOK_I2C_SCL = "GP9"     NOKNOK_I2C_FREQ = 100000
#   NOKNOK_USB_DP   = "GP16"     NOKNOK_USB_DM  = "GP17"    (read by noknok_usb.py)
#   NOKNOK_USB_DRIVE = 0         (read by boot.py, see set_usb_drive() below)

SETTINGS_FILE = "/settings.toml"

def env(key, default=None):
    """settings.toml value for `key`, or `default` when absent / unreadable."""
    try:
        v = os.getenv(key)
    except Exception:
        v = None
    return default if v is None else v

def env_pin(key, default):
    """A board pin named in settings.toml (e.g. "GP8"), or `default`. An unknown
    pin name is reported once and the default used, so a typo never kills the
    bus at boot."""
    name = env(key)
    if not name:
        return default
    try:
        return getattr(board, str(name).strip())
    except AttributeError:
        print(f"[settings] {key}={name!r} is not a pin on this board — using default")
        return default


# ── Filesystem policy (DEV-18) ────────────────────────────────────────────────
# The CIRCUITPY drive is a FAT filesystem on raw flash with no journal. A power
# cut in the middle of ANY write can corrupt it — and unplugging is how a noknok
# product is switched off. Software cannot make a FAT write survive a power
# cut; it can only make sure no write is in flight when the plug is pulled and
# that the rare writes cannot destroy what is already there. Hence:
#
#   1. The filesystem is READ-ONLY to the program by default. boot.py hides the
#      CIRCUITPY drive from PCs (NOKNOK_USB_DRIVE = 0) and remounts read-only
#      (with the drive hidden CircuitPython would otherwise leave it writable).
#      A stray open(..., "w") anywhere raises OSError instead of silently
#      writing flash — the OS enforces the policy, not code discipline.
#   2. writable() opens a short window: remount rw, write, os.sync(), remount
#      ro. Windows exist for provisioning, OTA and factory reset — moments the
#      app tells the customer to keep the plug in — and NEVER while product.py
#      runs. A power cut inside a window CAN still destroy the filesystem
#      (bench-proven: the flash driver rewrites whole 4 KB blocks in place);
#      /data + DEV-38 (one-file recovery) are the mitigations, not a cure.
#   3. write_atomic() writes <path>.tmp first and renames it over the target,
#      so a power cut mid-write leaves the OLD file's bytes intact. Orphan .tmp
#      files are cleaned by clean_tmp() on the next boot.
#   4. Runtime data never touches the FAT: see Store below.
#
# Maker/bench mode (NOKNOK_USB_DRIVE = 1): the drive is visible and a PC may
# write it. CircuitPython then refuses a runtime remount, so writable() is a
# no-op and program writes raise OSError — provisioning fails loudly rather
# than the brain and the PC both writing one FAT volume. Bench file transfers
# go over the REPL (pico.py put), which is unaffected by any of this.

_write_depth = 0          # nesting counter: one remount per outermost window
_we_remounted = False     # True only if THIS window flipped the FS to rw

def _fs_readonly():
    """Current state of '/', or None if unknown (host Python)."""
    try:
        return storage.getmount("/").readonly
    except Exception:
        return None

class writable:
    """`with writable():` — the filesystem is writable inside the block.
    Nests safely; the outermost exit syncs and returns to read-only — but only
    if it was this window that made the FS writable. A filesystem that was
    already writable (an old rw-remounting boot.py, a maker's own boot.py) is
    left exactly as found."""

    def __enter__(self):
        global _write_depth, _we_remounted
        if _write_depth == 0 and storage is not None:
            _we_remounted = False
            if _fs_readonly():
                try:
                    storage.remount("/", readonly=False)
                    _we_remounted = True
                except (RuntimeError, OSError):
                    pass   # refused (drive in use by a PC) — the write will raise
        _write_depth += 1
        return self

    def __exit__(self, *exc):
        global _write_depth, _we_remounted
        _write_depth -= 1
        if _write_depth == 0 and storage is not None:
            try:
                os.sync()
            except Exception:
                pass
            if _we_remounted:
                try:
                    storage.remount("/", readonly=True)
                except (RuntimeError, OSError):
                    pass
                _we_remounted = False
        return False           # never swallow the caller's exception

def ensure_dir(path):
    """Create the parent directory of `path` if missing (inside a window)."""
    parent = path.rsplit("/", 1)[0] if "/" in path.strip("/") else ""
    if not parent:
        return
    try:
        os.stat(parent)
    except OSError:
        with writable():
            os.mkdir(parent)

def write_atomic(path, data):
    """Write `data` (str or bytes) to `path`: bytes go to <path>.tmp, are
    synced, then renamed over the target. Protects the FILE's bytes (the old
    content is never truncated in place) — it does NOT protect the directory
    or the FAT: on RP2 flash any directory/FAT change rewrites a whole 4 KB
    block in place, and a power cut during that can take every entry in the
    block (bench-proven 15 Sep 2026, DEV-18). Hence the rule: call this only
    at setup / OTA time, never while a product runs; runtime data goes to
    Store (FRAM / nvm). Raises OSError when the filesystem is not writable."""
    tmp = path + ".tmp"
    binary = isinstance(data, (bytes, bytearray, memoryview))
    ensure_dir(path)
    with writable():
        with open(tmp, "wb" if binary else "w") as f:
            f.write(data)
            f.flush()
        try:
            os.sync()
        except Exception:
            pass
        os.rename(tmp, path)   # replaces an existing target (MicroPython semantics)

def write_json_atomic(path, obj):
    """json.dumps(obj) → write_atomic(). Returns True on success, False when the
    filesystem is read-only; never raises."""
    try:
        write_atomic(path, json.dumps(obj))
        return True
    except OSError:
        return False

def read_json(path, default=None):
    """json.load(path), or `default` when the file is absent or malformed."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default

def append_line(path, line):
    """Append one line inside a write window. Appends cannot be made atomic on
    FAT; keep them rare (audit events) and never call this from a product loop.
    Best-effort: returns False instead of raising."""
    try:
        ensure_dir(path)
        with writable():
            with open(path, "a") as f:
                f.write(line + "\n")
        return True
    except OSError:
        return False

def remove(*paths):
    """Delete files inside one write window. Missing files are ignored."""
    removed = 0
    try:
        with writable():
            for p in paths:
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    return removed

def clean_tmp(*roots):
    """Delete leftover *.tmp files from a write interrupted by a power cut.
    Called once at boot by code.py. Opens a window only if there is something
    to delete. Returns the number of files removed."""
    orphans = []
    for root in roots or ("/", DATA_DIR):
        try:
            orphans += [root.rstrip("/") + "/" + n for n in os.listdir(root) if n.endswith(".tmp")]
        except OSError:
            pass
    if not orphans:
        return 0
    n = remove(*orphans)
    if n:
        print(f"[fs] removed {n} orphan .tmp file(s) left by an interrupted write")
    return n

# Field-written files live in their own directory. Their directory entries then
# sit in /data's own cluster, not in the root-directory block that names
# code.py / noknok.py / lib — so a power cut during a setup-time write can at
# worst lose /data/* (recoverable: re-provision, or the Store copies), never
# the brain itself. The FAT block is still shared; that residual risk is why
# such writes happen only at setup / OTA, and why DEV-38 exists.
DATA_DIR = "/data"


# ── Store: runtime data that must NEVER touch the FAT (DEV-18) ────────────────
# Everything that changes while a product runs — module state, settings (DEV-34),
# the event history, plus recovery copies of the WiFi credentials and roles —
# lives here, as one small JSON record with a CRC:
#
#   FRAM backend (I2C FM24CL64B at 0x50 on the PicoHub, DEV-40): two 4 KB slots,
#     written alternately with a rising sequence number; a power cut mid-write
#     leaves one slot with a bad CRC and the other intact. FRAM writes are
#     byte-atomic and wear-free, so this is genuinely power-safe.
#   nvm backend (fallback, any Pico): the record at microcontroller.nvm[64:]
#     (nvm[0] and nvm[1:5] belong to code.py). nvm is one 4 KB flash sector
#     rewritten in place, so a cut mid-write loses the whole record — which is
#     ACCEPTABLE only because every key here is self-healing: state is
#     re-enumerated, settings fall back to defaults, the history starts empty,
#     and credentials/roles have their primaries on the filesystem.
#
# API: st = store(); st.get(key, default); st.set(key, value); st.append(key,
# item, max_items); st.delete(key); st.wipe(). Each set() writes immediately.

STORE_MAGIC      = b"NKS1"
STORE_NVM_OFFSET = 64
FRAM_ADDR        = 0x50
FRAM_SLOT_BYTES  = 4096        # 2 slots = one FM24CL64B (8 KB); bigger chips use the same 2 slots

_crc_table = None
def _crc32(data):
    """zlib-compatible CRC-32 (table-driven; ~1 KB of RAM on first use)."""
    global _crc_table
    if _crc_table is None:
        t = []
        for i in range(256):
            c = i
            for _ in range(8):
                c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
            t.append(c)
        _crc_table = t
    crc = 0xFFFFFFFF
    for b in data:
        crc = _crc_table[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF

class Store:
    def __init__(self):
        self._data, self._seq = None, 0
        self._backend = None        # "fram" | "nvm"
        self._bus = None            # a Conductor's busio.I2C when one exists
        self._fram_slot = 0         # slot the current record lives in

    # ── bus handling ─────────────────────────────────────────────────────────
    def attach(self, i2c):
        """Share a Conductor's bus (None to detach)."""
        self._bus = i2c

    def _with_bus(self, fn):
        """Run fn(i2c) on the attached bus, or on a temporary one that is
        deinit'ed afterwards so a later Conductor can claim the pins."""
        if self._bus is not None:
            try:
                return fn(self._bus)
            except (ValueError, RuntimeError):
                self._bus = None    # bus was deinit'ed under us — use a temporary one
        bus = busio.I2C(env_pin("NOKNOK_I2C_SCL", board.GP9), env_pin("NOKNOK_I2C_SDA", board.GP8),
                        frequency=int(env("NOKNOK_I2C_FREQ", 100_000)))
        try:
            return fn(bus)
        finally:
            bus.deinit()

    # ── FRAM primitives (FM24CL64B / MB85RC: 2-byte address, then data) ──────
    @staticmethod
    def _fram_write(i2c, addr, data):
        while not i2c.try_lock():
            pass
        try:
            for off in range(0, len(data), 30):
                a = addr + off
                i2c.writeto(FRAM_ADDR, bytes([a >> 8, a & 0xFF]) + bytes(data[off:off + 30]))
        finally:
            i2c.unlock()

    @staticmethod
    def _fram_read(i2c, addr, n):
        out = bytearray(n)
        while not i2c.try_lock():
            pass
        try:
            for off in range(0, n, 32):
                a = addr + off
                chunk = memoryview(out)[off:min(off + 32, n)]
                i2c.writeto_then_readfrom(FRAM_ADDR, bytes([a >> 8, a & 0xFF]), chunk)
        finally:
            i2c.unlock()
        return out

    @staticmethod
    def _fram_present(i2c):
        while not i2c.try_lock():
            pass
        try:
            return FRAM_ADDR in i2c.scan()
        finally:
            i2c.unlock()

    # ── record encoding ──────────────────────────────────────────────────────
    @staticmethod
    def _encode(seq, data):
        body = json.dumps(data).encode()
        hdr = STORE_MAGIC + seq.to_bytes(4, "little") + len(body).to_bytes(4, "little")
        return hdr + _crc32(body).to_bytes(4, "little") + body

    @staticmethod
    def _decode(buf):
        """(seq, dict) or None for anything that is not an intact record."""
        try:
            if bytes(buf[:4]) != STORE_MAGIC:
                return None
            seq = int.from_bytes(bytes(buf[4:8]), "little")
            n   = int.from_bytes(bytes(buf[8:12]), "little")
            crc = int.from_bytes(bytes(buf[12:16]), "little")
            body = bytes(buf[16:16 + n])
            if len(body) != n or _crc32(body) != crc:
                return None
            data = json.loads(body)
            return (seq, data) if isinstance(data, dict) else None
        except Exception:
            return None

    # ── load / flush ─────────────────────────────────────────────────────────
    def _load(self):
        if self._data is not None:
            return
        self._data, self._seq = {}, 0
        # FRAM first (unless disabled in settings.toml: NOKNOK_FRAM = 0)
        if str(env("NOKNOK_FRAM", "1")) != "0":
            try:
                def probe(i2c):
                    if not self._fram_present(i2c):
                        return None
                    best = None
                    for slot in (0, 1):
                        hdr = self._fram_read(i2c, slot * FRAM_SLOT_BYTES, 16)
                        n = int.from_bytes(bytes(hdr[8:12]), "little")
                        if bytes(hdr[:4]) == STORE_MAGIC and 0 <= n <= FRAM_SLOT_BYTES - 16:
                            rec = self._decode(hdr + self._fram_read(i2c, slot * FRAM_SLOT_BYTES + 16, n))
                            if rec and (best is None or rec[0] > best[0]):
                                best = (rec[0], rec[1], slot)
                    return best or (0, {}, 1)      # empty FRAM: first write goes to slot 0
                found = self._with_bus(probe)
                if found is not None:
                    self._backend = "fram"
                    self._seq, self._data, self._fram_slot = found
                    return
            except Exception as e:
                print("[store] FRAM probe failed (%r) — using nvm" % (e,))
        self._backend = "nvm"
        try:
            import microcontroller
            rec = self._decode(microcontroller.nvm[STORE_NVM_OFFSET:])
            if rec:
                self._seq, self._data = rec
        except Exception:
            pass

    def _flush(self):
        self._seq += 1
        blob = self._encode(self._seq, self._data)
        if self._backend == "fram":
            slot = 1 - self._fram_slot
            if len(blob) > FRAM_SLOT_BYTES:
                raise ValueError("store record too large for FRAM slot")
            def w(i2c):
                self._fram_write(i2c, slot * FRAM_SLOT_BYTES, blob)
                back = self._fram_read(i2c, slot * FRAM_SLOT_BYTES, len(blob))
                if bytes(back) != blob:
                    raise OSError("FRAM verify failed")
            self._with_bus(w)
            self._fram_slot = slot
        else:
            import microcontroller
            cap = len(microcontroller.nvm) - STORE_NVM_OFFSET
            if len(blob) > cap:
                raise ValueError("store record too large for nvm")
            microcontroller.nvm[STORE_NVM_OFFSET:STORE_NVM_OFFSET + len(blob)] = blob

    def _fit(self):
        """Trim the event ring until the record fits the backend."""
        cap = FRAM_SLOT_BYTES if self._backend == "fram" else 4096 - STORE_NVM_OFFSET
        while len(self._encode(self._seq, self._data)) > cap:
            ev = self._data.get("events")
            if ev:
                del ev[0]
            else:
                raise ValueError("store record too large")

    # ── public API ───────────────────────────────────────────────────────────
    @property
    def backend(self):
        self._load()
        return self._backend

    def get(self, key, default=None):
        self._load()
        return self._data.get(key, default)

    def set(self, key, value):
        """Write immediately. Returns True on success; never raises."""
        self._load()
        if self._data.get(key) == value and key in self._data:
            return True
        self._data[key] = value
        try:
            self._fit()
            self._flush()
            return True
        except Exception as e:
            print("[store] write failed: %r" % (e,))
            return False

    def append(self, key, item, max_items=40):
        self._load()
        lst = list(self._data.get(key) or [])
        lst.append(item)
        return self.set(key, lst[-max_items:])

    def delete(self, key):
        self._load()
        if key in self._data:
            del self._data[key]
            try:
                self._flush(); return True
            except Exception as e:
                print("[store] write failed: %r" % (e,)); return False
        return True

    def wipe(self):
        """Factory reset: empty record."""
        self._load()
        self._data = {}
        try:
            self._flush(); return True
        except Exception:
            return False

_store = None
def store():
    """The brain's one Store instance (lazy)."""
    global _store
    if _store is None:
        _store = Store()
    return _store


# ── Settings — the values the app and the product both change (DEV-34) ───────
# Principle (Device Protocol v1 §3): the manifest carries the schema, the
# device carries the values; a knob turn and the app's settings page change
# the same thing, so the device is the source of truth and the app a view of
# it. Values live in the Store (key "settings"), never in a file (DEV-18), and
# the Store is DESIGNED FOR nvm: one 4 KB flash sector, ~80 ms blocking per
# write, finite endurance. So: write only what changed, and only after
# IDLE_S without a change — a knob generates dozens of changes a second and a
# product must never write from its hot loop. The flush and app-side change
# callbacks both run from the servicing hook, i.e. at the top of a module
# read/write or inside c.sleep() — never inside a module transaction.
#
#   s = c.settings                      # a Settings, created by the Conductor
#   s.defaults({"brightness": 180, "color": "#FFAF5F", "on": True})
#   s.get("brightness")                 # current value
#   s.set("brightness", 200)            # device-side change (persisted later)
#   s.on_change(lambda changed: apply(changed))   # app-side changes arrive here
#
# Records: {"product": "<tag>", "values": {...}, "defaults": {...}, "seq": n}.
# A record tagged for another product is ignored, so a product switch starts
# from defaults while a reinstall of the same product keeps its values.

SETTINGS_KEY        = "settings"
DEVICE_SETTINGS_KEY = "device_settings"
SETTINGS_IDLE_S     = 5.0      # flush this long after the last change ...
SETTINGS_MAX_WAIT_S = 60.0     # ... or at the latest this long after the first (knob held forever)
SETTINGS_MIN_GAP_S  = 30.0     # never two nvm flushes closer than this (flash wear: ~100k erases)
SETTINGS_RETRY_S    = 60.0     # after a failed write, wait this long before trying again
SETTINGS_MAX_KEYS   = 32
SETTINGS_MAX_KEY    = 32       # characters
SETTINGS_MAX_STR    = 256      # characters per string value
SETTINGS_MAX_BYTES  = 1024     # JSON size of all values of one scope

def product_tag():
    """What the current product is called in the Store: the manifest id the app
    sent at provisioning (code.py 0.17+), else the script's file name."""
    creds = read_json(DATA_DIR + "/wifi.json") or read_json("/wifi.json") \
        or store().get("wifi") or {}
    pid = creds.get("product_id")
    if pid:
        return str(pid)[:64]
    url = creds.get("script_url") or ""
    return url.rsplit("/", 1)[-1][:64] if url else "unknown"


def _check_value(key, value, default=None):
    """Why a (key, value) is not acceptable, or None if it is. Settings are
    scalars only (every config_schema type is one); a value's type must match
    the declared default's, so a phone that sends "abc" for a slider cannot
    crash the product three times and park it (bool is checked apart from
    numbers — Python's bool is an int)."""
    if not isinstance(key, str) or not key or len(key) > SETTINGS_MAX_KEY:
        return "bad key"
    if isinstance(value, bool):
        kind = "bool"
    elif isinstance(value, (int, float)):
        kind = "number"
    elif isinstance(value, str):
        if len(value) > SETTINGS_MAX_STR:
            return "string too long"
        kind = "string"
    elif value is None:
        kind = "null"
    else:
        return "must be a boolean, number or string"
    if default is not None:
        want = ("bool" if isinstance(default, bool) else
                "number" if isinstance(default, (int, float)) else
                "string" if isinstance(default, str) else None)
        if want is not None and kind != want:
            return "expected %s" % want
        # A default that looks like a colour or a time fixes the format too.
        if kind == "string":
            if _looks_like_color(default) and not _looks_like_color(value):
                return "expected #RRGGBB"
            if _looks_like_time(default) and not _looks_like_time(value):
                return "expected HH:MM"
    elif kind == "null":
        return "null not allowed"
    return None

def _looks_like_color(s):
    if not isinstance(s, str) or len(s) != 7 or s[0] != "#":
        return False
    try:
        int(s[1:], 16)
        return True
    except ValueError:
        return False

def _looks_like_time(s):
    if not isinstance(s, str) or len(s) != 5 or s[2] != ":":
        return False
    try:
        return 0 <= int(s[0:2]) <= 23 and 0 <= int(s[3:5]) <= 59
    except ValueError:
        return False


class Settings:
    def __init__(self, tag=None, key=SETTINGS_KEY):
        self._key   = key
        self._tag   = tag
        self._vals  = {}
        self._defs  = {}
        self._seq   = 0
        self._dirty = False
        self._first_change = 0.0    # when the current dirty period began
        self._last_change  = 0.0
        self._last_flush   = -1e9
        self._error = None          # last write failure, shown in snapshot()
        self._cbs   = []
        self._pending = {}          # app-side changes not yet delivered to on_change
        rec = store().get(key)
        if isinstance(rec, dict) and (tag is None or rec.get("product") == tag):
            self._vals = dict(rec.get("values") or {})
            self._defs = dict(rec.get("defaults") or {})
            self._seq  = int(rec.get("seq") or 0)
        elif isinstance(rec, dict):
            print("[settings] stored values belong to %r, not %r - starting from defaults"
                  % (rec.get("product"), tag))

    # ── product API ──────────────────────────────────────────────────────────
    def defaults(self, defs):
        """Declare defaults; fills in any key that has no value yet. Values of
        the wrong type (a stale record, an old app) are replaced by the default."""
        touched = False
        for k, v in defs.items():
            if _check_value(k, v) is not None:
                print("[settings] default %r rejected: %s" % (k, _check_value(k, v)))
                continue
            if self._defs.get(k) != v or k not in self._defs:
                self._defs[k] = v
                touched = True
            if k not in self._vals or _check_value(k, self._vals[k], v) is not None:
                self._vals[k] = v
                touched = True
        if touched:
            self._mark()
        return self

    def get(self, key, default=None):
        return self._vals.get(key, self._defs.get(key, default))

    def set(self, key, value):
        """Device-side change (a knob, the product). Persisted later — see
        _tick(). Returns True if the value changed, False if equal or rejected."""
        why = _check_value(key, value, self._defs.get(key))
        if why is not None:
            print("[settings] set %r rejected: %s" % (key, why))
            return False
        if key in self._vals and self._vals[key] == value:
            return False
        if key not in self._vals and len(self._vals) >= SETTINGS_MAX_KEYS:
            print("[settings] set %r rejected: too many keys" % (key,))
            return False
        self._vals[key] = value
        self._mark()
        return True

    def update(self, values):
        return any([self.set(k, v) for k, v in values.items()])

    def all(self):
        return dict(self._vals)

    @property
    def seq(self):
        return self._seq

    def on_change(self, callback):
        """callback(changed: dict) for changes made by the app, delivered from
        the servicing hook (top of a module read / c.sleep), never mid-transaction."""
        self._cbs.append(callback)
        return callback

    def reset(self):
        """Back to defaults (app 'Reset to defaults'). Everything that actually
        changes is handed to on_change like an app-side change, so the product
        repaints."""
        old = self._vals
        self._vals = dict(self._defs)
        changed = {k: v for k, v in self._vals.items()
                   if k not in old or old[k] != v}
        if changed:
            self._pending.update(changed)
        self._mark()
        return dict(self._vals)

    def flush(self, force=False):
        """Write now if anything changed (force ignores the wear gap — used
        before a reboot). Returns True if the record is now on the Store."""
        if not self._dirty:
            return True
        now = time.monotonic()
        if not force and now - self._last_flush < SETTINGS_MIN_GAP_S:
            return False
        rec = {"product": self._tag, "values": self._vals,
               "defaults": self._defs, "seq": self._seq + 1}
        if len(json.dumps(self._vals)) > SETTINGS_MAX_BYTES:
            self._error = "values too large"
            self._last_flush = now                  # back off, don't spin
            return False
        ok = store().set(self._key, rec)
        self._last_flush = now
        if ok:
            self._seq += 1
            self._dirty = False
            self._error = None
        else:
            self._error = "store write failed"      # retried after SETTINGS_RETRY_S
            self._last_flush = now + SETTINGS_RETRY_S - SETTINGS_MIN_GAP_S
        return ok

    # ── app side (called by the settings.* ops) ──────────────────────────────
    def apply_remote(self, values):
        """Merge values sent by the app. Returns (changed, rejected) — rejected
        maps key -> reason, so the app can show why. Rejected values never
        reach the product."""
        changed, rejected = {}, {}
        for k, v in values.items():
            why = _check_value(k, v, self._defs.get(k))
            if why is None and k not in self._vals and len(self._vals) >= SETTINGS_MAX_KEYS:
                why = "too many keys"
            if why is not None:
                rejected[k] = why
                continue
            if k not in self._vals or self._vals[k] != v:
                self._vals[k] = v
                changed[k] = v
        if changed:
            self._mark()
            self._pending.update(changed)
        return changed, rejected

    def snapshot(self):
        out = {"product": self._tag, "values": dict(self._vals),
               "defaults": dict(self._defs), "seq": self._seq,
               "dirty": self._dirty}
        if self._error:
            out["error"] = self._error
        return out

    # ── internals ────────────────────────────────────────────────────────────
    def _mark(self):
        now = time.monotonic()
        if not self._dirty:
            self._first_change = now
        self._dirty = True
        self._last_change = now

    def _tick(self):
        """Called from the servicing hook: deliver app-side changes, then flush
        once the values have been quiet for SETTINGS_IDLE_S — or, if they never
        go quiet, SETTINGS_MAX_WAIT_S after the first change — but never two
        nvm writes closer than SETTINGS_MIN_GAP_S."""
        if self._pending:
            changed, self._pending = self._pending, {}
            for cb in self._cbs:
                try:
                    cb(changed)
                except Exception as e:
                    print("[settings] on_change callback failed: %r" % (e,))
        if self._dirty:
            now = time.monotonic()
            if (now - self._last_change >= SETTINGS_IDLE_S
                    or now - self._first_change >= SETTINGS_MAX_WAIT_S):
                self.flush()
_settings_by_key = {}

def settings(scope="product"):
    """The brain's Settings for a scope — one instance per scope, shared by
    every Conductor and the RPC handlers. "product" values are tagged with
    product_tag(); "device" values (timezone, radios, pairing — DEV-35/36)
    survive product switches."""
    key = SETTINGS_KEY if scope == "product" else DEVICE_SETTINGS_KEY
    s = _settings_by_key.get(key)
    if s is None:
        s = Settings(product_tag() if scope == "product" else None, key)
        _settings_by_key[key] = s
    return s

def install_defaults(tag, defaults):
    """Provisioning-time (setup, not the hot loop): write the product's
    config_defaults so device and app agree from first boot. Same product
    (same tag) keeps its values and only gains new keys; a different product
    starts from these defaults. Immediate Store write; returns True on success."""
    defaults = {k: v for k, v in dict(defaults or {}).items()
                if _check_value(k, v) is None}          # scalars only, sane sizes
    if len(defaults) > SETTINGS_MAX_KEYS:
        defaults = dict(list(defaults.items())[:SETTINGS_MAX_KEYS])
    rec = store().get(SETTINGS_KEY)
    if isinstance(rec, dict) and rec.get("product") == tag:
        values = dict(rec.get("values") or {})
        for k, v in defaults.items():
            values.setdefault(k, v)
        values = {k: v for k, v in values.items() if k in defaults} if defaults else values
        seq = int(rec.get("seq") or 0) + 1
    else:
        values, seq = dict(defaults), 1
    _settings_by_key.pop(SETTINGS_KEY, None)          # a live instance is now stale
    return store().set(SETTINGS_KEY, {"product": tag, "values": values,
                                      "defaults": defaults, "seq": seq})

def _settings_tick():
    for s in _settings_by_key.values():
        try:
            s._tick()
        except Exception:
            pass

def flush_settings():
    """Write every dirty scope now, ignoring the wear gap. Call before any
    reset / reload so the last few seconds of changes are not lost."""
    ok = True
    for s in _settings_by_key.values():
        try:
            ok = s.flush(force=True) and ok
        except Exception:
            ok = False
    return ok

def usb_drive_visible():
    """True when settings.toml asks boot.py to show the CIRCUITPY drive
    (NOKNOK_USB_DRIVE = 1). Absent key = visible (fail open, see boot.py)."""
    v = env("NOKNOK_USB_DRIVE")
    if v is None:
        return True
    try:
        return int(v) != 0
    except (TypeError, ValueError):
        return True

def set_usb_drive(visible):
    """Rewrite NOKNOK_USB_DRIVE in settings.toml. Takes effect on the next
    power cycle (boot.py reads it). From the REPL on a shipped brain:
        >>> import noknok; noknok.set_usb_drive(True)
    then unplug/replug — the drive appears and a PC can edit files. To hide it
    again, edit settings.toml on the PC (the program cannot write while the
    drive is visible) and power-cycle. Returns True when the file was written."""
    key, val = "NOKNOK_USB_DRIVE", "1" if visible else "0"
    try:
        with open(SETTINGS_FILE, "r") as f:
            lines = f.read().split("\n")
    except OSError:
        lines = []
    out, done = [], False
    for ln in lines:
        if ln.split("=")[0].strip() == key:
            out.append(f"{key} = {val}")
            done = True
        else:
            out.append(ln)
    if not done:
        if out and out[-1] != "":
            out.append("")
        out.append(f"{key} = {val}")
    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"
    try:
        write_atomic(SETTINGS_FILE, text)
        print(f"[settings] {key} = {val} — power-cycle the brain to apply")
        return True
    except OSError:
        print("[settings] cannot write settings.toml (drive visible to a PC?) "
              "— edit it on the PC instead")
        return False


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

    def __init__(self, sda=None, scl=None, frequency=None):
        # Pins: explicit argument > settings.toml > ecosystem standard (GP8/GP9,
        # 100 kHz). noknok hardware follows the standard; the file is for makers
        # wiring their own Pico.
        self._sda  = sda if sda is not None else env_pin("NOKNOK_I2C_SDA", board.GP8)
        self._scl  = scl if scl is not None else env_pin("NOKNOK_I2C_SCL", board.GP9)
        self._freq = int(frequency if frequency is not None
                         else env("NOKNOK_I2C_FREQ", 100_000))
        self.i2c = None
        self._init_i2c()   # tolerant: warns + leaves i2c=None if no pull-ups / no bus
        store().attach(self.i2c)   # the Store (FRAM at 0x50 / nvm) shares this bus
        global _last_conductor
        _last_conductor = self     # see conductor()
        self.settings = settings() # c.settings — product values (DEV-34), shared instance
        self.buzzer    = []    # NoknokBuzzer instances, indexed by discovery order
        self.knob      = []    # NoknokKnob instances
        self.ledbutton = []    # NoknokLedButton instances
        self.display   = []    # NoknokDisplay instances
        self.leds      = []    # NoknokLEDs (USB) instances, populated by enumerate_usb()
        self.role      = {}    # role_name → module object, populated by load_roles()
        self._registry = {}    # identity (I2C uid_hex / USB serial) → module object

    # ── Cooperative sleep (DEV-34) ────────────────────────────────────────────

    def sleep(self, seconds):
        """Sleep like time.sleep(), but keep answering the app meanwhile.
        A product that polls modules is serviced implicitly (every read/write
        pumps the channel); one that idles in a long time.sleep() polls nothing,
        so use c.sleep(s) there instead. Optional — forgetting it only delays
        replies until the next module read."""
        end = time.monotonic() + seconds
        while True:
            _service()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.02, left))

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
        _service()
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
        _service()
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

    # ── Bootloader self-update (DEV-31) ────────────────────────────────────────
    # A second, separate update type: same transport, different payload, and it
    # always destroys the application (the staging area IS the app region), so the
    # app is re-pushed straight afterwards. I2C only until the CH32V203 port lands.

    def bootloader_version(self, entry):
        """
        Read a module's bootloader version — the fleet discriminator.

        Drops the running app into the bootloader (0xB0), asks 0xB1, then BOOTs
        the app again and re-enumerates. Returns (proto, major, minor, patch,
        layout) for a stage-0/stage-1 module — layout is 0 if the stage-1 predates
        1.2.0 — or None for the legacy monolithic bootloader, which does not
        implement 0xB1 and cannot self-update (needs SWD).

        Heavier than it sounds — the module has to be in the bootloader to answer —
        so call it once per module when deciding whether a stage-1 update applies,
        not on every enumeration.
        """
        if entry.get("bus") != "i2c":
            raise NotImplementedError("bootloader_version: USB modules not yet (V203 port pending)")
        from module_flasher import ModuleFlasher
        f = ModuleFlasher(self.i2c)
        f.enter_bootloader(entry["address"])
        f.wait_for_bootloader()
        v = f.get_version()
        f.boot()                        # app is still valid — jump straight back
        self.enumerate()
        # Remember it. Reading costs a bootloader round-trip and a re-enumeration,
        # so the answer is kept on the module object and in noknok_state.json
        # ("bl": [...] or null for legacy) — one read per module lifetime, then
        # a free comparison whenever a newer stage-1 is published.
        uid = entry.get("uid")
        m = self.by_uid(uid) if uid else None
        if m is not None:
            m.bootloader = v
            self._save_state()
        return v

    @staticmethod
    def layout_of(bl_version):
        """Flash layout from a bootloader_version() tuple.

        The bootloader states it itself: byte 4 of its 0xB1 reply (stage-1
        1.2.0+). Only the bootloader knows where it puts the app, so nothing
        host-side infers it. Returns 0 for the legacy monolithic bootloader
        (no 0xB1 at all), and None when the stage-1 predates the layout byte
        (it answers 0 there) — refuse rather than guess; that module needs a
        stage-1 update first."""
        if bl_version is None:
            return 0
        if len(bl_version) < 5 or bl_version[4] == 0:
            return None
        return bl_version[4]

    def bootloader_layout(self, entry):
        """Which flash layout this module's bootloader installs apps for. Same
        cost as bootloader_version() (enters the bootloader, re-enumerates).
        An app image linked for a different layout will not run — the CRC
        cannot catch it, since it is over image bytes, not the link address."""
        return self.layout_of(self.bootloader_version(entry))

    def stage1_update(self, entry, stage1_image, app_image=None, progress=None):
        """
        Replace one module's stage-1 bootloader over the bus, then (optionally)
        restore its application.

        `entry`        a firmware_report() dict (needs 'bus' + 'address')
        `stage1_image` the stage-1 .bin (linked at 0x0400)
        `app_image`    the module's offset-linked app .bin; if given it is pushed
                       straight after the bootloader install so the module comes
                       back running. If None the module is left in the bootloader
                       with no app — you must flash one before it is usable.

        Returns {'before': (proto,maj,min,pat), 'after': (...), 'app_restored': bool}.
        Raises on a legacy module (no 0xB1) — those need SWD.

        Re-enumerates at the end, same as update_module(). Sequence per the
        module-I2C-bootloader spec §5: ERASE -> WRITE_CHUNK xN -> VERIFY_STAGE1 ->
        BOOT -> (module vanishes while stage-0 copies, ~300 ms) -> new stage-1
        answers -> version read back -> app re-pushed.
        """
        if entry.get("bus") != "i2c":
            raise NotImplementedError("stage1_update: USB modules not yet (V203 port pending)")
        from module_flasher import ModuleFlasher
        f = ModuleFlasher(self.i2c)
        before, after = f.flash_stage1(stage1_image, runtime_addr=entry.get("address"),
                                       progress=progress)
        restored = False
        if app_image:
            f.flash(app_image, runtime_addr=None, progress=progress)   # already in BL
            restored = True
        self.enumerate()
        return {"before": before, "after": after, "app_restored": restored}

    # Type code (as saved in noknok_state.json) -> manifest module_firmware key.
    _TYPE_TO_MF_KEY = {TYPE_BUZZER: "buzzer", TYPE_KNOB: "knob",
                       TYPE_LEDBUTTON: "led_button", TYPE_DISPLAY: "display"}

    def rescue_parked_module(self, get_image, logfn=print, state_file="noknok_state.json"):
        """
        DEV-31 hardening D. Call BEFORE enumerate() at start-up.

        A module parked in the bootloader at 0x7E does not enumerate, so nothing
        else in the Conductor will ever see it. Two things put a module there:
          - a bootloader or app update that lost power part-way (stage-0 finished
            the install; the app region now holds staging junk), or
          - stage-1 refusing to boot an app that crashed three times in a row
            (hardening C, last_error = 7).
        In both cases the fix is the same: push a good application. The module
        cannot say what TYPE it is, but it can say its chip UID (0xB3), and we
        remember UID -> type from the last successful enumeration.

        `get_image(entry) -> bytes` is injected, as for update_all(). The entry
        passed has 'type' (manifest key), 'bus', 'uid', 'address': None and a
        'reason' string.

        Returns None if nothing is parked, else a dict {uid, type, reason,
        action, detail}. Never raises on the "cannot help" paths — a parked
        module we cannot identify is logged and left for a human.

        Also the reason to do this FIRST: with a module already at 0x7E, starting
        another update would put two modules there and neither could be reached.
        """
        if self.i2c is None:
            return None
        from module_flasher import ModuleFlasher, ST_ERROR, _ERRMSG
        f = ModuleFlasher(self.i2c)
        st = f._read_status()
        if st is None:
            return None                                   # nothing at 0x7E — normal
        state, err = st
        reason = ("app unhealthy (stage-1 refused to boot it)"
                  if (state == ST_ERROR and err == 7) else
                  "interrupted update (no valid app)")
        logfn("Module parked in bootloader at 0x7E: %s" % reason)

        ver = f.get_version()
        if ver is None:
            logfn("  legacy bootloader (no 0xB1): cannot identify it - needs a manual flash")
            return {"uid": None, "type": None, "reason": reason,
                    "action": "none", "detail": "legacy bootloader, unidentifiable"}
        uid = f.get_uid()
        if not uid:
            logfn("  bootloader v%d.%d.%d answered but gave no UID" % tuple(ver[1:4]))
            return {"uid": None, "type": None, "reason": reason,
                    "action": "none", "detail": "no UID"}

        saved = self._state_map()
        info = saved.get(uid)
        mf_key = self._TYPE_TO_MF_KEY.get(info.get("type", 0)) if info else None
        if not mf_key:
            logfn("  UID %s is not in the module state - cannot tell what app to push" % uid)
            return {"uid": uid, "type": None, "reason": reason,
                    "action": "none", "detail": "unknown UID"}

        # The parked module is already in its bootloader, so its layout is known
        # for free — pass it on so get_image() can refuse a wrong-layout image
        # instead of pushing one that will hang the module all over again.
        entry = {"type": mf_key, "bus": "i2c", "uid": uid, "address": None,
                 "reason": reason, "bootloader": ver,
                 "bootloader_layout": self.layout_of(ver)}
        logfn("  UID %s was a %s (bootloader v%d.%d.%d, layout %s) - fetching its app..."
              % (uid, mf_key, ver[1], ver[2], ver[3], entry["bootloader_layout"]))
        try:
            image = get_image(entry)
            if not image:
                raise ValueError("no image available for %s" % mf_key)
            f.flash(image, runtime_addr=None)             # already in the bootloader
            logfn("  app pushed and booted")
            return {"uid": uid, "type": mf_key, "reason": reason,
                    "action": "reflashed", "detail": "%d bytes" % len(image)}
        except Exception as e:
            logfn("  FAILED: %s" % e)
            return {"uid": uid, "type": mf_key, "reason": reason,
                    "action": "failed", "detail": str(e)}

    def update_all(self, manifest_fw, get_image, progress=None, logfn=print,
                   exclude_uids=None):
        """
        Flash every module that firmware_report() flags needs_update.

        `get_image(entry) -> bytes` supplies the app .bin for an entry — INJECT the
        source: a local-file reader on the bench, or a WiFi downloader of
        entry['url'] in provisioning. (Keeps this method network-agnostic and
        bench-testable.)

        `exclude_uids` — modules to leave alone even though they are outdated.
        The provisioning layer uses it for modules whose bootloader cannot run
        the new image (wrong flash layout): one such module must not stop the
        others of its type from updating.

        Re-enumerates at the end so the Conductor's module instances are fresh.
        Returns the list of attempted entries, each with added 'updated':bool and
        'error':str|None.
        """
        skip = set(exclude_uids or ())
        todo = [r for r in self.firmware_report(manifest_fw)
                if r["needs_update"] and r.get("uid") not in skip]
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

        # Stable addresses (DEV-18): a module we have seen before gets the SAME
        # address it had last time, so the saved state does not change from
        # boot to boot — modules come up at 0x7F after every power cycle and
        # used to be numbered in whatever order they answered, which rewrote
        # the state on most boots. Addresses of known-but-absent modules stay
        # reserved so a newcomer cannot take them.
        known    = self._state_map()
        used     = {m.address for m in self._registry.values() if m is not None}
        reserved = {info.get("address") for info in known.values()
                    if isinstance(info, dict) and info.get("address")}

        def pick_address(uid_hex):
            a = known.get(uid_hex, {}).get("address") if isinstance(known.get(uid_hex), dict) else None
            if not a or a in used:
                a = 0x08
                while a in used or a in reserved or 0x50 <= a <= 0x57:   # 0x50-57 = brain FRAM
                    a += 1
            used.add(a)
            return a

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
            addr        = pick_address(uid_hex)

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

    def _state_map(self):
        """UID -> {address, type, bl} as last saved. Lives in the Store (FRAM /
        nvm) since DEV-18; a legacy noknok_state.json is imported once, read-
        only, when the Store has no state yet."""
        data = store().get("state")
        if isinstance(data, dict):
            return json.loads(json.dumps(data))   # a copy: callers mutate entries
        legacy = read_json("noknok_state.json")
        return legacy if isinstance(legacy, dict) else {}

    def _save_state(self, filename=None):
        """Save current module assignments (to the Store — never to the FAT)
        so next run can restore them. `filename` is accepted for backwards
        compatibility and ignored.

        MERGES into the existing map rather than replacing it. A module that
        did not answer this time — parked in its bootloader after an interrupted
        update, refused by stage-1 as unhealthy (DEV-31), or simply unplugged —
        must NOT be forgotten: its UID -> type entry is the only way
        rescue_parked_module() can tell what app to push. Found on the bench
        11 Sep 2026: one enumeration with the module parked wiped the file and
        the rescue reported 'unknown UID'.

        A stale entry's ADDRESS, however, is only kept while nothing live has
        claimed it. Found on the bench 12 Sep 2026: the buzzer died, only the
        LED Button re-enumerated and took 0x08, the merge kept the buzzer's old
        0x08 too, and _restore_state() — which only checks that *something*
        answers — then 'restored' the buzzer as a phantom object pointing at the
        LED Button. Two UIDs, one address. So: a stale entry whose address a
        live module now owns gets address None (type kept, that is all rescue
        needs), and _restore_state() skips None."""
        data = dict(self._state_map())
        before = json.dumps(data)
        live_addrs = set()
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
                entry = {"address": module.address, "type": t}
                if hasattr(module, "bootloader"):
                    bl = module.bootloader
                    entry["bl"] = list(bl) if bl else None
                elif isinstance(data.get(uid_hex), dict) and "bl" in data[uid_hex]:
                    entry["bl"] = data[uid_hex]["bl"]     # keep a value read on an earlier boot
                data[uid_hex] = entry
                live_addrs.add(module.address)
        for uid_hex, info in data.items():
            if uid_hex in self._registry or not isinstance(info, dict):
                continue
            if info.get("address") in live_addrs:
                info["address"] = None
        # This runs on every enumeration, i.e. every boot. With stable addresses
        # the map only changes when hardware changes; compare first and write
        # only then — to the Store (FRAM: power-safe; nvm: self-healing), never
        # to the FAT (DEV-18).
        if json.dumps(data) == before:
            return
        store().set("state", data)

    def _restore_state(self, filename=None):
        """
        Load saved state and ping each module at its known address.
        Returns the number of modules successfully restored.
        """
        data = self._state_map()
        if not data:
            return 0

        restored = 0
        claimed  = set()          # one address restores at most one UID
        for uid_hex, info in data.items():
            if not isinstance(info, dict):
                continue
            addr      = info.get("address")
            type_code = info.get("type", 0)

            if not addr:
                continue   # known UID, no current address (parked or moved) — rescue's job
            if addr in claimed:
                # A second UID on an address already restored this pass can only come
                # from a file written before _save_state() nulled such addresses.
                # First entry wins; a presence ping cannot tell which UID actually
                # answered, so this is the best available until modules answer a
                # runtime GET_UID and restore becomes identity-checked.
                print("  state: %s also claims 0x%02X — skipped (stale)" % (uid_hex, addr))
                continue
            if self._read(addr, 1) is None:
                continue   # module not responding
            claimed.add(addr)

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
                if "bl" in info:
                    bl = info["bl"]
                    module.bootloader = tuple(bl) if bl else None
                self._registry[uid_hex] = module
                restored += 1

        return restored

    # ── Role management ───────────────────────────────────────────────────────

    # Roles live in two places since DEV-18: the Store (FRAM: power-safe; nvm:
    # may be lost on a cut mid-write) and a copy in /data/noknok_roles.json
    # written at setup time (the app's role step — a moment the customer is
    # holding the phone). Whichever survives wins; both are written together.
    ROLES_FILE = DATA_DIR + "/noknok_roles.json"

    def _roles_map(self):
        data = store().get("roles")
        if isinstance(data, dict) and data:
            return dict(data)
        for path in (self.ROLES_FILE, "noknok_roles.json"):     # copy, then legacy root file
            data = read_json(path)
            if isinstance(data, dict):
                return data
        return {}

    def _write_roles(self, data):
        ok_store = store().set("roles", data)
        ok_file  = write_json_atomic(self.ROLES_FILE, data)
        return ok_store or ok_file

    def load_roles(self, filename=None):
        """
        Load role assignments (Store, or the /data copy). `filename` is kept
        for backwards compatibility and ignored.
        Returns True if all roles were found, False if any are missing.
        """
        mapping = self._roles_map()
        if not mapping:
            print("  No roles saved yet. Run c.setup_roles() (or assign roles in the app).")
            return False

        print("Loading roles...")
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

    def save_roles(self, mapping, filename=None):
        """
        Save a role mapping dict (Store + /data copy).
        mapping = { "role_name": module_object, ... }
        """
        data = {}
        for role_name, module in mapping.items():
            if module is not None and hasattr(module, "_uid_hex"):
                data[role_name] = module._uid_hex
            else:
                print(f"  ⚠ Skipping '{role_name}' — no UID available")

        if not self._write_roles(data):
            print("  ⚠ Could not save roles — Store write failed and filesystem "
                  "read-only (drive visible to a PC? see settings.toml NOKNOK_USB_DRIVE)")
            return

        print(f"Saved {len(data)} role(s)")

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
            if not self._write_roles(assignment):
                print("⚠ Could not save roles — Store write failed and filesystem read-only "
                      "(drive visible to a PC? see settings.toml NOKNOK_USB_DRIVE)")
                return None
            print(f"Saved {len(assignment)} role(s)")
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

    def append_role(self, role_id, uid_hex, filename=None):
        """
        Add or update a single role->UID entry and persist the whole map
        (Store + /data copy). Compatible with load_roles() ({role: uid}).
        Never raises; returns True if at least one home took the write.
        """
        uid = str(uid_hex).lower().replace("-", "").replace(" ", "")
        data = self._roles_map()
        data[role_id] = uid
        return self._write_roles(data)

    # ── Factory reset ─────────────────────────────────────────────────────────
    # Added v1.1 (Sam): hold the knob button for 5 s to wipe all credentials /
    # state and reboot into the noknok-setup provisioning AP. Call once per
    # product main-loop iteration: ks = knb.read(); c.check_factory_reset(ks)

    # Removed on reset: the credentials (wifi.json), the product script
    # (product.py) and the product-level role map — in /data (DEV-18) and, for
    # brains provisioned before /data existed, in the root. The Store loses its
    # credentials copy, roles and product settings; it deliberately KEEPS the
    # module state (UID -> address/type): a reset reboots the Pico but does NOT
    # power-cycle the modules, so they keep their addresses, and rescue needs
    # the UID -> type map. The restore logic self-heals if hardware changed.
    _RESET_FILES = (DATA_DIR + "/wifi.json", DATA_DIR + "/product.py", ROLES_FILE,
                    "wifi.json", "product.py", "noknok_roles.json")
    _RESET_KEYS  = ("wifi", "roles", SETTINGS_KEY, DEVICE_SETTINGS_KEY)

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
        # Let the confirmation beep/flash finish before the board drops out.
        factory_reset(delay=0.8)


def factory_reset(delay=0.0):
    """Wipe credentials, roles and settings (Store + files) and hard-reset into
    the provisioning AP. Shared by the knob-hold gesture and the app's
    `factory_reset` op (DEV-34) so both wipe exactly the same things."""
    print("[reset] Factory reset triggered — wiping credentials and state.")
    st = store()
    for key in Conductor._RESET_KEYS:         # Store first: power-safe on FRAM
        st.delete(key)
    # One write window for all of them (DEV-18); absent files are ignored.
    n = remove(*Conductor._RESET_FILES)
    print(f"[reset] removed {n} file(s)")
    if delay:
        time.sleep(delay)
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
        _service()                      # DEV-34: pump the app channel (before the lock)
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
        _service()                      # DEV-34: pump the app channel (before the lock)
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
        _service()                      # DEV-34: pump the app channel (before the lock)
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
        _service()                      # DEV-34: pump the app channel (before the lock)
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
        _service()                      # DEV-34: pump the app channel (before the lock)
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
        _service()                      # DEV-34: pump the app channel (before the lock)
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
        _service()                      # DEV-34: pump the app channel (before the lock)
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
        _service()                      # DEV-34: pump the app channel (before the lock)
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


