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
import os

LABEL = b"noknok-ble-v1"
APP_TO_BRAIN = 1
BRAIN_TO_APP = 2


# SHA-256 in pure Python ON PURPOSE. CircuitPython 11.0.0-alpha.3 on the ESP32-S3: every
# hashlib.new() sets up a PSA hash operation that is never freed, and gc does not get the
# memory back. About 1700-1950 hash objects use up the ESP-IDF internal heap; then hashlib
# raises "Unsupported hash algorithm" and BLE fails (start_advertising 0x0207, or
# espidf.MemoryError on BLE init). With ~7 hashes per session the brain died after ~128
# BLE connections (RDM-24 / DEV-274 soak, 24 Nov 2026). The session hashes a few short
# messages, so the pure-Python cost is small. aesio does not leak (6000 objects tested).
_K = (
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2)
_H0 = (0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19)
_M = 0xFFFFFFFF


def _rotr(x, n):
    return ((x >> n) | (x << (32 - n))) & _M


def sha256(*parts):
    """SHA-256 of the concatenated parts, in pure Python (see the note above)."""
    data = b"".join(parts)
    n = len(data)
    data += b"\x80" + bytes((55 - n) % 64) + (n * 8).to_bytes(8, "big")
    h = list(_H0)
    w = [0] * 64
    for off in range(0, len(data), 64):
        for i in range(16):
            w[i] = int.from_bytes(data[off + 4 * i:off + 4 * i + 4], "big")
        for i in range(16, 64):
            a, b = w[i - 15], w[i - 2]
            s0 = _rotr(a, 7) ^ _rotr(a, 18) ^ (a >> 3)
            s1 = _rotr(b, 17) ^ _rotr(b, 19) ^ (b >> 10)
            w[i] = (w[i - 16] + s0 + w[i - 7] + s1) & _M
        a, b, c, d, e, f, g, hh = h
        for i in range(64):
            t1 = (hh + (_rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25)) + ((e & f) ^ (~e & g))
                  + _K[i] + w[i]) & _M
            t2 = ((_rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) & _M
            hh, g, f, e, d, c, b, a = g, f, e, (d + t1) & _M, c, b, a, (t1 + t2) & _M
        h = [(x + y) & _M for x, y in zip(h, (a, b, c, d, e, f, g, hh))]
    return b"".join(x.to_bytes(4, "big") for x in h)


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
