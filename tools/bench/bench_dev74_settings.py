# SPDX-License-Identifier: MIT
# Copyright (c) noknok
#
# bench_dev74_settings.py - DEV-74 bench: rewrite /settings.toml for one failure
# scenario, then reboot the brain (settings.toml is read at VM start) and read the
# [cfg]/[CFG] lines. MODE: "noB" (bus B lines removed), "nopins" (file without any
# pin key), "typo" (bus B SDA = GP99), "samepp" (bus A on GP4/GP5 = I2C0, the same peripheral as bus B on GP20/GP21), "restore" (the repo template, PicoHub pins).
import noknok as nk

MODE = "restore"
BASE = ('NOKNOK_I2C_SDA = "GP10"\nNOKNOK_I2C_SCL = "GP11"\n'
        'NOKNOK_I2C_B_SDA = "GP20"\nNOKNOK_I2C_B_SCL = "GP21"\n'
        'NOKNOK_I2C_FREQ = 100000\nNOKNOK_USB_DP = "GP0"\nNOKNOK_USB_DM = "GP1"\n'
        'NOKNOK_USB_DRIVE = 0\n')
TEXT = {
    "restore": BASE,
    "noB": BASE.replace('NOKNOK_I2C_B_SDA = "GP20"\nNOKNOK_I2C_B_SCL = "GP21"\n', ''),
    "nopins": 'NOKNOK_USB_DRIVE = 0\n',
    "samepp": BASE.replace('NOKNOK_I2C_SDA = "GP10"', 'NOKNOK_I2C_SDA = "GP4"')
                  .replace('NOKNOK_I2C_SCL = "GP11"', 'NOKNOK_I2C_SCL = "GP5"'),
    "typo": BASE.replace('NOKNOK_I2C_B_SDA = "GP20"', 'NOKNOK_I2C_B_SDA = "GP99"'),
}[MODE]
with nk.writable():
    nk.write_atomic("/settings.toml", TEXT)
print("settings.toml written, mode =", MODE)
