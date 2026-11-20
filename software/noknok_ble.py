# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
# noknok_ble.py - Device Protocol v1 over Bluetooth LE (RDM-24 prototype, exploration).
#
# A second CARRIER for the existing noknok_rpc Dispatcher, next to the HTTP carrier:
# the very same JSON ops (hello, wifi.scan, provision, ...) travel over the
# Nordic UART Service (NUS) instead of POST /rpc. Framing: one JSON object per
# line ("\n"-terminated) in both directions; replies are split into notification
# chunks by this module and re-joined by the app at the "\n".
#
# Only boards whose CircuitPython has native _bleio (ESP32-S3: yes; Pico W /
# Pico 2 W: no) can use it. available() lets code.py decide at runtime, so ONE
# codebase serves both brain types: BLE where present, setup hotspot everywhere.
#
# Status: prototype. Session encryption (X25519 + AES-CTR + HMAC, optional PoP) lives in
# noknok_ble_session.py; ops in ENCRYPTED_ONLY are refused on a plaintext link.

import json
import time

__version__ = "0.2.0"

ENCRYPTED_ONLY = ("provision",)   # ops that carry secrets: never accepted in plaintext

NUS_NAME_PREFIX = "noknok-setup"
MAX_REQUEST = 2048          # bytes of one request line; longer = protocol error
CHUNK = 20                  # safe notification payload for the default ATT MTU (23)


def available():
    """True when this CircuitPython build has a usable native BLE adapter."""
    try:
        import _bleio
        return _bleio.adapter is not None
    except Exception:
        return False


class BleCarrier:
    """poll()-driven like noknok_rpc.HttpCarrier: call poll() from the service loop."""

    def __init__(self, disp, name, logfn=print, pop=b""):
        from adafruit_ble import BLERadio
        from adafruit_ble.advertising import Advertisement
        from adafruit_ble.advertising.standard import ProvideServicesAdvertisement
        from adafruit_ble.services.nordic import UARTService
        self._disp = disp
        self._log = logfn
        self.name = name
        self.radio = BLERadio()
        self.radio.name = name
        self.uart = UARTService()
        self._adv = ProvideServicesAdvertisement(self.uart)     # flags + 128-bit NUS UUID
        self._scan_resp = Advertisement()
        self._scan_resp.complete_name = name                    # name in the scan response
        self._buf = b""
        self._was_connected = False
        self._pop = pop
        self.session = None
        self.stats = {"requests": 0, "bytes_in": 0, "bytes_out": 0, "connects": 0,
                      "encrypted": 0, "rejected": 0}

    def start(self):
        if not self.radio.advertising:
            self.radio.start_advertising(self._adv, self._scan_resp)
            self._log("[ble] advertising '%s' (NUS)" % self.name)

    def stop(self):
        try:
            if self.radio.advertising:
                self.radio.stop_advertising()
            for c in self.radio.connections:
                c.disconnect()
        except Exception as e:
            self._log("[ble] stop: %r" % (e,))

    def _send(self, obj):
        data = json.dumps(obj).encode() + b"\n"
        for i in range(0, len(data), CHUNK):
            self.uart.write(data[i:i + CHUNK])
        self.stats["bytes_out"] += len(data)

    def poll(self):
        connected = self.radio.connected
        if connected and not self._was_connected:
            self.stats["connects"] += 1
            self._buf = b""
            self.session = None              # a new link always starts unencrypted
            self._log("[ble] app connected")
        if not connected:
            if self._was_connected:
                self._log("[ble] app disconnected")
            self._was_connected = False
            self.start()                     # advertising stops on connect; resume
            return
        self._was_connected = True
        n = self.uart.in_waiting
        if not n:
            return
        chunk = self.uart.read(n)
        self.stats["bytes_in"] += len(chunk)
        self._buf += chunk
        while b"\n" in self._buf:
            line, _, self._buf = self._buf.partition(b"\n")
            line = line.strip()
            if not line:
                continue
            self.stats["requests"] += 1
            try:
                msg = json.loads(line)
            except ValueError:
                self._send({"id": None, "ok": False, "error": "bad json"})
                continue
            self._handle(msg)
        if len(self._buf) > MAX_REQUEST:
            self._buf = b""
            self._send({"id": None, "ok": False, "error": "request too large"})

    def _handle(self, msg):
        if not isinstance(msg, dict):
            self._send({"id": None, "ok": False, "error": "bad message"})
            return
        # 1. Handshake (plaintext by nature).
        if msg.get("op") == "session.start":
            from noknok_ble_session import Session
            args = msg.get("args") or {}
            s = Session(self._pop)
            try:
                t = time.monotonic()
                res = s.start(args.get("pub", ""), args.get("nonce", ""))
            except Exception as e:
                self._send({"id": msg.get("id"), "ok": False, "error": "handshake", "detail": str(e)})
                return
            self.session = s
            res.update({"id": msg.get("id"), "ok": True,
                        "ms": int((time.monotonic() - t) * 1000)})
            self._log("[ble] encrypted session up (%d ms, pop=%s)" % (res["ms"], res["pop"]))
            self._send(res)
            return
        # 2. Encrypted frame: open -> dispatch -> seal.
        if "e" in msg:
            if self.session is None:
                self._send({"id": None, "ok": False, "error": "no session"})
                return
            try:
                inner = json.loads(self.session.open(msg))
            except Exception as e:
                self.stats["rejected"] += 1
                self._log("[ble] frame rejected: %s" % (e,))
                self._send({"id": None, "ok": False, "error": "bad frame"})
                return
            self.stats["encrypted"] += 1
            reply = self._disp.dispatch(inner)
            self._send(self.session.seal(json.dumps(reply).encode()))
            return
        # 3. Plaintext request: everything except secret-carrying ops.
        if msg.get("op") in ENCRYPTED_ONLY:
            self.stats["rejected"] += 1
            self._send({"id": msg.get("id"), "ok": False, "error": "encryption required"})
            return
        self._send(self._disp.dispatch(msg))
