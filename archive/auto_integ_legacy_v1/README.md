# auto_integration - Legacy V1 Framework (RTC Prototype)

An archived, proof-of-concept framework to *automatically integrate* the Real-Time Clock (RTC) peripheral IP into the *Shakti gc2025* SoC.

> **Legacy Warning.** This folder contains the **V1 procedural prototype**. It relies on heavily hardcoded Python script logic and is strictly limited to integrating the RTC. It has been superseded by the modular, multi-peripheral V2 engine located in the repository root. This archive is maintained strictly for reference and isolated RTC testing.

---

## What this folder is

`auto_integ_legacy_v1/` is the standalone **manager** that installs the V1 procedural automation framework into a fresh `gc2025` clone.

```text
auto_integ_legacy_v1/
├── Makefile          # the legacy manager: clone, inject, status, remove, delete
├── configs/          
│   └── soc_build_config.yaml  # Single static config (RTC only)
└── files/            # the V1 payload that gets injected into a clone's hw/
    ├── Makefile              # the legacy automation-enabled hw/ Makefile
    ├── master_constraints.xdc
    ├── scripts/             # V1 procedural scripts (peripheral_auto_integrator.py, etc.)
    └── ip_bsv/              # IP definition recipes (common/ and rtc_v1/ only)
    └── docs/               # README_AUTOMATION.md

```

---

## Usage

The legacy environment manager shares the same interface as V2, but uses different default variables tailored for isolated RTC testing.

```bash
# Setup the legacy RTC-only environment into a fresh clone:
make setup_base SETUP_PATH=../../workspace CLONE_NAME=gc2025_autortc
# then, inside the clone:
cd ../../workspace/gc2025_autortc/hw && make BOARD=nexys_video build_automated

```

### Arguments

| Variable | Default | Meaning |
| --- | --- | --- |
| `SETUP_PATH` | `$(HOME)/Desktop/shakti-dev/workspace3` | Where to create/find the clone. |
| `CLONE_NAME` | `gc2025_autortc` | Clone directory name → `$(SETUP_PATH)/$(CLONE_NAME)`. |
| `BOARD` | `nexys_video` | Target board. |
| `FORCE` | `0` | `1` = reuse/overwrite an existing clone (skips `git clone`). |

*(Note: There is no `CONFIG_SELECT` argument in V1, as it directly targets the single `soc_build_config.yaml`.)*

### Targets

| Target | Does |
| --- | --- |
| `setup_full` | (A) clone → inject V1 framework → deploy RTC config → track pin map → `run_setup_build`. |
| `setup_base` | (B) same as `setup_full` **without** `run_setup_build`. |
| `remove_automation` | (C) scrub the V1 framework from a clone; restore its original Makefile. |
| `delete_clone` | (D) delete the clone (warns first). |
| `status` | (E) report whether a clone exists, is injected, the framework (current v2 or legacy v1) it resembles, and a diff of all files in that framework. |
| `update_framework` | (F) push the latest `files/` into an existing clone without re-cloning. |

---

## Testing a local clone without GitLab

To install this legacy framework into a **local** SoC you already have, point at it and pass `FORCE=1` so the clone step is skipped:

```bash
make setup_base SETUP_PATH=/path/to CLONE_NAME=existing_soc FORCE=1

```

---

## The Shipped Configuration

Unlike the V2 framework which utilizes dynamic templates and supports multiple configuration profiles, the V1 framework ships with exactly one static configuration file:

| Config | Peripherals |
| --- | --- |
| `soc_build_config.yaml` | rtc (hardcoded integration footprint) |

This file is a working, compile-verified example of the RTC IP bound to the `nexys_video` constraints.

---

## Architecture Limitations

Because this is a procedural prototype, the engine logic is tightly coupled to its payload:

1. **No Modular Peripherals:** The Python scripts (`peripheral_auto_integrator.py` and `track_pin_map.py`) are hardcoded to search for, instantiate, and route the `rtc_v1` IP. They cannot dynamically parse new YAML definitions.
2. **Limited Board Support:** While the Makefile accepts a `BOARD` argument for directory routing, the underlying scripts were built assuming a `nexys_video` target footprint.

---

## Main Framework (V2) Interoperability

This legacy archive exists as a sub-directory within the greater `auto_integration` repository. Because users may frequently switch between testing the V1 prototype and the production V2 engine on the same hardware clones, the diagnostic `status` targets are mathematically cross-aware.

When you execute `make status` from **this legacy Makefile**:

1. **Resemblance Checker:** It calculates a byte-for-byte footprint of the target clone to determine if it is running this Legacy V1 framework or the Main V2 framework (located at `../../files`).
2. **Cross-Validation:** If the clone was injected using the Main V2 manager, this legacy `status` target will successfully detect it, flag the resemblance as `MAIN V2`, and perform a deep integrity scan against the V2 payload directories.
3. **Color Routing:** To maintain spatial awareness, this legacy Makefile flags a V1 match as a `GREEN` success (since it is local to this directory), and a V2 match as a `YELLOW` warning (indicating the clone is running the external, newer architecture).

The Makefile in the **Main Framework root** has the exact same cross-awareness functionality, but logically inverted (V2 is `GREEN`, Legacy V1 is a `YELLOW` warning).

---