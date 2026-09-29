# auto_integration - Framework Setup Manager

A framework to *automatically integrate* peripheral IP into an SoC by RTL manipulation and file handling/code injection. Uses *Python* and *YAML* to automatically read, modify and restore bsv and verilog RTL in the *Shakti gc2025* framework.

> **Tentative doc.** This describes the *setup* layer (this folder and its
> Makefile). It is expected to keep evolving as the setup flow is optimised;
> maintain it alongside changes here. The *integration engine* it ships is
> documented in `docs/README_AUTOMATION.md` (starter) and
> `docs/UserManual.md` (full reference).

---

## What this folder is

`auto_integration/` is **not** the SoC and **not** the engine — it is a thin
**manager** that installs the peripheral-auto-integration framework into a fresh
`gc2025` clone. The idea: ship only the small set of framework files and inject
them into a full SoC checkout on demand, instead of zipping multi-GB trees.

```text
periph_auto_integration/
├── Makefile          # the manager: clone, inject, status, remove, delete
├── configs/          # ready-to-use soc_build_config variants (config1..4)
├── docs/             # README_AUTOMATION.md + UserManual.md (deployed into the clone)
├── archive/          # the three V1 legacy frameworks (see below)
└── files/            # the payload that gets injected into a clone's hw/
    ├── Makefile              # the automation-enabled hw/ Makefile
    ├── master_constraints.xdc
    ├── scripts/             # the engine + per-board bootstrap seeds
    ├── ip_bsv/              # the IP definition recipes
    └── templates/           # per-peripheral config templates (copy into a config)

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
| `status` | (E) report whether a clone exists and is injected, score it against V2 **and each legacy framework**, name the closest match, then diff config, payload and docs against *that* framework. |
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

---

## The four shipped configs

| Config | Peripherals |
| --- | --- |
| `soc_build_config1.yaml` | rtc, uart, gpio(48), i2c, gptimer, watchdog, sspi, pwm — the full set |
| `soc_build_config2.yaml` | rtc(0 io + 1 io/no-plic), uart3(no-pinmux), gpio(36), i2c2, gptimer4, wd0 |
| `soc_build_config3.yaml` | uart3, gpio(48), i2c2+i2c3, sspi2+sspi3, gptimer4+gptimer5 |
| `soc_build_config4.yaml` | rtc(no_io), uart3(pinmux)+uart4(pins), i2c2, sspi2+3, gpio(48), wd0 |

Each is a working, compile-verified example. Copy one, or build your own from
`files/templates/`.

---

## Adding a board

The engine is board-agnostic; only two things are board-specific:

1. A bootstrap seed at `files/scripts/bootstrap/<board>/bootstrap.yaml` (the anchors
a fresh clone of that board lacks). The engine discovers it automatically from
the config's `target_board`.
2. `files/master_constraints.xdc` / the board's pin map for XDC assignment.

No engine or config-schema change is needed to add a board.

---

## Legacy Frameworks (V1)

Three archived **V1 procedural prototypes** live under `archive/`. They are the
proof-of-concept ancestors of the V2 engine: each relies on heavily hardcoded
Python logic and integrates exactly **one** peripheral in **one** routing mode.

| Directory | Integrates | Routing |
| --- | --- | --- |
| `archive/auto_integ_legacy_v1_rtc/` | RTC | Dedicated pins (PMOD JA) |
| `archive/auto_integ_legacy_v1_uart/` | UART3 | Dedicated pins — patches `fpga_top.v`, `constraints.xdc`, `TbSoc.bsv` |
| `archive/auto_integ_legacy_v1_uart_pinmux/` | UART3 | Through the pinmux — patches `pinmux.bsv`, no Verilog/XDC changes |

> **Renamed 2026-08-24.** `auto_integ_legacy_v1_uart_pinmux/` was previously
> called `auto_integ_legacy_v1_uart_nopinmux/`. The two UART directory names had
> been the **inverse of their contents** — the one named `_uart` held the
> *without*-pinmux def while `_nopinmux` held the *with*-pinmux def. `_uart` was
> already correct and kept its name; only the other moved. If you have an older
> clone, script or note referring to `_uart_nopinmux`, it means this directory.

The two UART frameworks are forks of the RTC one and share their entire
`scripts/` directory; they differ in `ip_bsv/`, their `configs/`, and their hw
Makefile sync lists. V2 supersedes all three: it folds both UART routing modes
into a single `uart_v1` def selected per-instance by `uart_mode`, and composes
eight peripherals in one run.

### V1 Structure

```text
auto_integ_legacy_v1_<variant>/
├── Makefile          # The legacy manager
├── configs/
│   └── soc_build_config.yaml  # Single static config
└── files/
    ├── ip_bsv/       # The one IP def this variant integrates
    ├── scripts/      # V1 procedural python scripts
    ├── Makefile      # Legacy hw/ root Makefile
    ├── master_constraints.xdc
    └── docs/         # Variant-specific README_AUTOMATION.md
```

### V1 Usage

Each legacy manager shares the V2 interface but has its own default clone name
and no `CONFIG_SELECT` (there is only one config per variant).

| Framework | Default `CLONE_NAME` |
| --- | --- |
| `auto_integ_legacy_v1_rtc` | `gc2025_autortc` |
| `auto_integ_legacy_v1_uart` | `gc2025_autouart` |
| `auto_integ_legacy_v1_uart_pinmux` | `gc2025_autouart_pinmux` |

```bash
cd archive/auto_integ_legacy_v1_uart
make setup_base SETUP_PATH=../../workspace CLONE_NAME=gc2025_autouart
```

`setup_full`, `setup_base`, `remove_automation`, `status`, `delete_clone` and
`update_framework` all behave as in V2, against the V1 payload.

### Cross-Framework Interoperability

Because clones get reused across experiments, the `status` targets in **all
four** managers (V2 plus the three legacy ones) are mutually aware. Running
`make status` from this root Makefile:

1. **Candidate scoring.** It computes a byte-for-byte match score for the clone against the V2 payload (`files/`) and against every archived legacy framework (`archive/*/files`), printing one `[CANDIDATE]` line per framework so you see the margin, not just the verdict.
2. **Closest-match selection.** The highest scorer wins — so a clone injected by the pinmux UART manager is recognised specifically as *that* framework, not merely as "some legacy V1".
3. **Tie-break.** The legacy frameworks share most of their payload, so ties are resolved by checking which framework ships a `soc_build_config.yaml` matching the clone's active config.
4. **Targeted diffing.** The configuration check, deep integrity scan, and documentation check then all run against the **winning** framework, using its own `configs/` and `docs/` as the reference.
5. **Colour routing.** A V2 match is `GREEN`; any legacy match is a `YELLOW` deprecation warning.

Legacy frameworks are discovered by glob, so adding a fourth one to `archive/`
needs no edit to any `status` target.

Each legacy manager implements the same logic inverted for its own vantage
point: it scores itself, V2 (`../../files`), and its siblings (`../*/files`),
colouring a match on *itself* `GREEN` and V2 or a sibling `YELLOW`.

---
