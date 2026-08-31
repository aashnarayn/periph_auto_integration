# Automated Peripheral Integration (PoC)
## Shakti GC2025 — YAML-Driven UART3 Integration (Dedicated Pins)

## Goal
Demonstrate end-to-end peripheral integration via YAML configuration alone — zero manual code edits — by adding a fourth UART (UART3) to the SoC and wiring it out to dedicated FPGA pins.

> **Routing mode.** This payload is the **without-pinmux** variant. UART3 becomes a top-level SoC port pair and gets its own pins. The sibling framework `auto_integ_legacy_v1_uart_pinmux/` routes the same UART3 through pinmux cells 12/13 instead.

> **See also.** `docs/UserManual.md` is the full technical reference — patch
> schema, phase order, idempotency model, verification recipes, and this
> framework's own headline bug (the sync-list fix, 2026-08-24).

---

## Deliverables
```
gc2025/hw/
├── Makefile                     # Build system + automation targets
├── bsvpath                      # Include paths
├── soc_build_config.yaml        # Centralized Bill of Materials (BOM)
├── scripts/
│   ├── peripheral_auto_integrator.py  # V1 procedural automation engine
│   └── track_pin_map.py               # XDC pin map generator / state tracker
├── boards/
│   └── nexys_video/             # Board sources (patched by automation)
│       ├── Soc.defines
│       ├── Soc.bsv
│       ├── TbSoc.bsv
│       ├── uart_cluster.bsv
│       ├── mixed_cluster.bsv
│       ├── fpga_top.v
│       └── constraints.xdc
├── ip_bsv/
│   ├── uart_v1/                 # The peripheral being integrated
│   │   ├── uart_v1.yaml         # IP definition + patch instructions
│   │   └── uart_validate.sh     # Anchor validation suite
│   └── rtc_v1/                  # Inherited from the RTC prototype; UNUSED here
└── README_AUTOMATION.md         # This file
```

UART3 ships **no new BSV source**. It reuses `devices/uart_v2/`, which the base SoC already provides for uart0–uart2, so the def only instantiates an existing module. This is also why there is no `bsvpath` patch.

---

## How Automation Works

```
1. Patches applied → boards/nexys_video/*  (via uart_v1.yaml paths)
       ↓
2. make sync_bsv_patches → copies patched board files to hw/ root
       ↓
3. make generate_verilog → compiles patched BSV → Verilog
       ↓
4. make board_build → Vivado synthesis → bitstream with integrated UART3
```

### Key Design Decisions
| Component | Strategy | Why |
|-----------|----------|-----|
| **`board_dir` in `soc_build_config.yaml`** | Single source for validation | Auto-detection; no CLI flags needed |
| **Idempotency** | `skip_if_contains` on **every** patch | An anchor miss rolls the whole transaction back to pristine, so guards are mandatory, not optional |
| **Interrupt widths** | Derived from the concat, not hardcoded | UART3 grows the bus `Bit#(13)`→`Bit#(14)` and PLIC 35→36; the slice bounds move with it |
| **Pin conflicts** | `fallback_pin` per signal | `ja[9]`/`ja[10]` are occupied in a stock clone; incumbents relocate automatically |

### The sync list is part of the contract
`sync_bsv_patches` must copy **every** board file this def patches, or the compiler reads a stale `hw/` root copy. For this framework that is:

```
Soc.bsv  Soc.defines  mixed_cluster.bsv  uart_cluster.bsv  TbSoc.bsv
```

Omitting `uart_cluster.bsv` produces a confusing pair of errors — `Field 'uart3_io' is not in the type 'uart_cluster::Ifc_uart_cluster'`, plus a cascading `pwm_cluster.pwm0_sb_interrupt Expected Bit#(2) Inferred Bit#(1)` as `bsc` tries to make up the missing interrupt bit. If you add a patch targeting a new file, add that file to both sync targets.

---

## Quick Start

```bash
cd gc2025/hw

# 1. Verify prerequisites
python3 -c "import yaml; print('[INFO] PyYAML ready')"
which bsc || echo "[WARN] bsc not in PATH"

# 2. Pre-automation validation
make pre_validate_automation

# 3. Dry-run (SAFE — no files modified)
make run_peripheral_automation_dry

# 4. Apply automation
make run_peripheral_automation

# 5. Post-automation verification
make post_validate_automation

# 6. Build end-to-end
make quick_build_automated

# Rollback at any point
make restore_peripheral_patches
```

---

## Makefile Targets

| Target | Purpose |
|--------|---------|
| `run_peripheral_automation` | Apply all phases (BSV → Verilog → XDC) |
| `run_peripheral_automation_dry` | Preview all phases without modifying files |
| `run_peripheral_bsv` / `_dry` | Phase 1 only |
| `run_peripheral_verilog` / `_dry` | Phase 2 only (`fpga_top.v`) |
| `run_peripheral_xdc` / `_dry` | Phase 3 only (`constraints.xdc`) |
| `sync_bsv_patches` | Copy patched board BSV to the hw/ root |
| `sync_automated_files` | Same, plus `constraints.xdc` and `fpga_top.v` |
| `track_pin_map` | Re-export `pin_map.yaml` from the current XDC |
| `restore_peripheral_patches` | Roll back to pre-automation state |
| `pre_validate_automation` | Anchor validation before patching |
| `post_validate_automation` | Patch verification after patching |
| `quick_build_automated` | Full flow: validate → automate → build |

### Exit Codes
| Code | Meaning |
|------|---------|
| `0` | All checks passed |
| `1` | One or more checks failed |
| `2` | Invalid arguments, missing config, or missing target files |

> **`board_build` exit codes are unreliable.** Its second Vivado invocation ends in `|| (echo "ERROR: While running synthesis")` with no `exit 1`, so the target reports success even when synthesis fails. Read `artybuild.log` rather than trusting `$?`.

---

## What Gets Patched

| File | Injection |
|------|-----------|
| `Soc.defines` | `UARTCluster_Num_Slaves` 4→5, `UART3_slave_num`, `UART3Base`/`UART3End`, `err_slave_num` 3→4 |
| `uart_cluster.bsv` | `uart3` instance, `uart3_io`, decoder arm, AXI connection, `uart_interrupts` `Bit#(3)`→`Bit#(4)` |
| `mixed_cluster.bsv` | interrupt bus →`Bit#(14)`, PLIC 35→36, `plic_inputs` slices `[12:6]`→`[13:7]`, `[5:0]`→`[6:0]` |
| `Soc.bsv` | `interface RS232 uart3_io` on `chip_io`, exported from `uart_cluster` |
| `TbSoc.bsv` | `uart3` sim instance + tx/rx wiring |
| `fpga_top.v` | `uart3_SIN`/`uart3_SOUT` ports + SoC port map |
| `constraints.xdc` | `uart3_SIN`→`ja[9]`, `uart3_SOUT`→`ja[10]`, incumbents relocated to fallbacks |

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| "Anchor not found" then everything reverts | A patch lost its anchor and had no `skip_if_contains`; the transaction rolled back to pristine | Add a guard unique to that patch's own output |
| `Field 'uart3_io' is not in the type ...` | `uart_cluster.bsv` not synced to the hw/ root | Check `sync_bsv_patches` covers every patched file |
| `pwm0_sb_interrupt Expected Bit#(2)` | Cascade of the above — stale `uart_interrupts` width at the root | Same fix |
| Half-integrated tree after a failed re-run | BSV rolled back but XDC/`fpga_top.v` still patched | `make restore_peripheral_patches`, then re-apply |
| "BSV syntax error" | Template variable missing in `context` | Check `context` keys match `{placeholders}` in `uart_v1.yaml` |
| Pin already assigned | Desired pin occupied and no fallback defined | Add `uart_rx_fallback` / `uart_tx_fallback` |
| `bsc` not found | Bluespec compiler not in `$PATH` | Install it, or skip the compile check |

---

## Scaling Beyond Proof of Concept

The limitations here are what motivated the V2 engine:

1. **Multi-peripheral composition** — V1 integrates one IP; V2 takes a list and allocates shared resources dynamically.
2. **Indentation policy** — V1 injects at column 0; V2 resolves a named `indent_context` per patch.
3. **VFS + atomic commit** — V1 writes files directly under a rollback transaction; V2 patches an in-memory VFS and flushes only on full success.
4. **Guard expressiveness** — V1 has only `skip_if_contains` (a substring); V2 adds `skip_if_matches` (a regex), which is what lets a patch guard on a pattern its own replacement consumes.
