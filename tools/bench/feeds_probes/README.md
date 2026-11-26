<!--
SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Probe scripts for `noknok_feeds` (bench, 20 + 23 Nov 2026)

These ran ON a real brain from RAM (`pico.py run`, nothing flashed) next to a monitor on the Pi. Results: architecture repo `flows/data-feeds.md` sections 2b and 2c (private).
**Reserve the rig first (`~/BENCH_IN_USE`), clean up with `python3 pico.py reset` (never `supervisor.reload()`), see `~/.claude/tools.md` "Rig lessons" (Pico RAM probes).**

| File | What it does |
| --- | --- |
| `wifi_head.py` | helpers appended after the library source: `_lfb()` largest free block, `_join()` reconnect from the brain's own `/data/wifi.json` (prints nothing), `stamp()` |
| `probeA_tail.py` | A: app channel stall: starts the brain's `/rpc` server and a product-like loop with 1-3 feeds (edit the `feeds.add(...)` lines and the duration) while `monitor.py` pings `/rpc` from the Pi |
| `probeD_tail.py` | D: Wi-Fi lost mid-run (radio off 40 s, rejoin, recovery time, optional session reset) |
| `soak_tail.py` | 60 min soak: one server, 30 s refresh, app server up |
| `monitor.py` | on the Pi: `python3 monitor.py <brain ip> <seconds> <interval s>` prints `MON <epoch> <latency> ok/ERR` |
| `run_probe.sh` | on the Pi: `bash run_probe.sh <name> <seconds>` runs monitor + `pico.py run` of `zz_probe_<name>.py` and prints stall windows |

Build a probe file = the library source + a head + a tail, then copy it to the Pi's `~/dev/pico/zz_probe_<name>.py`:

```
cp <brain-Pico clone>/software/noknok_feeds.py nf.py
cat nf.py wifi_head.py probeA_tail.py > zz_probe_A.py
```

Find the brain with `python3 tools/bench/diag/find_brain.py <part of the DEVICE ID>` (the device id, e.g. a324, not "noknok").
