# auto_integration - Legacy V1 Framework (RTC Prototype)

An archived, proof-of-concept framework to *automatically integrate* the Real-Time Clock (RTC) peripheral IP into the *Shakti gc2025* SoC.

> **Legacy Warning.** This folder contains the **original V1 procedural prototype**. It relies on heavily hardcoded Python script logic and is strictly limited to integrating the RTC. It has been superseded by the modular, multi-peripheral V2 engine located in the repository root. This archive is maintained strictly for reference and isolated RTC testing.

> **One of three.** `archive/` now holds three V1 frameworks forked from this one: this RTC prototype, `auto_integ_legacy_v1_uart/` (UART3 on dedicated pins) and `auto_integ_legacy_v1_uart_pinmux/` (UART3 through the pinmux). They share the same engine and manager; they differ in `ip_bsv/`, their config, and their hw Makefile sync lists.

---

## What this folder is

`auto_integ_legacy_v1_rtc/` is the standalone **manager** that installs the V1 procedural automation framework into a fresh `gc2025` clone.

```text
auto_integ_legacy_v1_rtc/
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

## Cross-Framework Interoperability

`archive/` holds three V1 frameworks — this one, `auto_integ_legacy_v1_uart/` and
`auto_integ_legacy_v1_uart_pinmux/` — alongside the Main V2 engine at the
repository root. Because clones get reused across experiments, every manager's
`status` target is mutually aware of all the others.

When you execute `make status` from **this legacy Makefile**:

1. **Candidate scoring.** It computes a byte-for-byte match score for the clone against *this* framework, against Main V2 (`../../files`), and against every sibling legacy framework (`../*/files`). One `[CANDIDATE]` line is printed per framework, so you can see the margin rather than just the verdict.
2. **Closest-match selection.** The highest scorer wins. Because the sibling frameworks share most of their payload, ties are broken by checking which framework ships a `soc_build_config.yaml` matching the clone's active config.
3. **Targeted diffing.** The configuration check, deep integrity scan, and documentation check all run against the **winning** framework — not blindly against this directory. A clone injected by the UART manager is diffed against the UART payload, with its own `configs/` and `docs/`.
4. **Colour routing.** A match on this framework is `GREEN`; Main V2 or a sibling legacy framework is `YELLOW`, flagging that the clone belongs to a different manager.

Sibling frameworks are discovered by glob, so adding a fourth legacy framework to
`archive/` requires no edit to any `status` target.

The **Main V2 manager** implements the same logic from its own vantage point:
it scores V2 against every `archive/*/files`, and colours a V2 match `GREEN`
while any legacy match is a `YELLOW` deprecation warning.

---
