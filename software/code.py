# code.py — noknok Pico W provisioning + launcher
# Version: 0.4 (PoC — WiFi AP mode; script_url from /connect makes Pico product-agnostic)
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

def log(msg):
    """Print to the serial console AND append to log.txt on the Pico.
    Lets us read what happened after a power-cycle (when serial output is missed).
    Open log.txt in Thonny's file browser to review."""
    line = str(msg)
    sys.stdout.write(line + "\n")          # console output
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

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
            f.write("\n===== BOOT =====\n")
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

    url = script_url or SCRIPT_URL

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

            log(f"[download] Saved {PRODUCT_SCRIPT_FILE} ({len(content)} bytes)")
            return True

        except Exception as e:
            log(f"[download] Attempt {attempt} error: {e}")
            time.sleep(2)  # wait and retry — usually DNS settling

    log("[download] All attempts failed")
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
        ssid = (form.get("ssid") or "").strip() if form else ""
        pw   = (form.get("password") or "") if form else ""
        url  = (form.get("script_url") or "").strip() if form else ""

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

        if connect_wifi(ssid, pw):
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
            # WiFi join failed — restart the AP so the user can retry.
            # We do NOT supervisor.reload() here: a soft reload leaves the
            # CYW43 radio in a state where AP mode no longer works.
            log("[ap] WiFi join failed — restarting hotspot for retry")
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
        if connect_wifi(creds["ssid"], creds["password"]):
            if product_script_exists():
                log(f"[boot] Running {PRODUCT_SCRIPT_FILE}")
                exec(open(PRODUCT_SCRIPT_FILE).read(), {"__name__": "__main__"})
            else:
                log("[boot] No product script — downloading")
                if download_and_save_script(creds.get("script_url")):
                    supervisor.reload()
                else:
                    log("[boot] Download failed — starting AP provisioning")
                    delete_wifi_credentials()
                    run_ap_provisioning()
        else:
            log("[boot] WiFi failed — clearing credentials, starting AP provisioning")
            delete_wifi_credentials()
            run_ap_provisioning()
    else:
        log("[boot] No credentials — starting AP provisioning")
        run_ap_provisioning()

main()
