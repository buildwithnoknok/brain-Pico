#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# bench_dev189_park.py IP [RUNS] [guard] - DEV-189: a parked brain (3 crash strikes) must stay reachable
# and leave park from the app's `reboot`. Runs on the bench host (the Pi) with the brain on USB
# (pico.py in ~/dev/pico). Per run:
#   1. nvm[0] = 0xC3 (3 strikes) and a SOFT reboot (Ctrl-D): the soft reload keeps the count, so
#      main() parks without starting the product (a hard reset would clear the strikes).
#   2. poll `hello` every 2 s: time until it answers (limit 90 s), its `state` (expect "parked"),
#      `status.strikes` (expect 3).
#   3. send `reboot`: hard reset = fresh start; poll until `hello` answers again with state
#      "provisioned" and strikes 0 (limit 120 s).
# With the 3rd argument `guard` it checks the parked network fallback instead (code.py 0.40):
#   G1 parked with the bench flag NOKNOK_BENCH_PARK_NET_FAIL = 1: no hello (parked offline);
#   G2 flag removed + hard reset: hello answers "provisioned" again. The console proof that
#      code.py kept running (knob loop) is read separately with a serial capture.
# One result line per run + a final PASS/FAIL count. Needs BENCH_SUDO_PW? No. Never touches files.
import json, os, socket, subprocess, sys, time

IP = sys.argv[1]
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
PICO = os.path.expanduser("~/dev/pico/pico.py")


def rpc(op, args=None, timeout=4.0):
    b = json.dumps({"id": 1, "op": op, "args": args or {}}).encode()
    try:
        s = socket.create_connection((IP, 80), timeout=timeout)
        s.sendall(b"POST /rpc HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  b"Connection: close\r\nContent-Length: %d\r\n\r\n" % len(b) + b)
        d = b""
        while True:
            c = s.recv(4096)
            if not c:
                break
            d += c
        s.close()
        return json.loads(d.split(b"\r\n\r\n", 1)[1])
    except Exception:
        return None


def wait_hello(limit, want_state=None):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        h = rpc("hello")
        if h and (want_state is None or h.get("state") == want_state):
            return time.monotonic() - t0, h
        time.sleep(2)
    return None, None


def pico(code, tmo=60):
    r = subprocess.run([sys.executable, PICO, "exec", code], capture_output=True, text=True, timeout=tmo)
    return r.stdout


CAPTURE = os.environ.get("CAPTURE")     # CAPTURE=dir: soft reboot with the console port held open
_cap = []                                #   and logged to dir/bootN.log (needs pyserial)
_boots = [0]


def reset_soft():
    if not CAPTURE:
        subprocess.run([sys.executable, PICO, "reset"], capture_output=True, timeout=30)
        return
    import glob, serial, threading
    os.makedirs(CAPTURE, exist_ok=True)
    port = glob.glob("/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00")[0]
    s = serial.Serial(port, 115200, timeout=0.5)
    s.write(b"\x02\x03\x03")
    time.sleep(0.3)
    s.write(b"\x04")
    _boots[0] += 1
    f = open(os.path.join(CAPTURE, "boot%d.log" % _boots[0]), "wb")
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            f.write(s.read(4096))
            f.flush()
        f.close()
        s.close()
    th = threading.Thread(target=pump, daemon=True)
    th.start()
    _cap.append((stop, th))


def end_capture():
    while _cap:
        stop, th = _cap.pop()
        stop.set()
        th.join(3)


if len(sys.argv) > 3 and sys.argv[3] == "guard":
    # G1: parked + the network side throws (bench flag NOKNOK_BENCH_PARK_NET_FAIL = 1 in
    #     settings.toml, set by this script through the REPL) -> no hello for 45 s, and the brain
    #     still runs safe_idle (the console shows "parked OFFLINE", code.py did not end).
    # G2: flag removed + `reset` (hard) -> hello answers "provisioned" again.
    res = {}
    FLAG = "NOKNOK_BENCH_PARK_NET_FAIL = 1"
    SET = ("import storage, microcontroller\n"
           "storage.remount('/', readonly=False)\n"
           "t = open('/settings.toml').read()\n"
           "open('/settings.toml', 'w').write(t.rstrip('\\n') + '\\n" + FLAG + "\\n')\n"
           "storage.remount('/', readonly=True)\n"
           "microcontroller.nvm[0] = 0xC3\n")
    CLR = ("import storage\n"
           "storage.remount('/', readonly=False)\n"
           "t = open('/settings.toml').read()\n"
           "open('/settings.toml', 'w').write(t.replace('" + FLAG + "\\n', ''))\n"
           "storage.remount('/', readonly=True)\n"
           "print('FLAG', '" + FLAG + "' in open('/settings.toml').read())\n")
    pico(SET)
    reset_soft()
    t, h = wait_hello(45)
    res["G1_offline_no_hello"] = h is None
    end_capture()
    res["flag_cleared"] = "FLAG False" in pico(CLR)
    pico("import microcontroller; microcontroller.reset()")           # hard reset = strikes cleared
    t, h = wait_hello(120, "provisioned")
    res["G2_reset_recovers"] = h is not None
    res["pass"] = res["G1_offline_no_hello"] and res["G2_reset_recovers"] and res["flag_cleared"]
    print(json.dumps(res), flush=True)
    sys.exit(0 if res["pass"] else 1)

ok = 0
for n in range(1, RUNS + 1):
    time.sleep(20)                       # let a freshly booted product settle before Ctrl-C
    set_out = ""
    for _ in range(3):                   # the REPL step can miss right after a boot: verify + retry
        set_out = pico("import microcontroller; microcontroller.nvm[0]=0xC3; "
                       "print('SET', hex(microcontroller.nvm[0]))")
        if "SET 0xc3" in set_out:
            break
        time.sleep(3)
    reset_soft()
    t, h = wait_hello(90, "parked")
    st = {}
    for _ in range(3 if h else 0):       # one status read can time out (DEV-187): retry
        st = rpc("status") or {}
        if st:
            break
        time.sleep(1)
    parked_ok = h is not None and st.get("strikes") == 3
    diag = None
    end_capture()                        # release the port before any pico.py call
    if h is None:        # no answer: leave a trace of the nvm bytes (strikes, parked-network marker)
        diag = pico("import microcontroller; print('nvm', hex(microcontroller.nvm[0]), hex(microcontroller.nvm[23]))").strip()[-60:]
    res = {"run": n, "parked_hello_s": round(t, 1) if t else None,
           "state": (h or {}).get("state"), "strikes": st.get("strikes"),
           "code": ((h or {}).get("versions") or {}).get("code")}
    t2 = None
    if h is not None:                    # parked per hello: always leave park, even if status failed
        rpc("reboot")
        time.sleep(5)
        t2, h2 = wait_hello(120, "provisioned")
        st2 = (rpc("status") or {}) if h2 else {}
        res["left_park_s"] = round(t2 + 5, 1) if t2 else None
        res["strikes_after"] = st2.get("strikes")
    passed = parked_ok and t2 is not None
    ok += passed
    res["pass"] = bool(passed)
    if diag:
        res["diag"] = diag
        res["set_out"] = set_out.strip()[-120:]
    print(json.dumps(res), flush=True)
print("bench_dev189_park: %d/%d PASS" % (ok, RUNS))
