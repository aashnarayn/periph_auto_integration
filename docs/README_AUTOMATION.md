# Automated Peripheral Integration
## Shakti GC2025 — YAML-Driven SoC Peripheral Integration

## Goal
End-to-end peripheral integration driven by YAML alone — zero manual edits to
BSV/Verilog/XDC, and zero Python changes to add a new peripheral.

> **This file is the quick reference.** The full documentation — patch schema,
> handler types, the indentation system, the independence rules, and a worked
> section per integrated peripheral — lives in **`UserManual.md`**.

---

## Layout
```
gc2025/hw/
├── Makefile                     # Build system + automation targets
├── bsvpath                      # BSV include paths
├── soc_build_config.yaml        # Centralized Bill of Materials (BOM)
├── UserManual.md                # Full documentation
├── scripts/
│   ├── peripheral_auto_integrator.py  # The engine (VFS, handlers, backup/restore)
│   ├── utils.py                       # Logging, templates, IndentationAnalyzer
│   ├── indent_tools.py                # normalize / cleanup / audit
│   ├── xdc_analyzer.py                # Pin map + XDC conflict resolution
│   └── peripheral_registry.py         # Optional per-IP context generators
├── boards/
│   └── nexys_video/             # Board sources (patched by automation)
│       ├── Soc.defines
│       ├── Soc.bsv
│       ├── mixed_cluster.bsv
│       ├── TbSoc.bsv
│       ├── fpga_top.v
│       ├── constraints.xdc
│       └── pin_map.yaml
├── ip_bsv/                      # One directory per IP definition
│   ├── rtc_v1/                  # RTC0 + RTC1  (new RTL -> ships a .bsv)
│   │   ├── rtc_v1.yaml
│   │   ├── rtc_v1.bsv
│   │   └── rtc.defines
│   ├── uart_v1/                 # UART3 through the pinmux
│   ├── uart_v1/        # UART3 on dedicated pins   (YAML only)
│   ├── gpio_v1/                 # GPIO 32 -> 48             (YAML only)
│   ├── i2c_v1/                  # I2C2 + I2C3               (YAML only)
│   ├── sspi_v1/        # SPI2 + SPI3               (YAML only)
│   ├── pwm_v1/         # PWM6..PWM9                (YAML only)
│   ├── gptimer_v1/     # GPTimer4                  (YAML only)
│   └── watchdog_v1/    # wd0 watchdog              (YAML only)
├── .automation_backup/          # Pristine baseline + manifest.json
└── README_AUTOMATION.md         # This file
```

An IP directory ships a `.bsv` **only when it introduces new RTL**. Peripherals
that reuse an IP already under `devices/` (gptimer, pwm, i2c, sspi, gpio, uart)
need nothing but their YAML — the def instantiates existing modules.

### Two independent version numbers — `ip_bsv/*_v1` vs `devices/*_v2`
These are **different axes and are meant to differ**:

| Name | What it versions | Set by |
|------|------------------|--------|
| `ip_bsv/<name>_v1/` | The **automation def** — the YAML recipe that wires the peripheral in. Every def here is a *first* integration recipe, so all are `_v1`. | This framework |
| `devices/<name>_v2/` on `bsvpath` | The **RTL** the def instantiates (`mkpwm`, `mkspi`, …). The base SoC already shipped these at `_v2`/`_v3`. | Upstream Shakti RTL |

A def never hardcodes a device path; it instantiates modules that resolve through
`bsvpath`, so `pwm_v1` (def) legitimately drives `devices/pwm_v2/` (RTL). Renaming
`ip_bsv` to match the RTL suffix would be incorrect — the def revision and the RTL
revision have separate histories. To retarget a different RTL version, change the
`bsvpath` entry, not the def-folder name.

---

## How Automation Works

```
1. Patches applied     -> boards/<board>/*        (paths from each IP yaml)
       |
2. make sync_bsv_patches -> copies patched board files up to hw/ root
       |
3. make generate_verilog -> compiles patched BSV -> Verilog
       |
4. make board_build      -> Vivado synthesis -> bitstream
```

### Key design decisions
| Component | Strategy | Why |
|-----------|----------|-----|
| **`board_dir` in `soc_build_config.yaml`** | Single source of truth | No CLI flags; file paths in IP yamls template on `{board_dir}` |
| **Shared resources** | `{allocated_slot}` + width-agnostic `plic_vector` | Slave numbers and PLIC width are allocated at apply time, never hardcoded — peripherals stay order-independent |
| **Idempotency** | `skip_if_contains` / `skip_if_matches` on every patch | Safe to re-run; no duplicate injections |
| **Rollback** | `.automation_backup/manifest.json` | One `--restore` rewinds board sources *and* the hw/ root copies |
| **Include paths** | Patch `bsvpath`, never `makefile.inc` | `soc_config` regenerates `makefile.inc`, building `BSVINCDIR` from `".:%/Libraries:" + top_dir` plus every line of `bsvpath`. `quick_build_automated` runs `update_bsvpath` before `run_setup_build`, so a `bsvpath` entry propagates automatically |

---

## Quick Start

```bash
cd gc2025/hw

# 0. Prerequisites
python3 -c "import yaml; print('[INFO] PyYAML ready')"
which bsc || echo "[WARN] bsc not in PATH (BSV compile check will be skipped)"

# 1. Pre-automation validation — every anchor must exist in a pristine tree
make pre_validate_automation

# 2. Dry run (SAFE — no files modified)
make run_autointeg_complete_dry

# 3. Apply all phases (BSV + Verilog + XDC) and re-export the pin map
make run_autointeg_complete

# 4. Post-automation verification
make post_validate_automation

# 5. Copy patched board files to the hw/ root so the compiler sees them
make sync_bsv_patches

# 6. Compile BSV -> Verilog (the gold-standard correctness check)
make generate_verilog

# If anything goes wrong, rewind to the pristine baseline
make restore_autointeg_patches
```

---

## Makefile Targets

| Target | Purpose |
|--------|---------|
| `pre_validate_automation` | Verify every anchor exists before patching |
| `post_validate_automation` | Verify every patch landed after patching |
| `run_autointeg_complete_dry` | Preview all phases; writes nothing |
| `run_autointeg_complete` | Apply all phases (BSV + Verilog + XDC) |
| `run_autointeg_bsv` / `..._dry` | Phase 1 only: BSV + `bsvpath` |
| `run_autointeg_verilog` / `..._dry` | Phase 2 only: `fpga_top.v` |
| `run_autointeg_xdc` / `..._dry` | Phase 3 only: `constraints.xdc` |
| `update_bsvpath` | Register IP include paths only |
| `sync_bsv_patches` | Copy patched board BSV to the hw/ root |
| `sync_automated_files` | Copy patched BSV + XDC + `fpga_top.v` to the hw/ root |
| `track_pin_map` | Re-export `pin_map.yaml` from the current XDC |
| `restore_autointeg_patches` | Rewind to the pristine baseline (keeps the baseline) |
| `reset_autointeg_baseline` | Forget the baseline; the next run re-captures one |
| `quick_build_automated` | Full flow: validate -> automate -> build |

### Verbose / dry-run
The per-phase `_dry` targets pass `--dry-run --verbose`; `run_autointeg_complete_dry`
passes `--dry-run` only. To run any phase verbosely by hand, call the engine directly:

```bash
python3 scripts/peripheral_auto_integrator.py --config soc_build_config.yaml --verbose
python3 scripts/peripheral_auto_integrator.py --config soc_build_config.yaml --pre-audit --verbose
```

### Exit codes
| Code | Meaning |
|------|---------|
| `0` | All checks passed |
| `1` | One or more checks failed |
| `2` | Invalid arguments, missing config, or missing target files |

---

## Changing Boards

1. Point the config at the new board:
   ```yaml
   target_board: arty_a7_ganga
   board_dir: boards/arty_a7_ganga
   pin_map_path: boards/arty_a7_ganga/pin_map.yaml
   ```
2. Build with `make BOARD=arty_a7_ganga quick_build_automated`.
3. Confirm the anchors still match the new board's sources:
   ```bash
   make pre_validate_automation
   ```

IP yamls already template their paths on `{board_dir}`, so they need no edits —
only the anchor *patterns* have to match the new board's file contents.

---

## Adding a New Peripheral (zero Python changes)

1. Create `ip_bsv/<name>/<name>.yaml` with `anchors:` + `patches:`
   (`pwm_v1.yaml` is the simplest model; `rtc_v1.yaml` the most complete).
2. Add an entry to `automated_peripherals` in `soc_build_config.yaml`.
3. `make pre_validate_automation && make run_autointeg_complete_dry`.
4. Apply, post-validate, compile.

Ship a `.bsv` in the IP directory **only if** the peripheral introduces new RTL.

**Example** — a second direct-connected timer, reusing `devices/gptimer/`:

```yaml
- name: gptimer_v1
  def_path: ip_bsv/gptimer_v1/gptimer_v1.yaml
  base_context:
    xdc_file: '{board_dir}/constraints.xdc'
    gptimer_io_standard: LVCMOS12
  instances:
  - instance_id: '4'
    slave_id_macro: GPTimer4_slave_num   # value comes from {allocated_slot}
    base_macro: GPTimer4Base
    base_addr: '0004_0800'
    end_macro: GPTimer4End
    end_addr: '0004_08FF'
    connect_to_plic: true
    plic_index: 42
    gptimer_in_pin: fmc_la17_cc_n
    gptimer_out_pin: fmc_la18_cc_n
    gptimer_in_fallback: fmc_la_n[06]
    gptimer_out_fallback: fmc_la_n[08]
```

Note there is **no** `slave_id_val`: slave numbers are allocated dynamically so
the entry can sit anywhere in the list. See `UserManual.md` §17 for the three
independence rules, and §19 for this peripheral in full.

---

## Safety Features

- **Transactional patching** — all target files are edited in an in-memory VFS and
  flushed only after every patch succeeds
- **Baseline backup** — `.automation_backup/` captures the pristine tree on the
  first real run and is kept across restores
- **Idempotent execution** — skip guards make every phase safe to re-run
- **Pre/post validation** — anchors verified before, injections verified after
- **Dry-run mode** — preview all changes without touching disk
- **Width-agnostic PLIC** — handles any starting interrupt count
- **XDC conflict resolution** — a taken pin auto-falls-back to `fallback_pin`
- **Resource tracking** — duplicate address ranges and PLIC indices are rejected

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "Anchor not found" | Upstream code changed, or wrong `board_dir` | Update `pattern` in the IP yaml; check `board_dir` |
| Counters grow on every re-run | Skip guard keys on `{calculated_val}` | Guard on the per-instance macro instead (UserManual §7) |
| "PLIC width != signal count" | `mkplic` wrapper out of sync | Re-run `run_autointeg_complete`; check `plic_vector` anchor patterns |
| `restore` is a silent no-op | Baseline was captured from an already-patched tree | `make reset_autointeg_baseline`, restore sources from git, re-run |
| Makefile "missing separator" | Spaces instead of TABs in a recipe | Replace indentation with literal TAB characters |
| `bsc` not found | Bluespec compiler not in `$PATH` | Install it, or skip the compile check |
| Validation "file not found" | Wrong working directory | Run from `gc2025/hw/` |

---

## Known Gaps

- **`TbSoc.bsv` has drifted out of sync with `chip_io` (dormant).** `gpio_v1` and
  `i2c_v1` expose `always_enabled` chip_io *inputs* (`chip_io_gpio_32..47`,
  `chip_io_i2c{2,3}_out_*_in`) without adding the matching tie-offs to
  `TbSoc.bsv`, so `bsc -g mkTbSoc` fails with
  `(G0066) ... requires the following methods to be always enabled`.

  **Nothing in this repo currently compiles `TbSoc.bsv`.** `TOP_MODULE` is
  `mkDebugSoc`, and both `quick_build_sim` and the FPGA flow reach it through
  `generate_verilog` (`TOP_FILE := DebugSoc.bsv`). `TbSoc.bsv` appears only in
  `SYNC_BSV_FILES` (a copy list) and in the backup manifest — no target builds
  it, and no BSV imports it. So this breaks no build today; it would surface only
  if `TOP_MODULE` were pointed at `mkTbSoc` or a Bluesim testbench flow revived.
  `uart_v1`, `sspi_v1` and `gptimer_v1` keep
  `TbSoc.bsv` consistent anyway. See `UserManual.md` §19.
- **Peripheral order in `soc_build_config.yaml` is part of the software ABI.**
  Reordering entries silently renumbers PLIC source IDs. See `UserManual.md` §17.

---

This framework demonstrates data-driven hardware design: YAML as the single source
of truth for hardware wiring, address maps, interrupt routing, and FPGA pin export
— with automated validation at every step.
