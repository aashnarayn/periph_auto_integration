# Reference copies — NOT build inputs

Upstream `devices/watchdog/` sources, copied here on 2026-09-23 from
`gitlab.com/shaktiproject/uncore/devices` @ `origin/master`
(`60d9dee9090f0015ccd14de47dd09277c3d608a7`), for reading while authoring or
reviewing `watchdog_v1.yaml`.

**This directory is deliberately not on any `bsvpath`.** `watchdog_v1` integrates
the watchdog by adding `devices/watchdog/` to the clone's `bsvpath` — it does
**not** ship RTL. Putting `watchdog.bsv` anywhere that *is* on the bsvpath would
put two `package watchdog` definitions on the search path.

Do not add this directory to `bsvpath`, and do not edit these files — they are a
snapshot for reference only. The build reads `devices/watchdog/` in the clone.

| File | What it is |
|---|---|
| `watchdog.bsv` | The IP: `mkwatchdog`, `mkwatchdog_axi4lite`, `mkwatchdog_axi4` |
| `watchdog_template.bsv` | Upstream's own standalone synthesis example (`mkdummy`) |
| `watchdog_driver.h` / `.c` | Upstream driver — note its register offsets are relative to that driver's own base, not to `WatchDog0Base` |

Verified on copy: the RTL at `origin/master` is **byte-identical** to the commit
pinned in `gc2025/hw/devices` (`d5dda6d`). No upstream drift to absorb.
