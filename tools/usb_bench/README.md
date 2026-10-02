# USB module bench scripts (Pico side)

Small helpers to exercise noknok **USB modules through the Pico's USB host** (PIO-USB), driven
from the bench host with `tools/pico.py`. Written on 29 Sep 2026 while testing the LEDs (8×)
firmware 1.8.2 OTA. They are the reproduction kit for **DEV-45** (the Pico misses a module that
re-enumerates during OTA and can wedge).

Copy `pico.py` and these files to the bench host, then run them ON THE PICO with
`./pico.py run <file>`. `noknok_usb.py` must be on the Pico.

| File | Runs on | What |
|---|---|---|
| `leds_probe.py` | Pico | `noknok_usb.discover()`: serial + firmware version of each USB LED module |
| `leds_bl_state.py` | Pico | Is a noknok USB module present as **app** (PID 4E4E) or **bootloader** (4E42)? |
| `usb_list.py` | Pico | Every USB device on the host port. ⚠ This is the call that **hung** the host in DEV-45 |
| `leds_ota.py` | Pico | OTA an app image through `UsbModuleFlasher` (app → 0xB0 → bootloader → flash → boot). Set `IMG`, `pico.py put` the image first |
| `leds_ota_bl.py` | Pico | Same, for a module that is already waiting in the bootloader |
| `leds_stress.py` | Pico | 300 back-to-back 8× `0x04` frames, then prints the frame the ring must show |
| `hard_reset.py` | Pico | `microcontroller.reset()`: recovers a stuck USB host port (a soft reset does not) |
| `listen.py` | bench host | Passively read the Pico console for N seconds (shows whether the Pico is wedged: 0 bytes) |

If the Pico is wedged (no console output, Ctrl-C ignored), even `hard_reset.py` can't run: unplug
the Pico's USB for ~3 s.

**Faster host:** the Pico's PIO-USB is slow (300 frames ≈ 0.2 s) and can hide receive-path bugs.
Stress-test USB modules from the bench host's native USB too: see the module repos'
`tools/` (e.g. `module-usb-led-16x/tools/bench/stress_leds16.py`), and flash from there with
`module-USB-bootloader/tools/usb_flash.py`.
