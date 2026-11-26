# DEV-298 bench: USB stage-1 update through the brain's daily pass

Bench only. `registry_test.json` = the Ecosystem registry 2.1.0 (branch `dev-237-v203-layout4`) with
`bootloader.stage1.CH32V203` pointed at Sam's TEST stage-1 2.0.1 index (module-USB-bootloader branch
`dev-237-v203-stage0-stage1`, `firmware/test/stage1_2.0.1/`). Never put it on a field brain.

`deploy.sh` (on a bench Pi, in a brain-Pico checkout of this branch): builds nothing itself; give it the three
`.mpy` files built with mpy-cross 10.3.1 from `software/` (`noknok_main` with `MODULE_REGISTRY_URL` replaced by the
raw URL of `registry_test.json`), puts them on the brain, clears the OTA timestamp (nvm[1:5]) so the next boot runs
the daily pass, and resets. `read_state.py` prints versions, the Store `usb` record and the last events.

Expected on a LEDs 16x with bootloader 1.x: the read happens during an app update (or one read visit), `bl` = null,
never a stage-1 visit afterwards. On a LEDs 16x with the layout-4 full image (stage-0 + 2.0.0): read visit (or
piggyback), then stage-1 visit -> `[USBFW] action=stage1`, `stage1_ok`, `flashed`, `verified`; `0xB1` = (1,2,0,1,4).
Restore: the main `.mpy` files, `nvm[1:5] = bytes(4)`; the module back to 2.0.0 with
`module-USB-bootloader/tools/usb_flash.py --stage1 firmware/bin/noknok_usb_stage1.bin` (Pi USB) or SWD.

## Run it (Pi4RFID, 26 Nov 2026 - what passed)
Put the branch `.mpy` files (`noknok_main` built with the registry URL above) on the brain with `pico.py put`, then:
1. `pico.py run clear_usb_record.py` if the module was re-flashed over SWD to the same app version.
2. `pico.py run force_due.py` -> wait ~2.5 min -> `pico.py run read_state.py`: `[BL] ... visit scheduled (version read)`,
   `[USBFW] action=read detail=bootloader 2.0.0`.
3. `force_due.py` again -> wait ~5 min -> `read_state.py`: `[BL] ... (stage-1 2.0.1)`, `action=stage1`, `stage1_ok`,
   `flashed`, `verified`; usb record `bl` = [1, 2, 0, 1, 4].
4. `force_due.py` a third time -> no new event (0 resets).
5. Put the main `.mpy` files back; the module goes back to 2.0.0 over SWD (the brain never downgrades).
