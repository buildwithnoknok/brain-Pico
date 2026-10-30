# Debug Probe tools: catch a frozen brain (DEV-172 / DEV-173)

Tools for halting a frozen Pico 2 W over SWD with the Raspberry Pi Debug Probe: they record where
both cores are stuck, then reset the chip without unplugging anything. They were written for the
DEV-172 hunt on 30 Oct 2026; the findings are on the Jira ticket.

## Wiring (Raspberry Pi Debug Probe)

- Probe port **D** (SWD) to the Pico's 3-pin debug connector at the end opposite its micro-USB
  (SWCLK / GND / SWDIO). Use the JST-to-JST cable if the Pico has the white 3-pin connector. On bare
  holes, solder a 3-pin 2.54 mm header and use the jumper cable: orange = SWCLK, black = GND,
  yellow = SWDIO.
- Probe micro-USB to the bench Pi. Port **U** (UART) stays empty: CircuitPython prints nothing there.
- The probe does not power the Pico, and plugging it in does not stop or reset the brain.

## Bench Pi setup (Debian 13)

    sudo apt-get install -y openocd gdb-multiarch     # Raspberry Pi build: has target/rp2350.cfg

OpenOCD and usbmon need sudo. Either use passwordless sudo or export `BENCH_SUDO_PW` before running
the scripts. Never write the password into a file in this repo.

## Scripts

| Script | What it does |
|---|---|
| `hang_dump.sh [tag]` | At a freeze: 3 s usbmon of USB bus 1, then `hang_dump.tcl`. Output in `~/dev/dev172/dumps/`. |
| `hang_dump.tcl` | Halts BOTH cores 12 times (PC/LR/SP samples), then dumps registers, SCB fault registers, stack, TinyUSB state (`_usbd_dev`, `_cdcd_itf`, `hw_endpoints`), and the USB registers + DPRAM (twice, 1 s apart). Always releases both cores at the end. |
| `swd_reset.sh [tag]` | SWD `reset run`. Recovers a DEV-172 freeze; the brain boots like after a power-on. |
| `usbmon_dev.py FILE DEV...` | Per USB device number: console bulk-IN completions, whether data ever arrived, control requests. |
| `usbmon_tl.py FILE DEV [N]` | Compact timeline of one device from a usbmon text trace. |
| `dev173_probe_loop.py IP N [OUT]` | Ctrl-C into the running product N times, with the port re-opened before each Ctrl-C (pico.py style). On a silent console: dump + SWD reset. |
| `cdc_timeout_test.py [HOLD_S]` | Workaround-B check (stops the product): the host keeps the console open but stops reading; a `usb_cdc.console` write with a 0.2 s timeout must give up, while `print()` blocks until the host reads again. |
| `cp_build.sh` (WSL) | Builds CircuitPython 10.3.1 for `raspberry_pi_pico2_w` with Arm GCC 15.2.Rel1 (Adafruit CI's compiler) to get `firmware.elf`, and compares it with the official .uf2. |
| `cp_a2l.sh ADDR...` (WSL) | Turns addresses into function names from that build. |

## Traps (learned the hard way)

- OpenOCD links the two RP2350 cores (SMP). Halt and resume BOTH cores together; resuming one alone
  fails. Run `poll` before reading registers.
- A script that stops after `halt` leaves the brain halted, and OpenOCD does not resume on exit.
  Release by hand: `mww 0xE000EDF0 0xA05F0001` on each core (DHCSR, clears C_HALT).
- Inside a Tcl `catch {}`, `reg` and `mdw` return their text instead of printing it. Wrap them in `echo [...]`.
- Adafruit publishes no `.elf` for releases. The `cp_build.sh` build matches the official 10.3.1 image
  except for the build date, gzip timestamps and the **ulab** code block. Symbols are valid
  everywhere else, including the code copied to RAM, and data addresses match because the literal
  pools are identical.
- usbmon on an idle bus shows nothing: a stuck read just stays pending. Run it continuously
  (`sudo cat /sys/kernel/debug/usb/usbmon/1u > file &`) to capture the moment it stops.
- With Sue's reset loop (`bench_dev172_reset_loop.py`, branch dev-172), a "HANG" where the console
  still answers Ctrl-C is a lost /rpc command, not a freeze. Only "Ctrl-C answer 0 bytes" is real.
