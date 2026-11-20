# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
# Pure-Python X25519 (RFC 7748) for CircuitPython - RDM-24 BLE session key prototype.
# Montgomery ladder, constant number of iterations. Big ints are native in
# CircuitPython on ESP32-S3. NOT constant-time at the CPU level (Python ints);
# acceptable for a short-lived setup-window key exchange, see RDM-24 notes.

P = 2 ** 255 - 19
A24 = 121665


def _decode_scalar(k):
    k = bytearray(k)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    return int.from_bytes(bytes(k), "little")


def _decode_u(u):
    u = bytearray(u)
    u[31] &= 127
    return int.from_bytes(bytes(u), "little")


def _encode_u(x):
    return (x % P).to_bytes(32, "little")


def x25519(k, u):
    """Scalar multiplication: 32-byte private scalar k, 32-byte u-coordinate."""
    k = _decode_scalar(k)
    x1 = _decode_u(u)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in range(254, -1, -1):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        a = (x2 + z2) % P
        aa = a * a % P
        b = (x2 - z2) % P
        bb = b * b % P
        e = (aa - bb) % P
        c = (x3 + z3) % P
        d = (x3 - z3) % P
        da = d * a % P
        cb = c * b % P
        x3 = (da + cb) % P
        x3 = x3 * x3 % P
        z3 = (da - cb) % P
        z3 = x1 * (z3 * z3 % P) % P
        x2 = aa * bb % P
        z2 = e * ((aa + A24 * e) % P) % P
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return _encode_u(x2 * pow(z2, P - 2, P))


BASE = (9).to_bytes(32, "little")


def public_key(private):
    return x25519(private, BASE)
