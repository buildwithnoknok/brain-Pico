# Changelog - brain-Pico (Pico 2 W brain software)

Newest first. One entry per version bump, written in the same commit as the bump. Format:
`## <version> - <date>` then `- DEV-xx - what changed and why. Breaking: yes/no.`
Convention: Confluence "Development Conventions" (SD space, page 120160257).

## noknok.py 1.12 - 2026-10-08

- DEV-65 - flash-layout gate inside the library. `Conductor.update_all()` now refuses (fail closed, nothing written) any I2C module whose bootloader layout is not exactly the manifest entry's `layout`; `firmware_report()` gains `layout_image`, `layout_installed`, `blocked` and optional `read_layout` / `strict_layout`. Before, only the provisioning path in `code.py` was gated; `tools/bench_update_modules.py` and any caller of `update_all()` could hang a layout-1 module with a layout-2 image. `tools/bench_update_modules.py` manifest now carries layouts and current versions; `update_demo.py` documents that it needs them. code.py unchanged (0.18). Breaking: `update_all()` callers whose manifest has no `layout` now get their I2C modules refused (pass `layout` or `check_layout=False`).

## Baseline - 2026-10-02

- Changelog starts here. Current: noknok.py 1.11 (__version__), code.py 0.18 (CODE_VERSION). Track the two versions separately in each entry. Earlier history: git log and the README.
