# auto_integration - Legacy V1 Framework (UART3 Prototype, via Pinmux)

An archived, proof-of-concept framework to *automatically integrate* a fourth UART (UART3) into the *Shakti gc2025* SoC, routed through the **pinmux** on cells 12 and 13.

> **Legacy Warning.** This folder contains a **V1 procedural prototype**. It relies on heavily hardcoded Python script logic and integrates exactly one peripheral. It has been superseded by the modular, multi-peripheral V2 engine in the repository root. This archive is maintained for reference and isolated UART testing.

> **Formerly `auto_integ_legacy_v1_uart_nopinmux/`.** Renamed 2026-08-24: the
> old name said the opposite of what this framework does. Nothing inside it
> changed in the rename.

> **Routing mode.** This is the **with-pinmux** variant. UART3 never reaches a top-level SoC port; it shares muxed pads with GPIO and PWM. Its sibling, `auto_integ_legacy_v1_uart/`, wires the same UART3 to dedicated FPGA pins instead. See [Choosing between the two UART variants](#choosing-between-the-two-uart-variants).

---

## What this folder is

`auto_integ_legacy_v1_uart_pinmux/` is the standalone **manager** that installs this V1 payload into a fresh `gc2025` clone.

```text
auto_integ_legacy_v1_uart_pinmux/
├── Makefile          # the legacy manager: clone, inject, status, remove, delete
├── configs/
│   └── soc_build_config.yaml  # single static config (UART3 through pinmux cells)
└── files/            # the V1 payload injected into a clone's hw/
    ├── Makefile              # the legacy automation-enabled hw/ Makefile
    ├── master_constraints.xdc
    ├── scripts/             # V1 procedural scripts
    ├── ip_bsv/              # rtc_v1/ (inherited, unused) and uart_v1/
    └── docs/               # README_AUTOMATION.md
```

---

## Usage

```bash
# Setup this UART3 environment into a fresh clone:
make setup_base SETUP_PATH=../../workspace CLONE_NAME=gc2025_autouart_pinmux
# then, inside the clone:
cd ../../workspace/gc2025_autouart_pinmux/hw && make BOARD=nexys_video build_automated
```

### Arguments

| Variable | Default | Meaning |
| --- | --- | --- |
| `SETUP_PATH` | `$(HOME)/Desktop/shakti-dev/workspace3` | Where to create/find the clone. |
| `CLONE_NAME` | `gc2025_autouart_pinmux` | Clone directory name → `$(SETUP_PATH)/$(CLONE_NAME)`. |
| `BOARD` | `nexys_video` | Target board. |
| `FORCE` | `0` | `1` = reuse/overwrite an existing clone (skips `git clone`). |

*(There is no `CONFIG_SELECT` in V1 — it targets the single `soc_build_config.yaml`.)*

### Targets

| Target | Does |
| --- | --- |
| `setup_full` | (A) clone → inject → deploy config → track pin map → `run_setup_build`. |
| `setup_base` | (B) same as `setup_full` **without** `run_setup_build`. |
| `remove_automation` | (C) scrub the framework from a clone; restore its original Makefile. |
| `delete_clone` | (D) delete the clone (warns first). |
| `status` | (E) identify which framework the clone resembles, then diff against that one. |
| `update_framework` | (F) push the latest `files/` into an existing clone without re-cloning. |

---

## What it integrates

UART3 is added to the UART cluster and its tx/rx are steered into **pinmux cells 12 and 13**, so it consumes no new FPGA pins.

| File | What is injected |
| --- | --- |
| `Soc.defines` | `UARTCluster_Num_Slaves` 4→5, `UART3_slave_num`, `UART3Base`/`UART3End` (`0001_1600`–`0001_1640`), `err_slave_num` 3→4 |
| `uart_cluster.bsv` | `uart3` instance, `uart3_io` interface, decoder arm, AXI connection, `uart_interrupts` `Bit#(3)`→`Bit#(4)` |
| `mixed_cluster.bsv` | interrupt bus `Bit#(13)`→`Bit#(14)`, PLIC width 35→36, `plic_inputs` slices `[12:6]`→`[13:7]` and `[5:0]`→`[6:0]` |
| `pinmux.bsv` | `wruart3_tx`/`wruart3_rx` wires, `PeripheralSideUART uart3`, cell 12 and 13 mux arms, their capture rules, and the peripheral-side tx/rx implementation |
| `Soc.bsv` | `uart3.tx.put(uart_cluster.uart3_io.sout)` and `uart_cluster.uart3_io.sin(pinmux_uart3_rx)` |

**No `fpga_top.v` and no `constraints.xdc` patches** — by design. Cells 12 and 13 already have pads and constraints; UART3 simply becomes another option on their existing muxes.

### The cell direction convention

The pad direction control in this codebase reads backwards from intuition, and getting it wrong is silent:

| Signal kind | `cellN_mux_out` | `cellN_mux_outen` |
| --- | --- | --- |
| **rx** (input) | `val0` | **`val1`** |
| **tx** (output) | the tx wire | **`val0`** |

`val1` on `outen` means *the pad is an input*. Cells 7/9 (uart1_rx, uart2_rx) and 8/10 (uart1_tx, uart2_tx) are the reference implementations; cells 12/13 mirror them exactly.

---

## Choosing between the two UART variants

| | `auto_integ_legacy_v1_uart` | `auto_integ_legacy_v1_uart_pinmux` (this one) |
| --- | --- | --- |
| Routing | Dedicated FPGA pins | Pinmux cells 12 and 13 |
| Patches `pinmux.bsv` | No | Yes (9 patches) |
| Patches `fpga_top.v` | Yes | No |
| Patches `constraints.xdc` | Yes | No |
| Patches `TbSoc.bsv` | Yes | No |
| Top-level SoC ports | `chip_io_uart3_io_SIN/SOUT/SOUT_EN` | none — internal to the pinmux |
| Displaces other pins | Yes (`gptimer1_out`, `gpio_29`) | No |
| Costs pinmux cells | No | Yes (cells 12, 13) |

Pick this one when pins are scarce and UART3 can share muxed cells. Pick the dedicated-pin variant when you want UART3 permanently available on its own header pins.

---

## Verified state

Verified end to end on `nexys_video`: setup → pre-validate → integrate → post-validate → `generate_verilog` → Vivado bitstream. The integration is idempotent (re-running changes nothing) and fully reversible via `restore_peripheral_patches`.

Generated RTL confirms four `mkuart` instances in `mkuart_cluster.v`, `peripheral_side_uart3_tx_put`/`_rx_get` in `mkpinmux.v`, and a cell12 outen muxer structurally identical to the working cell7 reference:

```verilog
2'd1: iocell_side_io12_cell_outen = 1'b1;   // uart3_rx reads as an input
```

---

## Architecture Limitations

This is a procedural prototype; the engine is tightly coupled to its payload.

1. **One peripheral per framework.** The scripts cannot compose multiple IP definitions the way V2 does. `ip_bsv/rtc_v1/` is present but unused — it is inherited from the RTC prototype this framework was forked from.
2. **No indentation system.** Injected lines land at column 0 or with mangled indent. Cosmetic only, but it is why V2 introduced `INDENT_PROFILE`.
3. **Anchor-miss is fatal, not skippable.** A patch whose anchor is gone returns failure inside the `PatchTransaction`, which then rolls every file back to the pristine baseline. Every patch therefore carries a `skip_if_contains` guard.
4. **Guards must be unique per patch.** Several patches in this def produce similar text. If two patches share both an anchor and a guard, the first consumes the anchor and the second silently self-skips — which is exactly how the cell12 out/outen muxers were once left inverted. Anchor each muxer on its enclosing block, and guard on that patch's own distinctive output.
5. **`update_bsvpath` is a misnomer here.** In V1 the `bsvpath` phase applies "patch #1" whatever it happens to be. UART3 reuses `devices/uart_v2/`, so it needs no `bsvpath` entry and patch #1 is a `Soc.defines` edit instead. Harmless — it is guarded and idempotent — but do not read the target name literally.
6. **Limited board support.** The Makefile accepts `BOARD`, but the scripts assume a `nexys_video` footprint.

---

## Cross-Framework Interoperability

Three legacy frameworks now live side by side in `archive/`, alongside the Main V2 engine at the repository root. Because clones get reused across experiments, every manager's `status` target is mutually aware.

When you run `make status` here:

1. **Candidate scoring.** It computes a byte-for-byte match score for the clone against *this* framework, against Main V2 (`../../files`), and against every sibling legacy framework (`../*/files`), printing one `[CANDIDATE]` line each.
2. **Closest-match selection.** The highest scorer wins. Ties are broken by checking which framework ships a `soc_build_config.yaml` matching the clone's active config — necessary because this framework and `auto_integ_legacy_v1_uart` share their entire `scripts/` directory and differ only in `ip_bsv/`, the hw Makefile sync lists, and the config.
3. **Targeted diffing.** The config check, deep integrity scan, and documentation check all run against the **winning** framework, not blindly against this directory.
4. **Colour routing.** A match on this framework is `GREEN`; Main V2 or a sibling legacy framework is `YELLOW`, flagging that the clone belongs to a different manager.

The Main V2 manager and the other legacy managers implement the same logic from their own vantage point.
