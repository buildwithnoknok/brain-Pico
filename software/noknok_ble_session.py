# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
# noknok_ble_session.py - session encryption for the BLE carrier (RDM-24 prototype).
#
# Handshake (plaintext, one round trip):
#   app   -> {"id": n, "op": "session.start", "args": {"pub": <hex32>, "nonce": <hex16>}}
#   brain -> {"id": n, "ok": true, "pub": <hex32>, "nonce": <hex16>, "pop": true|false}
# Keys:  K     = SHA256("noknok-ble-v1" | shared | app_pub | brain_pub | app_nonce | brain_nonce | pop)
#        k_enc = SHA256(K | "enc"),  k_mac = SHA256(K | "mac")
# After: every line is {"c": <counter>, "e": <hex ciphertext>, "m": <hex HMAC-SHA256[:16]>}
#        AES-256-CTR, IV = direction(1 B: 0x01 app->brain, 0x02 brain->app) | 0 x 7 | counter (8 B BE)
#        MAC over direction | counter (8 B BE) | ciphertext. Counters strictly increase (no replay).
# pop = optional proof-of-possession code (e.g. printed on the label, settings NOKNOK_BLE_POP).
# Without a PoP this stops passive sniffing but not an active man-in-the-middle (like BLE
# "Just Works"); with a PoP the attacker also needs the code from the label.
import binascii
import hashlib
import os

LABEL = b"noknok-ble-v1"
APP_TO_BRAIN = 1
BRAIN_TO_APP = 2


def sha256(*parts):
    h = hashlib.new("sha256")
    for p in parts:
        h.update(p)
    return h.digest()


def hmac_sha256(key, msg):
    if len(key) > 64:
        key = sha256(key)
    key = key + bytes(64 - len(key))
    ipad = bytes(b ^ 0x36 for b in key)
    opad = bytes(b ^ 0x5C for b in key)
    return sha256(opad, sha256(ipad, msg))


def _iv(direction, counter):
    return bytes([direction]) + bytes(7) + counter.to_bytes(8, "big")


def _eq(a, b):
    """Compare MACs without an early exit."""
    if len(a) != len(b):
        return False
    r = 0
    for x, y in zip(a, b):
        r |= x ^ y
    return r == 0


class Session:
    """Brain side. start() answers session.start; open()/seal() wrap every later line."""

    def __init__(self, pop=b""):
        self.pop = pop or b""
        self.k_enc = None
        self.k_mac = None
        self.rx = 0              # last app->brain counter accepted
        self.tx = 0              # last brain->app counter sent

    @property
    def active(self):
        return self.k_enc is not None

    def start(self, app_pub_hex, app_nonce_hex):
        import x25519
        app_pub = binascii.unhexlify(app_pub_hex)
        app_nonce = binascii.unhexlify(app_nonce_hex)
        if len(app_pub) != 32 or len(app_nonce) != 16:
            raise ValueError("bad handshake")
        priv = os.urandom(32)
        pub = x25519.public_key(priv)
        shared = x25519.x25519(priv, app_pub)
        if shared == bytes(32):
            raise ValueError("bad peer key")
        nonce = os.urandom(16)
        k = sha256(LABEL, shared, app_pub, pub, app_nonce, nonce, self.pop)
        self.k_enc, self.k_mac = sha256(k, b"enc"), sha256(k, b"mac")
        self.rx = self.tx = 0
        return {"pub": binascii.hexlify(pub).decode(), "nonce": binascii.hexlify(nonce).decode(),
                "pop": bool(self.pop)}

    def _crypt(self, direction, counter, data):
        import aesio
        out = bytearray(len(data))
        aesio.AES(self.k_enc, aesio.MODE_CTR, _iv(direction, counter)).encrypt_into(data, out)
        return bytes(out)

    def open(self, frame):
        """Decrypt an app->brain frame dict. Raises ValueError on MAC / replay errors."""
        c = int(frame["c"])
        ct = binascii.unhexlify(frame["e"])
        mac = binascii.unhexlify(frame["m"])
        want = hmac_sha256(self.k_mac, bytes([APP_TO_BRAIN]) + c.to_bytes(8, "big") + ct)[:16]
        if not _eq(mac, want):
            raise ValueError("bad mac")
        if c <= self.rx:
            raise ValueError("replay")
        self.rx = c
        return self._crypt(APP_TO_BRAIN, c, ct)

    def seal(self, plaintext):
        self.tx += 1
        ct = self._crypt(BRAIN_TO_APP, self.tx, plaintext)
        mac = hmac_sha256(self.k_mac, bytes([BRAIN_TO_APP]) + self.tx.to_bytes(8, "big") + ct)[:16]
        return {"c": self.tx, "e": binascii.hexlify(ct).decode(), "m": binascii.hexlify(mac).decode()}
