# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
#
# safemode.py — noknok brain, DEV-154.
#
# CircuitPython runs this file (instead of boot.py / code.py) when it starts in SAFE MODE
# after its own core crashed: hard fault, stack overflow, brownout, ... In safe mode
# code.py never runs, so a shipped lamp would stay dark until someone replugs it, and
# neither the 3-strike crash recovery (code.py) nor a watchdog (ADR-006) can help.
#
# What it does: restart the brain, at most SM_MAX times in a row. After that it stays in
# safe mode on purpose (USB console + drive reachable for recovery, DEV-38) - no reboot loop.
# Entering safe mode deliberately (buttons during boot) never runs this file: CircuitPython
# skips safemode.py for SAFE_MODE_USER.
#
# code.py records each restart as a [SAFE] event (the app sees it in `status`) and sets the
# count back to 0 after a real power-on (replug) or after 10 min of the product running.
#
# nvm layout: [0] crash count, [1:5] last update check, [5] post-update marker,
#             [6] safe-mode restart count (0xA0 | n), [7:23] last reason (ASCII), [64:] Store.
# One nvm write per restart (one flash sector erase), nothing on the filesystem.

import microcontroller
import supervisor

SM_NVM_INDEX = 6
SM_MAGIC = 0xA0
SM_MAX = 2
SM_REASON_LEN = 16

try:
    b = microcontroller.nvm[SM_NVM_INDEX]
    n = (b & 0x0F) if (b & 0xF0) == SM_MAGIC else 0
    reason = str(supervisor.runtime.safe_mode_reason).split(".")[-1]
    if n < SM_MAX:
        r = reason.encode()[:SM_REASON_LEN]
        microcontroller.nvm[SM_NVM_INDEX:SM_NVM_INDEX + 1 + SM_REASON_LEN] = \
            bytes([SM_MAGIC | (n + 1)]) + r + bytes(SM_REASON_LEN - len(r))
        print("noknok safemode.py: safe mode (%s) - restart %d of %d" % (reason, n + 1, SM_MAX))
        microcontroller.reset()
    print("noknok safemode.py: safe mode (%s) again after %d restarts - staying in safe mode "
          "(replug the brain to try again)" % (reason, n))
except Exception as e:                       # never let this file make things worse
    print("noknok safemode.py: skipped (%r)" % (e,))
