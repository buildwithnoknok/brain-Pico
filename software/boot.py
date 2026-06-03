# boot.py — runs before code.py on every boot
# Only job: remount filesystem as writable so code.py can save wifi.json and product.py
# Without this, any file write will throw a ReadOnlyFilesystem error.

import storage
storage.remount("/", readonly=False)
