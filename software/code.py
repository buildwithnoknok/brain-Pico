# code.py — noknok Pico W provisioning + launcher
# Version: 0.8 (PoC — adds app-driven role assignment over the setup AP)
#
# v0.8 changes (Sam): app-driven role assignment ("PoC v1 Step 3"). Two new
#   routes on the AP HTTP server let the noknok app assign roles to physical
#   modules while the phone is still on the noknok-setup AP (before /connect):
#     POST /roles/detect  — ask the customer to interact with a module type and
#                           return the UID of the one they touched (or timeout).
#     POST /roles/save    — persist a role_id -> UID mapping to noknok_roles.json.
#   A Conductor (from noknok.py) is created + enumerated lazily on the first
#   /roles/detect call and cached, so the ~3 s enumeration happens only once.
#   The handlers are transport-agnostic (no AP-specific logic) so they could be
#   served on home WiFi later unchanged. /connect and the provisioning flow are
#   unchanged — the app calls the role endpoints BEFORE /connect.
#
# v0.5 changes (Sam):
#   - log() now timestamps every line with monotonic uptime [  12.34], and
#     after a successful WiFi connect does a one-time NTP sync so lines also
#     carry wall-clock HH:MM:SS. NTP is wrapped in try/except — never crashes boot.
#   - Instrumented + hardened the normal (STA) boot download path so we can SEE
#     where it fails: explicit branch logging, script_url source logging, and
#     2-3 retries on the direct-boot WiFi join before falling back to AP.
#   - NOTE: NTP needs adafruit_ntp.mpy in /lib. If absent, logging still works
#     (uptime only) — wall-clock is simply skipped.
#
# Boot logic:
#   1. If wifi.json exists -> connect to home WiFi directly
#        - Success -> run product.py (if downloaded)
#        - Failure -> delete wifi.json, fall through to AP mode
#   2. AP provisioning:
#        - Start hotspot "noknok-setup" (open network)
#        - Serve setup form at 192.168.4.1 via adafruit_httpserver
#        - Captive-portal probe paths serve the setup page so the OS shows "Sign in"
#        - On form submit -> stop AP -> try home WiFi -> save creds -> download -> reboot
#        - On WiFi failure -> restart AP so the user can retry
#
# IMPORTANT (development): the CYW43 radio is NOT reset by a soft reboot
# (Ctrl+D / supervisor.reload). AP mode only works reliably after a full
# POWER CYCLE. Always unplug/replug the Pico when testing AP provisioning.
#
# Required libraries in /lib:
#   adafruit_httpserver/   (folder)
#   adafruit_requests.mpy
#   adafruit_connection_manager.mpy

import json
import os
import sys
import time
import supervisor
import microcontroller
import wifi
import socketpool
import ssl
import adafruit_requests
import adafruit_connection_manager
from adafruit_httpserver import Server, Request, Response, POST

LOG_FILE = "log.txt"

# Wall-clock availability. The Pico 2W has no battery-backed RTC, so on every
# boot we only know uptime (time.monotonic). After a successful WiFi connect we
# try a one-time NTP sync; if it works, _rtc_synced flips True and log lines
# additionally carry HH:MM:SS. Until then (and if NTP fails) we log uptime only.
_rtc_synced = False

def _timestamp():
    """Build the log-line prefix.
    Always: monotonic uptime, e.g. '[  12.34]'.
    After NTP sync: also wall-clock, e.g. '[  12.34 14:03:09]'.
    Must never raise — logging has to survive any clock state."""
    try:
        up = time.monotonic()
        prefix = "[%8.2f" % up
        if _rtc_synced:
            try:
                t = time.localtime()
                prefix += " %02d:%02d:%02d" % (t.tm_hour, t.tm_min, t.tm_sec)
            except Exception:
                pass
        return prefix + "] "
    except Exception:
        return "[ ?.??] "

def log(msg):
    """Print to the serial console AND append to log.txt on the Pico.
    Every line is timestamped (uptime, plus wall-clock once NTP has synced).
    Lets us read what happened after a power-cycle (when serial output is missed).
    Open log.txt in Thonny's file browser to review."""
    line = _timestamp() + str(msg)
    sys.stdout.write(line + "\n")          # console output
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def sync_time_ntp(pool):
    """One-time NTP sync to set the RTC so logs get wall-clock timestamps.
    Best-effort: any failure (no adafruit_ntp.mpy, no internet, DNS) is swallowed
    and we simply keep uptime-only logging. Call this AFTER a WiFi connect.
    `pool` is a socketpool from the radio (reuse the existing one)."""
    global _rtc_synced
    if _rtc_synced:
        return
    try:
        import rtc
        import adafruit_ntp
        # tz_offset stays 0 -> UTC. Keeps it simple; uptime gives relative timing.
        ntp = adafruit_ntp.NTP(pool, tz_offset=0, cache_seconds=3600)
        rtc.RTC().datetime = ntp.datetime
        _rtc_synced = True
        log("[ntp] RTC synced (UTC) — wall-clock timestamps now enabled")
    except Exception as e:
        log(f"[ntp] sync skipped ({e}) — keeping uptime-only timestamps")

LOG_MAX_BYTES = 32000  # cap so the log can't fill the Pico flash (~32 KB)

def log_new_boot():
    """Append a boot separator to the log (history accumulates across boots).
    If the file has grown past LOG_MAX_BYTES, clear it first so it stays bounded."""
    try:
        try:
            if os.stat(LOG_FILE)[6] > LOG_MAX_BYTES:   # index 6 = file size
                with open(LOG_FILE, "w") as f:
                    f.write("(log trimmed — exceeded size cap)\n")
        except OSError:
            pass  # file doesn't exist yet
        with open(LOG_FILE, "a") as f:
            f.write("\n" + _timestamp() + "===== BOOT =====\n")
    except Exception:
        pass

# ── Constants ─────────────────────────────────────────────────────────────────

AP_SSID     = "noknok-setup"
AP_PASSWORD = ""   # Open network

# Script to download on first provision (PoC: hardcoded to trio demo)
# PoC test script — hosted in the PUBLIC buildwithnoknok.github.io repo so the
# Pico can fetch it without auth. (The Ecosystem repo is private -> 404 over raw.)
# In production this URL comes from the backend based on the purchased product.
SCRIPT_URL = "https://raw.githubusercontent.com/buildwithnoknok/buildwithnoknok.github.io/main/poc/trio_demo.py"

WIFI_CREDENTIALS_FILE = "wifi.json"
PRODUCT_SCRIPT_FILE   = "product.py"
WIFI_TIMEOUT_S        = 15

# Shared state: the /connect handler fills this, the main loop acts on it.
pending = {"ssid": None, "password": None, "script_url": None, "ready": False}

# ── Role assignment: lazily-created, cached Conductor ───────────────────────────
# The role endpoints need a Conductor to talk to the I2C modules. Enumeration
# takes a few seconds, so we create + enumerate it once on first use and reuse it.
_conductor = None

def get_conductor():
    """Return a cached, enumerated Conductor, creating it on first use.
    The Conductor (noknok.py) self-configures its own I2C bus on the noknok
    standard pins, so no pins are passed here. Returns None if noknok.py is
    missing or the bus can't be brought up — callers degrade gracefully."""
    global _conductor
    if _conductor is None:
        try:
            from noknok import Conductor
            log("[roles] Creating Conductor + enumerating modules (first use)...")
            c = Conductor()                 # self-configures I2C (GP8/GP9, 100 kHz)
            found = c.enumerate()           # ~3 s — discovers all connected modules
            log(f"[roles] Enumeration done — {found} module(s) found")
            _conductor = c
        except Exception as e:
            log(f"[roles] Conductor init failed: {e}")
            return None
    return _conductor

# ── HTML pages ─────────────────────────────────────────────────────────────────

HTML_SETUP = """\
<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>noknok setup</title>
<style>
body{font-family:sans-serif;max-width:360px;margin:48px auto;padding:24px;color:#222}
h1{margin:0 0 4px}p{margin:0 0 24px;color:#666;font-size:14px}
label{font-size:13px;font-weight:600;display:block;margin-bottom:4px}
input{width:100%;padding:10px;margin-bottom:16px;border:1px solid #ccc;
      border-radius:6px;box-sizing:border-box;font-size:15px}
button{width:100%;padding:12px;background:#0066ff;color:#fff;border:none;
       border-radius:6px;font-size:16px;cursor:pointer}
</style></head><body>
<h1>noknok setup</h1>
<p>Connect to your home WiFi network.</p>
<form method="POST" action="/connect">
<label>Network name</label>
<input type="text" name="ssid" placeholder="Your WiFi name" required>
<label>Password</label>
<input type="password" name="password" placeholder="WiFi password">
<button type="submit">Connect</button>
</form></body></html>"""

HTML_SUCCESS = """\
<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>noknok setup</title>
<style>body{font-family:sans-serif;max-width:360px;margin:48px auto;padding:24px;color:#222}
h1{color:#00aa44}</style></head><body>
<h1>Connected!</h1>
<p>Your noknok device is joining your network and downloading its software.</p>
<p>You can close this page. The device will continue on its own.</p>
</body></html>"""

# ── Filesystem helpers ─────────────────────────────────────────────────────────

def load_wifi_credentials():
    """Return saved {"ssid":..., "password":...} or None."""
    try:
        with open(WIFI_CREDENTIALS_FILE, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None

def save_wifi_credentials(ssid, password, script_url=None):
    with open(WIFI_CREDENTIALS_FILE, "w") as f:
        json.dump(
            {"ssid": ssid, "password": password, "script_url": script_url}, f
        )
    log("[storage] Saved wifi.json")

def delete_wifi_credentials():
    try:
        os.remove(WIFI_CREDENTIALS_FILE)
        log("[storage] Deleted wifi.json")
    except OSError:
        pass

def _url_decode(s):
    """Percent-decode an application/x-www-form-urlencoded value.
    '+' -> space, %XX -> byte. Robust: leaves malformed sequences as-is."""
    if not s:
        return s
    s = s.replace("+", " ")
    out = bytearray()
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "%" and i + 2 < n:
            try:
                out.append(int(s[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(ord(c))
        i += 1
    try:
        return out.decode("utf-8")
    except Exception:
        return out.decode("latin-1")

def product_script_exists():
    try:
        os.stat(PRODUCT_SCRIPT_FILE)
        return True
    except OSError:
        return False

# ── WiFi ──────────────────────────────────────────────────────────────────────

def connect_wifi(ssid, password):
    """Join a WiFi network. Returns True on success."""
    log(f"[wifi] Connecting to '{ssid}'...")
    try:
        wifi.radio.connect(ssid, password, timeout=WIFI_TIMEOUT_S)
        log(f"[wifi] Connected — IP: {wifi.radio.ipv4_address}")
        return True
    except Exception as e:
        log(f"[wifi] Failed: {e}")
        return False

# ── Script download ────────────────────────────────────────────────────────────

def download_and_save_script(script_url=None):
    """Download product script from GitHub and save as product.py.
    Uses adafruit_connection_manager so DNS and the SSL context are set up
    correctly for the radio. Retries a few times since DNS can need a moment
    to settle after a fresh WiFi join."""

    # Resolve the URL and log WHERE it came from. This is the key diagnostic:
    # it tells us whether the app-supplied script_url actually arrived in
    # wifi.json, or whether we silently fell back to the hardcoded PoC URL.
    if script_url:
        url = script_url
        log(f"[download] script_url source: wifi.json (app-supplied)")
    else:
        url = SCRIPT_URL
        log(f"[download] script_url source: fallback SCRIPT_URL (none in wifi.json)")
    log(f"[download] resolved url: {url}")

    # Give the network stack a moment to settle DNS after connecting
    time.sleep(2)
    log(f"[wifi] DNS server: {wifi.radio.ipv4_dns}  gateway: {wifi.radio.ipv4_gateway}")

    # Build the session via the connection manager (correct DNS + SSL setup)
    pool    = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
    context = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
    session = adafruit_requests.Session(pool, context)

    for attempt in range(1, 4):  # up to 3 tries
        log(f"[download] Attempt {attempt}: fetching {url}")
        try:
            response = session.get(url, timeout=30)

            if response.status_code != 200:
                log(f"[download] HTTP {response.status_code}")
                response.close()
                return False

            content = response.text
            response.close()

            with open(PRODUCT_SCRIPT_FILE, "w") as f:
                f.write(content)

            log(f"[download] SUCCESS — saved {PRODUCT_SCRIPT_FILE} ({len(content)} bytes) from {url}")
            return True

        except Exception as e:
            log(f"[download] Attempt {attempt} error: {e}")
            time.sleep(2)  # wait and retry — usually DNS settling

    log(f"[download] FAILED — all attempts exhausted for {url}")
    return False

# ── HTTP routes ────────────────────────────────────────────────────────────────

def register_routes(server):
    """Attach all routes to the given adafruit_httpserver Server."""

    @server.route("/")
    def _root(request: Request):
        log("[http] GET / — serving setup page")
        return Response(request, HTML_SETUP, content_type="text/html")

    @server.route("/connect", POST)
    def _connect(request: Request):
        form = request.form_data
        ssid = _url_decode(form.get("ssid") or "").strip() if form else ""
        pw   = _url_decode(form.get("password") or "") if form else ""
        url  = _url_decode(form.get("script_url") or "").strip() if form else ""

        if not ssid:
            # No network name entered — show the form again
            return Response(request, HTML_SETUP, content_type="text/html")

        # Store credentials; the main loop will act on them after the
        # success page has been delivered to the browser.
        pending["ssid"]       = ssid
        pending["password"]   = pw
        pending["script_url"] = url
        pending["ready"]      = True
        log(f"[ap] Credentials received for '{ssid}'")
        return Response(request, HTML_SUCCESS, content_type="text/html")

    # ── Role assignment endpoints (v0.8) ─────────────────────────────────────
    # Called by the noknok app BEFORE /connect, while the phone is on the
    # noknok-setup AP. Transport-agnostic: no AP-specific logic lives here.

    @server.route("/roles/detect", POST)
    def _roles_detect(request: Request):
        """Ask the customer to interact with a module of a given type and return
        the UID of the one they touched.
        Form fields:
          module_type : "knob", "led_button", or "buzzer"
          exclude     : optional comma-separated uid_hex of already-assigned modules
        Response JSON: {"uid": "<hex>", "type": "<module_type>"} on detection,
                       or {"timeout": true} if nobody interacted in time.
        Blocks up to ~20 s — acceptable for a single-client setup interaction."""
        form = request.form_data
        module_type = (_url_decode(form.get("module_type") or "").strip()
                       if form else "")
        exclude_raw = (_url_decode(form.get("exclude") or "").strip()
                       if form else "")
        exclude = [u.strip() for u in exclude_raw.split(",") if u.strip()] \
            if exclude_raw else []

        log(f"[roles] /roles/detect type='{module_type}' "
            f"exclude={len(exclude)} module(s)")

        c = get_conductor()
        if c is None:
            # No bus / noknok.py — degrade gracefully, never 500.
            return Response(request, json.dumps({"timeout": True}),
                            content_type="application/json")

        try:
            uid = c.detect_interaction(module_type, timeout=20, exclude=exclude)
        except Exception as e:
            log(f"[roles] detect_interaction error: {e}")
            uid = None

        if uid:
            log(f"[roles] detected uid={uid} for type='{module_type}'")
            body = json.dumps({"uid": uid, "type": module_type})
        else:
            log(f"[roles] detect timed out for type='{module_type}'")
            body = json.dumps({"timeout": True})
        return Response(request, body, content_type="application/json")

    @server.route("/roles/save", POST)
    def _roles_save(request: Request):
        """Persist a role_id -> UID mapping to noknok_roles.json.
        Form fields: role_id, uid.
        Response JSON: {"ok": true} (or {"ok": false} if the write failed)."""
        form = request.form_data
        role_id = (_url_decode(form.get("role_id") or "").strip()
                   if form else "")
        uid = (_url_decode(form.get("uid") or "").strip()
               if form else "")

        log(f"[roles] /roles/save role_id='{role_id}' uid={uid}")

        if not role_id or not uid:
            return Response(request, json.dumps({"ok": False}),
                            content_type="application/json")

        c = get_conductor()
        ok = False
        if c is not None:
            try:
                ok = bool(c.append_role(role_id, uid))
            except Exception as e:
                log(f"[roles] append_role error: {e}")
                ok = False

        return Response(request, json.dumps({"ok": ok}),
                        content_type="application/json")

    # Captive-portal probe paths: serving the setup page (instead of the
    # expected 204/empty) makes iOS/Android/Windows show a "Sign in to
    # network" prompt that opens our page automatically.
    @server.route("/hotspot-detect.html")        # iOS / macOS
    @server.route("/library/test/success.html")  # iOS fallback
    @server.route("/generate_204")               # Android
    @server.route("/gen_204")                    # Android
    @server.route("/ncsi.txt")                   # Windows
    @server.route("/connecttest.txt")            # Windows
    @server.route("/canonical.html")             # Firefox
    @server.route("/redirect")                   # generic
    def _captive(request: Request):
        log(f"[http] captive probe {request.path} — serving setup page")
        return Response(request, HTML_SETUP, content_type="text/html")

# ── AP provisioning ────────────────────────────────────────────────────────────

def run_ap_provisioning():
    """
    Start a WiFi hotspot + HTTP server and wait for credentials.
    Retries (restarts the AP) if the WiFi join fails.
    """
    while True:  # outer loop lets us rebuild the AP after a failed WiFi attempt
        wifi.radio.start_ap(ssid=AP_SSID, password=AP_PASSWORD)
        ap_ip = str(wifi.radio.ipv4_address_ap)
        log(f"[ap] Hotspot started: '{AP_SSID}' — http://{ap_ip}")

        pool   = socketpool.SocketPool(wifi.radio)
        server = Server(pool, debug=True)
        register_routes(server)
        # Bind to 0.0.0.0 (all interfaces) on port 80.
        # NOTE: adafruit_httpserver defaults to port 5000 — we MUST pass port=80
        # so plain http://192.168.4.1 (no port) reaches the server.
        server.start("0.0.0.0", port=80)
        log(f"[ap] HTTP server listening — open http://{ap_ip}")

        # Reset state and serve requests until credentials arrive
        pending["ready"] = False
        last_beat = time.monotonic()
        while not pending["ready"]:
            try:
                server.poll()
            except Exception as e:
                log(f"[http] poll error: {e}")
            now = time.monotonic()
            if now - last_beat > 5:
                log("[ap] waiting for setup… (server alive)")
                last_beat = now
            time.sleep(0.01)

        # Credentials received — give the success page a moment to flush
        time.sleep(1)
        ssid = pending["ssid"]
        pw   = pending["password"]
        su   = pending["script_url"]

        wifi.radio.stop_ap()
        log("[ap] Hotspot stopped — attempting WiFi join")

        # The Pico W radio often fails the FIRST join right after AP mode with
        # "Unknown failure 205", then succeeds on a retry. Try a few times
        # before giving up — otherwise a transient blip kicks the user back to
        # re-entering credentials in the app.
        joined = False
        for attempt in range(1, 4):
            log(f"[ap] WiFi join attempt {attempt}/3")
            if connect_wifi(ssid, pw):
                joined = True
                break
            if attempt < 3:
                time.sleep(3)

        if joined:
            # Credentials verified. Save them, then do a FULL hardware reset.
            # We do NOT download here: this radio was just in AP mode, and the
            # AP->STA transition (without a chip reset) leaves DNS broken.
            # A hardware reset brings the radio up clean in STA-only mode, and
            # main() will then connect + download on the fresh boot.
            save_wifi_credentials(ssid, pw, su)
            log("[boot] Credentials saved — hardware reset into WiFi mode")
            time.sleep(2)  # let the success page flush to the browser
            microcontroller.reset()
            return
        else:
            # WiFi join failed after all retries — restart the AP so the user
            # can retry. We do NOT supervisor.reload() here: a soft reload leaves
            # the CYW43 radio in a state where AP mode no longer works.
            log("[ap] WiFi join failed after 3 attempts — restarting hotspot for retry")
            try:
                server.stop()
            except Exception:
                pass
            pending["ready"] = False
            time.sleep(1)
            # loop back to top -> start_ap again

# ── Main flow ──────────────────────────────────────────────────────────────────

def main():
    log_new_boot()
    log("[boot] noknok Pico W — starting")

    creds = load_wifi_credentials()

    if creds:
        log("[boot] Found wifi.json — connecting directly")
        log(f"[boot] wifi.json ssid='{creds.get('ssid')}' "
            f"script_url={'present' if creds.get('script_url') else 'MISSING'}")

        # Direct-boot WiFi join can be flaky right after a hardware reset (the
        # radio/AP may need a moment). Try a few times before giving up to AP.
        connected = False
        for attempt in range(1, 4):  # up to 3 attempts
            log(f"[boot] WiFi join attempt {attempt}/3")
            if connect_wifi(creds["ssid"], creds["password"]):
                connected = True
                break
            if attempt < 3:
                time.sleep(3)  # brief pause before retry

        # Explicit post-connect marker so the log never goes silent here.
        log(f"[boot] connect_wifi result: {'CONNECTED' if connected else 'FAILED'}")

        if connected:
            # One-time NTP sync so subsequent log lines carry wall-clock time.
            # Best-effort — wrapped internally, never blocks the boot.
            try:
                sync_time_ntp(socketpool.SocketPool(wifi.radio))
            except Exception as e:
                log(f"[ntp] setup error (ignored): {e}")

            if product_script_exists():
                log(f"[boot] product.py present — running {PRODUCT_SCRIPT_FILE}")
                exec(open(PRODUCT_SCRIPT_FILE).read(), {"__name__": "__main__"})
            else:
                log("[boot] product.py missing — will download")
                if download_and_save_script(creds.get("script_url")):
                    log("[boot] download OK — reloading to run product.py")
                    supervisor.reload()
                else:
                    log("[boot] Download failed — starting AP provisioning")
                    delete_wifi_credentials()
                    run_ap_provisioning()
        else:
            log("[boot] WiFi failed after 3 tries — clearing credentials, starting AP provisioning")
            delete_wifi_credentials()
            run_ap_provisioning()
    else:
        log("[boot] No credentials — starting AP provisioning")
        run_ap_provisioning()

main()


