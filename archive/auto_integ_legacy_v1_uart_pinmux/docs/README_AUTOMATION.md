# Automated Peripheral Integration (PoC)
## Shakti GC2025 — YAML-Driven UART3 Integration (via Pinmux)

## Goal
Demonstrate end-to-end peripheral integration via YAML configuration alone — zero manual code edits — by adding a fourth UART (UART3) to the SoC and steering it through pinmux cells 12 and 13.

> **Routing mode.** This payload is the **with-pinmux** variant. UART3 stays internal to the SoC and shares muxed pads with GPIO/PWM. The sibling framework `auto_integ_legacy_v1_uart/` wires the same UART3 to dedicated FPGA pins instead.

> **See also.** `docs/UserManual.md` is the full technical reference —
> including the cell12/13 muxer convention and the 2026-08-24 fix for the
> UART3-RX-dead-in-hardware bug that a pristine build couldn't reveal on its
> own.

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
│       ├── uart_cluster.bsv
│       ├── mixed_cluster.bsv
│       └── pinmux.bsv
├── ip_bsv/
│   ├── uart_v1/                 # The peripheral being integrated
│   │   ├── uart_v1.yaml         # IP definition + patch instructions
│   │   └── uart_validate.sh     # Anchor validation suite
│   └── rtc_v1/                  # Inherited from the RTC prototype; UNUSED here
└── README_AUTOMATION.md         # This file
```

UART3 ships **no new BSV source**. It reuses `devices/uart_v2/`, which the base SoC already provides for uart0–uart2, so the def only instantiates an existing module. This is also why there is no `bsvpath` patch. Note that `fpga_top.v` and `constraints.xdc` are **not** in the patched set — cells 12/13 already have pads and constraints.

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
| **Idempotency** | `skip_if_contains` on **every** patch | An anchor miss rolls the whole transaction back to pristine, so guards are mandatory |
| **Guard uniqueness** | One guard per patch, matching that patch's own output | Two patches sharing an anchor *and* a guard silently collide — see below |
| **Interrupt widths** | Derived from the concat, not hardcoded | UART3 grows the bus `Bit#(13)`→`Bit#(14)` and PLIC 35→36 |
| **No pin allocation** | UART3 rides existing muxed pads | No XDC edits, no displaced peripherals |

### The duplicate-anchor trap
Cells 12 and 13 each have **two** muxers whose stock text is identical:

```bsv
      // output muxer for cell idx 12
      cell12_mux_out=
			...
			wrcell12_mux==1?val0: // unused      <-- appears here

      // outen muxer for cell idx 12
      cell12_mux_outen=
			...
			wrcell12_mux==1?val0: // unused      <-- and identically here
```

A patch anchored on the bare line hits the **first** occurrence only. If the companion patch shares that anchor *and* its `skip_if_contains`, it then self-skips and the second muxer is never touched. Each muxer patch therefore anchors on its enclosing block (including the preceding `mux==0` arm) and guards on its own distinctive output.

### The cell direction convention
Getting this backwards is silent — it compiles, synthesizes, and produces a bitstream that simply does not receive:

| Signal kind | `cellN_mux_out` | `cellN_mux_outen` |
|---|---|---|
| **rx** (input) | `val0` | **`val1`** |
| **tx** (output) | the tx wire | **`val0`** |

`val1` on `outen` means *the pad is an input*. Cells 7/9 (uart1_rx, uart2_rx) and 8/10 (uart1_tx, uart2_tx) are the reference implementations. Verify against the generated Verilog, not the source:

```verilog
2'd1: iocell_side_io12_cell_outen = 1'b1;   // correct — matches cell7
```

### The sync list is part of the contract
`sync_bsv_patches` must copy **every** board file this def patches:

```
Soc.bsv  Soc.defines  mixed_cluster.bsv  uart_cluster.bsv  pinmux.bsv
```

Omitting `pinmux.bsv` yields `Field 'uart3' is not in the type 'pinmux::PeripheralSide'`; omitting `uart_cluster.bsv` yields a cascading `pwm_cluster.pwm0_sb_interrupt Expected Bit#(2) Inferred Bit#(1)`.

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
| `run_peripheral_automation` | Apply all phases |
| `run_peripheral_automation_dry` | Preview all phases without modifying files |
| `run_peripheral_bsv` / `_dry` | Phase 1 only |
| `sync_bsv_patches` | Copy patched board BSV to the hw/ root |
| `sync_automated_files` | Same, plus `constraints.xdc` and `fpga_top.v` |
| `track_pin_map` | Re-export `pin_map.yaml` from the current XDC |
| `restore_peripheral_patches` | Roll back to pre-automation state |
| `pre_validate_automation` | Anchor validation before patching |
| `post_validate_automation` | Patch verification after patching |
| `quick_build_automated` | Full flow: validate → automate → build |

The `run_peripheral_verilog` and `run_peripheral_xdc` phases exist and are safe to run, but this def has no patches in either phase, so they are no-ops.

### Exit Codes
| Code | Meaning |
|------|---------|
| `0` | All checks passed |
| `1` | One or more checks failed |
| `2` | Invalid arguments, missing config, or missing target files |

> **`board_build` exit codes are unreliable.** Its second Vivado invocation ends in `|| (echo "ERROR: While running synthesis")` with no `exit 1`. Read `artybuild.log` rather than trusting `$?`.

---

## What Gets Patched

| File | Injection |
|------|-----------|
| `Soc.defines` | `UARTCluster_Num_Slaves` 4→5, `UART3_slave_num`, `UART3Base`/`UART3End`, `err_slave_num` 3→4 |
| `uart_cluster.bsv` | `uart3` instance, `uart3_io`, decoder arm, AXI connection, `uart_interrupts` `Bit#(3)`→`Bit#(4)` |
| `mixed_cluster.bsv` | interrupt bus →`Bit#(14)`, PLIC 35→36, `plic_inputs` slices `[12:6]`→`[13:7]`, `[5:0]`→`[6:0]` |
| `pinmux.bsv` | `wruart3_tx`/`wruart3_rx` wires, `PeripheralSideUART uart3`, cell 12/13 out and outen muxer arms, capture rules, peripheral-side tx/rx |
| `Soc.bsv` | `uart3.tx.put(...)` and `uart_cluster.uart3_io.sin(pinmux_uart3_rx)` |

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| "Anchor not found" then everything reverts | A patch lost its anchor and had no `skip_if_contains` | Add a guard unique to that patch's own output |
| `Field 'uart3' is not in the type 'pinmux::PeripheralSide'` | `pinmux.bsv` not synced to the hw/ root | Check `sync_bsv_patches` covers every patched file |
| `pwm0_sb_interrupt Expected Bit#(2)` | Stale `uart_cluster.bsv` at the root | Same fix |
| Builds fine but UART3 never receives | cell12 `outen` left at `val0` (drive-as-output) | Confirm `2'd1: ..._io12_cell_outen = 1'b1` in the generated `mkpinmux.v` |
| A muxer patch silently did nothing | It shares an anchor **and** a guard with its companion | Anchor on the enclosing block; give each its own guard |
| `bsc` not found | Bluespec compiler not in `$PATH` | Install it, or skip the compile check |

---

## Scaling Beyond Proof of Concept

The limitations here are what motivated the V2 engine:

1. **Multi-peripheral composition** — V1 integrates one IP; V2 takes a list and allocates shared resources dynamically.
2. **Indentation policy** — V1 injects at column 0; V2 resolves a named `indent_context` per patch.
3. **VFS + atomic commit** — V1 writes files directly under a rollback transaction; V2 patches an in-memory VFS and flushes only on full success.
4. **Guard expressiveness** — V1 has only `skip_if_contains` (a substring); V2 adds `skip_if_matches` (a regex), which is precisely what the cell12/cell13 muxer pairs would have wanted.
5. **Unified routing modes** — V2 folds both UART routing variants into one def selected by a per-instance `uart_mode`, rather than two forked frameworks.
