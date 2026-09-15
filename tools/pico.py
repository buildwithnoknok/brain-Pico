#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# pico.py — drive a CircuitPython Pico from the Pi4 bench host over its USB serial
# REPL, so bench scripts can be run and files pushed without Thonny or a PC.
#
#   pico.py run  <local.py>           run a script on the Pico, stream its output
#   pico.py exec "<python>"           run a one-liner / snippet
#   pico.py put  <local> [<remote>]   copy a file onto the Pico's filesystem
#   pico.py ls   [<dir>]              list the Pico's filesystem
#   pico.py reset                     soft-reboot (Ctrl-D) so code.py runs again
#
# Uses CircuitPython's raw-REPL mode (the same protocol Thonny/mpremote use):
#   Ctrl-C  interrupt whatever is running (code.py)
#   Ctrl-A  enter raw REPL          -> device prints "raw REPL; CTRL-B to exit\r\n>"
#   <code> + Ctrl-D  execute        -> "OK", stdout, \x04, stderr, \x04, ">"
#   Ctrl-B  back to the friendly REPL
#
# Files are written THROUGH the REPL (device-side open/write), not via the
# CIRCUITPY mass-storage mount. Since DEV-18 boot.py hides the drive and leaves
# the filesystem read-only to the program, so `put` opens its own write window:
# remount rw -> write <name>.tmp -> rename over the target -> sync -> remount ro
# (same atomic pattern as noknok.write_atomic, and the old file survives a power
# cut mid-transfer). If the drive is visible (settings.toml NOKNOK_USB_DRIVE = 1)
# the remount is refused and the write fails — copy over the mount instead.
# REPL-side writes also don't trigger CircuitPython's auto-reload, so pushing a
# helper module won't reboot the board.
#
# Port: the by-id path is stable across re-enumeration; ttyACM numbering is not
# (the WCH-LinkE also exposes a CDC port and grabs ttyACM0 if it enumerates first).

import sys, time, glob, serial

PORT_GLOB = '/dev/serial/by-id/usb-Raspberry_Pi_Pico*-if00'
BAUD      = 115200

CTRL_A, CTRL_B, CTRL_C, CTRL_D = b'\x01', b'\x02', b'\x03', b'\x04'


def find_port():
    hits = glob.glob(PORT_GLOB)
    if not hits:
        sys.exit('no Pico serial port found (%s) — is it plugged into the Pi?' % PORT_GLOB)
    return hits[0]


class Pico:
    def __init__(self, port=None, timeout=1.0):
        self.ser = serial.Serial(port or find_port(), BAUD, timeout=timeout)

    # ── low level ──────────────────────────────────────────────────────────
    def _read_until(self, marker, timeout=10.0, echo=False):
        """Read until `marker` appears. Returns bytes before the marker."""
        buf = b''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            b = self.ser.read(1)
            if not b:
                continue
            buf += b
            if echo:
                sys.stdout.write(b.decode('utf-8', 'replace'))
                sys.stdout.flush()
            if buf.endswith(marker):
                return buf[:-len(marker)]
        raise TimeoutError('timed out waiting for %r; got %r' % (marker, buf[-200:]))

    def enter_raw(self):
        """Interrupt code.py and drop into raw REPL.

        Order matters: after Ctrl-C CircuitPython tears down code.py and prints
        its banner + '>>> ' — that takes a moment, and if Ctrl-A goes out before
        the prompt is back, the banner lands where the raw-REPL reply should be.
        So: Ctrl-B (in case a previous session left us in raw mode), Ctrl-C,
        wait for '>>> ', THEN Ctrl-A and wait for the raw-REPL banner."""
        self.ser.reset_input_buffer()
        self.ser.write(CTRL_B + CTRL_C + CTRL_C)
        try:
            self._read_until(b'>>> ', timeout=3.0)
        except TimeoutError:
            # code.py had already finished ("Code done running ... Press any
            # key to enter the REPL") — typical right after a reset. Ctrl-C is
            # not "a key" there; a carriage return is. Then the prompt appears.
            self.ser.write(b'\r')
            self._read_until(b'>>> ', timeout=8.0)
        self.ser.write(CTRL_A)
        self._read_until(b'raw REPL; CTRL-B to exit\r\n>', timeout=5.0)

    def exit_raw(self):
        self.ser.write(CTRL_B)
        time.sleep(0.1)

    def exec_raw(self, code, echo=True, timeout=120.0):
        """Execute `code` in raw REPL. Streams stdout if echo. Returns (stdout, stderr)."""
        if isinstance(code, str):
            code = code.encode('utf-8')
        self.ser.write(code + CTRL_D)
        self._read_until(b'OK', timeout=5.0)
        out = self._read_until(b'\x04', timeout=timeout, echo=echo)
        err = self._read_until(b'\x04', timeout=5.0)
        self._read_until(b'>', timeout=5.0)
        if err:
            sys.stdout.write(err.decode('utf-8', 'replace'))
            sys.stdout.flush()
        return out, err

    # ── commands ───────────────────────────────────────────────────────────
    def run(self, path):
        with open(path, 'rb') as f:
            code = f.read()
        self.enter_raw()
        try:
            out, err = self.exec_raw(code, echo=True)
        finally:
            self.exit_raw()
        return 1 if err else 0

    def exec(self, snippet):
        self.enter_raw()
        try:
            out, err = self.exec_raw(snippet, echo=True)
        finally:
            self.exit_raw()
        return 1 if err else 0

    def put(self, local, remote=None):
        remote = remote or local.split('/')[-1]
        with open(local, 'rb') as f:
            data = f.read()
        tmp = remote + '.tmp'
        self.enter_raw()
        try:
            # Open the write window (DEV-18). A refused remount (drive visible
            # to a PC) is reported by the open() below as a read-only error —
            # unless an old rw-remounting boot.py made the FS writable anyway,
            # in which case we must NOT flip it read-only afterwards (that
            # would break every following put until a hard reset): only undo
            # a remount we actually did.
            self.exec_raw("import os, storage\n"
                          "_rw = False\n"
                          "try:\n"
                          "    if storage.getmount('/').readonly:\n"
                          "        storage.remount('/', readonly=False); _rw = True\n"
                          "except Exception as e: print('remount:', e)\n", echo=True)
            _, err = self.exec_raw("f = open(%r, 'wb')" % tmp, echo=True)
            if err:
                return 1
            for i in range(0, len(data), 512):
                self.exec_raw("f.write(%r)" % data[i:i + 512], echo=False)
            _, err = self.exec_raw(
                "f.close(); os.sync(); os.rename(%r, %r); os.sync()\n"
                "print('wrote', %d, 'bytes to', %r)" % (tmp, remote, len(data), remote),
                echo=True)
        finally:
            self.exec_raw("try:\n"
                          "    if _rw: storage.remount('/', readonly=True)\n"
                          "except Exception: pass\n", echo=False)
            self.exit_raw()
        return 1 if err else 0

    def ls(self, d='/'):
        return self.exec("import os\nfor n in sorted(os.listdir(%r)): print(n)" % d)

    def reset(self):
        self.ser.write(CTRL_C + CTRL_C)
        time.sleep(0.2)
        self.ser.write(CTRL_D)       # soft reboot -> code.py runs again
        print('soft reboot sent')
        return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__ or 'usage: pico.py run|exec|put|ls|reset ...')
        return 2
    cmd, args = argv[1], argv[2:]
    p = Pico()
    if cmd == 'run':   return p.run(args[0])
    if cmd == 'exec':  return p.exec(args[0])
    if cmd == 'put':   return p.put(*args)
    if cmd == 'ls':    return p.ls(*args)
    if cmd == 'reset': return p.reset()
    print('unknown command', cmd)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
