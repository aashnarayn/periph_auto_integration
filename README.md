# auto_integration - Framework Setup Manager

A framework to *automatically integrate* peripheral IP into an SoC by RTL manipulation and file handling/code injection. Uses *Python* and *YAML* to automatically read, modify and restore bsv and verilog RTL in the *Shakti gc2025* framework.

> **Tentative doc.** This describes the *setup* layer (this folder and its
> Makefile). It is expected to keep evolving as the setup flow is optimised;
> maintain it alongside changes here. The *integration engine* it ships is
> documented in `files/docs/README_AUTOMATION.md` (starter) and
> `files/docs/UserManual.md` (full reference).

---

## What this folder is

`auto_integration/` is **not** the SoC and **not** the engine — it is a thin
**manager** that installs the peripheral-auto-integration framework into a fresh
`gc2025` clone. The idea: ship only the small set of framework files and inject
them into a full SoC checkout on demand, instead of zipping multi-GB trees.

```text
auto_integration/
├── Makefile          # the manager: clone, inject, status, remove, delete
├── configs/          # ready-to-use soc_build_config variants (config1..4)
├── templates/        # per-peripheral config templates (copy into a config)
└── files/            # the payload that gets injected into a clone's hw/
    ├── Makefile              # the automation-enabled hw/ Makefile
    ├── master_constraints.xdc
    ├── scripts/             # the engine + per-board bootstrap seeds
    ├── ip_bsv/              # the IP definition recipes
    └── docs/               # README_AUTOMATION.md + UserManual.md

```

---

## Usage

```bash
# Install the framework + config N into a fresh clone (clones gc2025 from GitLab):
make setup_base   CLONE_NAME=my_soc CONFIG_SELECT=soc_build_config1.yaml BOARD=nexys_video
# then, inside the clone:
cd ../my_soc/hw && make BOARD=nexys_video build_automated

```

### Arguments

| Variable | Default | Meaning |
| --- | --- | --- |
| `SETUP_PATH` | `..` | Where to create/find the clone (parent dir). |
| `CLONE_NAME` | `gc2025_autop` | Clone directory name → `$(SETUP_PATH)/$(CLONE_NAME)`. |
| `CONFIG_SELECT` | `default_config.yaml` | Which `configs/*.yaml` to deploy. |
| `BOARD` | `nexys_video` | Target board (drives the SoC config + sync). |
| `FORCE` | `0` | `1` = reuse/overwrite an existing clone (skips `git clone`). |

### Targets

| Target | Does |
| --- | --- |
| `setup_full` | (A) clone → inject framework → deploy config → track pin map → `run_setup_build`. |
| `setup_base` | (B) same as `setup_full` **without** `run_setup_build` (no pip/repomanager). |
| `remove_automation` | (C) scrub the framework from a clone; restore its original Makefile. |
| `delete_clone` | (D) delete the clone (warns first). |
| `status` | (E) report whether a clone exists, is injected, the framework (current v2 or legacy v1) it resembles and a diff of al the files in that framework and whether there are any modifications. |
| `update_framework` | (F) push the latest `files/` into an existing clone without re-cloning. |

`_setup_core` steps: `git clone` (or skip if `FORCE=1`) → back up `hw/Makefile` to
`Makefile.orig` and inject `files/Makefile` → copy `files/scripts` + `files/ip_bsv`
→ copy `docs/*` → deploy the chosen `configs/*.yaml` as `hw/soc_build_config.yaml`
→ copy `files/master_constraints.xdc` → `track_pin_map`.

---

## Testing a local clone without GitLab

`setup_full`/`setup_base` `git clone` from GitLab. To install into a **local**
SoC you already have, point at it and pass `FORCE=1` so the clone step is skipped:

```bash
make setup_base SETUP_PATH=/path/to CLONE_NAME=existing_soc FORCE=1 \
     CONFIG_SELECT=soc_build_config1.yaml BOARD=nexys_video

```

Or use `files/scripts/port_to_fresh_clone.sh <clone>/hw` for a scriptable inject.

---

## The four shipped configs

| Config | Peripherals |
| --- | --- |
| `soc_build_config1.yaml` | rtc, uart, gpio(48), i2c, gptimer, watchdog, sspi, pwm — the full set |
| `soc_build_config2.yaml` | rtc(0 io + 1 io/no-plic), uart3(no-pinmux), gpio(36), i2c2, gptimer4, wd0 |
| `soc_build_config3.yaml` | uart3, gpio(48), i2c2+i2c3, sspi2+sspi3, gptimer4+gptimer5 |
| `soc_build_config4.yaml` | rtc(no_io), uart3(pinmux)+uart4(pins), i2c2, sspi2+3, gpio(48), wd0 |

Each is a working, compile-verified example. Copy one, or build your own from
`templates/`.

---

## Adding a board

The engine is board-agnostic; only two things are board-specific:

1. A bootstrap seed at `files/scripts/bootstrap/<board>/bootstrap.yaml` (the anchors
a fresh clone of that board lacks). The engine discovers it automatically from
the config's `target_board`.
2. `files/master_constraints.xdc` / the board's pin map for XDC assignment.

No engine or config-schema change is needed to add a board.

---

## Legacy Framework (V1: RTC Only)

An older snapshot of the integration engine is maintained in the `archive/auto_integ_legacy_v1` directory. This is the **V1 procedural prototype** that serves as a proof-of-concept.

Unlike the modular V2 engine, which was exoanded for multi-peripheral, multi-instance support, V1 relies on heavily hardcoded script logic and is strictly limited to integrating the **RTC (Real-Time Clock)** peripheral.

### V1 Structure

```text
auto_integ_legacy_v1/
├── Makefile          # The legacy manager
├── configs/          
│   └── soc_build_config.yaml  # Single static config (RTC only)
└── files/            
    ├── ip_bsv/       # Contains only common/ and rtc_v1/
    ├── scripts/      # V1 procedural python scripts
    ├── Makefile      # Legacy hw/ root Makefile
    └── master_constraints.xdc

```

### V1 Usage

The legacy environment manager shares the same interface as V2, but uses different default variables tailored for isolated RTC testing:

* **Default Clone Name:** `gc2025_autortc`
* **Default Config:** Directly targets `soc_build_config.yaml` (no multi-config selection).

```bash
# Navigate to the legacy archive
cd archive/auto_integ_legacy_v1

# Setup the legacy RTC-only environment
make setup_base SETUP_PATH=../../workspace CLONE_NAME=gc2025_autortc

```

The targets `setup_full`, `setup_base`, `remove_automation`, `status`, `delete_clone`, and `update_framework` function identically to the V2 manager, but interact predominantly with the V1 engine payload.

The Makefile in this root can interact with the legacy framework using the `status` target to determine if an existing clone has been injected with the current framework (v2) or the legacy framework (v1). The Makefile in the legacy framework root has the same functionality.

### Cross-Framework Interoperability

Because the legacy prototype is archived within this repository (`archive/auto_integ_legacy_v1`), developers may occasionally interact with hardware clones deployed using the older standards. To prevent architectural confusion, the diagnostic `status` targets in both the root Makefile and the legacy Makefile are mathematically cross-aware.

When you execute `make status` from **this Main Makefile**:

1. **Resemblance Checker:** It calculates a byte-for-byte footprint of the target clone to determine if it is running the current Main V2 framework or the archived Legacy V1 framework (located at `archive/auto_integ_legacy_v1/files`).
2. **Cross-Validation:** If the clone was injected using the Legacy V1 manager, this main `status` target will successfully detect it, flag the resemblance as `LEGACY V1`, and perform a deep integrity scan against the V1 legacy payload directories.
3. **Color Routing:** To enforce deprecation awareness, this main Makefile flags a V2 match as a `GREEN` success (since it is the current production standard), and a V1 match as a `YELLOW` warning (indicating the clone is structurally sound but running a deprecated architecture).

The Makefile in the **Legacy Framework archive** has the exact same cross-awareness functionality, but logically inverted (local V1 is `GREEN`, external V2 is a `YELLOW` warning).


---