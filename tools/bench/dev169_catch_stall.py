#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# dev169_catch_stall.py IP [BACKGROUND] - DEV-169: find WHERE the product loop is held. Sends a
# hello every second while a background client runs every 3 s (settings | late_body:
# headers, body 0.6 s later); the moment one hello has waited 1.5 s it presses Ctrl-C on the
# brain's console and prints the traceback - the line the loop was stuck in. Stops the
# product (reset the brain afterwards). Needs the console (no other listener).
import glob, json, socket, sys, threading, time
import serial

IP = sys.argv[1]
BG = sys.argv[2] if len(sys.argv) > 2 else "settings"
con = serial.Serial(glob.glob("/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00")[0], 115200, timeout=0.2)
con.read(5000)


def req(op):
    b = json.dumps({"id": 1, "op": op}).encode()
    return (b"POST /rpc HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
            b"Content-Length: %d\r\n\r\n" % len(b)), b


def call(op, late=0.0):
    head, body = req(op)
    try:
        c = socket.create_connection((IP, 80), timeout=10)
        if late:
            c.sendall(head)
            time.sleep(late)
            c.sendall(body)
        else:
            c.sendall(head + body)
        while c.recv(4096):
            pass
        c.close()
    except Exception:
        pass


def background():
    while True:
        if BG == "late_body":
            threading.Thread(target=call, args=("hello", 0.6), daemon=True).start()
        else:
            threading.Thread(target=call, args=("settings.get",), daemon=True).start()
        time.sleep(3)


threading.Thread(target=background, daemon=True).start()
t_end = time.time() + 120
while time.time() < t_end:
    th = threading.Thread(target=call, args=("hello",), daemon=True)
    th.start()
    t0 = time.monotonic()
    while th.is_alive():
        if time.monotonic() - t0 > 1.5:
            print("a hello waited 1.5 s - Ctrl-C now")
            con.write(b"\x03")
            time.sleep(2)
            print(con.read(8000).decode("utf-8", "replace")[-1500:])
            sys.exit(0)
        time.sleep(0.05)
    time.sleep(1)
print("no stall in 120 s")
