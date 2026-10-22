#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# dhcp_offers.py [IFACE] [SECONDS] - DEV-113: who answers DHCP on this network?
#
# Runs on the bench host (needs root for UDP port 68):
#   echo raspberry | sudo -S python3 dhcp_offers.py wlan0 8
# Sends ONE DHCPDISCOVER with a random, locally administered MAC and prints every
# DHCPOFFER that comes back: server, offered address, gateway, DNS. Requests nothing,
# so no lease is taken. A healthy home network shows exactly one offer (the router).
# An offer of 192.168.4.16-23 with gateway/DNS 192.168.4.1 is a noknok brain (any
# CircuitPython Pico) whose setup hotspot's DHCP server answers on its HOME-WiFi side.
import os, random, select, socket, struct, sys, time

IFACE = sys.argv[1] if len(sys.argv) > 1 else "wlan0"
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 8
mac = bytes([0x02] + [random.randrange(256) for _ in range(5)])
xid = random.getrandbits(32)

pkt = struct.pack("!BBBBIHH4s4s4s4s16s64s128s4s",
                  1, 1, 6, 0, xid, 0, 0x8000, b"\0" * 4, b"\0" * 4, b"\0" * 4, b"\0" * 4,
                  mac + b"\0" * 10, b"\0" * 64, b"\0" * 128, b"\x63\x82\x53\x63")
pkt += bytes([53, 1, 1, 55, 4, 1, 3, 6, 54, 255])          # DISCOVER, ask for mask/router/dns

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
s.setsockopt(socket.SOL_SOCKET, 25, IFACE.encode() + b"\0")  # SO_BINDTODEVICE
s.bind(("0.0.0.0", 68))
s.sendto(pkt, ("255.255.255.255", 67))
print("DISCOVER sent on %s (mac %s, xid %08x), listening %.0f s" % (IFACE, mac.hex(":"), xid, SECS))

def opts(b):
    out, i = {}, 0
    while i < len(b) and b[i] != 255:
        if b[i] == 0:
            i += 1; continue
        out[b[i]] = b[i + 2:i + 2 + b[i + 1]]; i += 2 + b[i + 1]
    return out

ip = lambda b: ".".join(str(x) for x in b[:4]) if b else "-"
n, end = 0, time.time() + SECS
while time.time() < end:
    r, _, _ = select.select([s], [], [], max(0, end - time.time()))
    if not r:
        break
    data, src = s.recvfrom(2048)
    if len(data) < 240 or struct.unpack("!I", data[4:8])[0] != xid:
        continue
    o = opts(data[240:])
    kind = {2: "OFFER", 5: "ACK", 6: "NAK"}.get(o.get(53, b"\0")[0], "?")
    n += 1
    rogue = data[16:19] == bytes([192, 168, 4]) and o.get(3, b"")[:4] == bytes([192, 168, 4, 1])
    print("%s from %s: server %s offers %s  gateway %s  dns %s%s" % (
        kind, src[0], ip(o.get(54)), ip(data[16:20]), ip(o.get(3)), ip(o.get(6)),
        "   <-- noknok setup-hotspot DHCP (rogue)" if rogue else ""))
print("RESULT offers=%d" % n)
