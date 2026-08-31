# auto_integration - Legacy V1 Framework (UART3 Prototype, Dedicated Pins)

An archived, proof-of-concept framework to *automatically integrate* a fourth UART (UART3) into the *Shakti gc2025* SoC, wired out to **dedicated FPGA pins**.

> **Legacy Warning.** This folder contains a **V1 procedural prototype**. It relies on heavily hardcoded Python script logic and integrates exactly one peripheral. It has been superseded by the modular, multi-peripheral V2 engine in the repository root. This archive is maintained for reference and isolated UART testing.

> **Routing mode.** This is the **without-pinmux** variant. UART3 leaves the SoC as a top-level port pair and lands on its own FPGA pins. Its sibling, `auto_integ_legacy_v1_uart_pinmux/`, routes the same UART3 through the pinmux instead. See [Choosing between the two UART variants](#choosing-between-the-two-uart-variants).

---

## What this folder is

`auto_integ_legacy_v1_uart/` is the standalone **manager** that installs this V1 payload into a fresh `gc2025` clone.

```text
auto_integ_legacy_v1_uart/
├── Makefile          # the legacy manager: clone, inject, status, remove, delete
├── configs/
│   └── soc_build_config.yaml  # single static config (UART3 on dedicated pins)
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
make setup_base SETUP_PATH=../../workspace CLONE_NAME=gc2025_autouart
# then, inside the clone:
cd ../../workspace/gc2025_autouart/hw && make BOARD=nexys_video build_automated
```

### Arguments

| Variable | Default | Meaning |
| --- | --- | --- |
| `SETUP_PATH` | `$(HOME)/Desktop/shakti-dev/workspace3` | Where to create/find the clone. |
| `CLONE_NAME` | `gc2025_autouart` | Clone directory name → `$(SETUP_PATH)/$(CLONE_NAME)`. |
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

UART3 is added to the UART cluster and routed **straight to FPGA pins** — no pinmux cells consumed.

| File | What is injected |
| --- | --- |
| `Soc.defines` | `UARTCluster_Num_Slaves` 4→5, `UART3_slave_num`, `UART3Base`/`UART3End` (`0001_1600`–`0001_1640`), `err_slave_num` 3→4 |
| `uart_cluster.bsv` | `uart3` instance, `uart3_io` interface, decoder arm, AXI connection, `uart_interrupts` `Bit#(3)`→`Bit#(4)` |
| `mixed_cluster.bsv` | interrupt bus `Bit#(13)`→`Bit#(14)`, PLIC width 35→36, `plic_inputs` slices `[12:6]`→`[13:7]` and `[5:0]`→`[6:0]` |
| `Soc.bsv` | `interface RS232 uart3_io` on `chip_io`, exported from `uart_cluster` |
| `TbSoc.bsv` | `uart3` sim instance and its tx/rx wiring |
| `fpga_top.v` | `uart3_SIN`/`uart3_SOUT` top-level ports and their SoC port map |
| `constraints.xdc` | `uart3_SIN`→`ja[9]`, `uart3_SOUT`→`ja[10]`, with automatic fallback |

### Pin displacement

`ja[9]` and `ja[10]` are already taken in a stock clone, so the XDC handler relocates the incumbents to their fallbacks. Verified through Vivado placement:

```
AA20  uart3_SIN      LVCMOS33  bank 14   (ja[9],  was gptimer1_out)
AA18  uart3_SOUT     LVCMOS33  bank 14   (ja[10], was gpio_29)
N22   gptimer1_out   LVCMOS12  bank 15   (relocated to fmc_la_p[06])
M22   gpio_29        LVCMOS12  bank 15   (relocated to fmc_la_n[06], PULLDOWN preserved)
```

---

## Choosing between the two UART variants

| | `auto_integ_legacy_v1_uart` (this one) | `auto_integ_legacy_v1_uart_pinmux` |
| --- | --- | --- |
| Routing | Dedicated FPGA pins | Pinmux cells 12 and 13 |
| Patches `pinmux.bsv` | No | Yes (9 patches) |
| Patches `fpga_top.v` | Yes | No |
| Patches `constraints.xdc` | Yes | No |
| Patches `TbSoc.bsv` | Yes | No |
| Top-level SoC ports | `chip_io_uart3_io_SIN/SOUT/SOUT_EN` | none — internal to the pinmux |
| Displaces other pins | Yes (`gptimer1_out`, `gpio_29`) | No |
| Costs pinmux cells | No | Yes (cells 12, 13) |

Pick this one when you want UART3 permanently available on its own header pins. Pick the pinmux variant when pins are scarce and UART3 can share muxed cells with GPIO/PWM.

---

## Verified state

Verified end to end on `nexys_video`: setup → pre-validate → integrate → post-validate → `generate_verilog` → Vivado bitstream. The integration is idempotent (re-running changes nothing) and fully reversible via `restore_peripheral_patches`.

Generated RTL confirms four `mkuart` instances in `mkuart_cluster.v` and `chip_io_uart3_io_SIN/SOUT/SOUT_EN` as genuine top-level ports on `mkSoc`.

---

## Architecture Limitations

This is a procedural prototype; the engine is tightly coupled to its payload.

1. **One peripheral per framework.** The scripts cannot compose multiple IP definitions the way V2 does. `ip_bsv/rtc_v1/` is present but unused — it is inherited from the RTC prototype this framework was forked from.
2. **No indentation system.** Injected lines land at column 0 or with mangled indent. Cosmetic only — BSV and Verilog are whitespace-insensitive — but it is why V2 introduced `INDENT_PROFILE`.
3. **The sync list is part of the contract.** `sync_bsv_patches` must copy every
   board file this def patches — `Soc.bsv`, `Soc.defines`, `mixed_cluster.bsv`,
   `uart_cluster.bsv` and `TbSoc.bsv`. The list was originally inherited from the
   RTC framework and covered only the first three, so `uart_cluster.bsv` never
   reached the `hw/` root and nothing could compile. If you add a patch targeting
   a new file, add that file to both sync targets. See
   `docs/README_AUTOMATION.md` for the failure signature.
4. **Anchor-miss is fatal, not skippable.** A patch whose anchor is gone returns failure inside the `PatchTransaction`, which then rolls every file back to the pristine baseline. Every patch therefore carries a `skip_if_contains` guard; if you add one without a guard, re-running the integration will silently revert it.
5. **`update_bsvpath` is a misnomer here.** In V1 the `bsvpath` phase applies "patch #1" whatever it happens to be. UART3 reuses `devices/uart_v2/`, so it needs no `bsvpath` entry and patch #1 is a `Soc.defines` edit instead. Harmless — it is guarded and idempotent — but do not read the target name literally.
6. **Limited board support.** The Makefile accepts `BOARD`, but the scripts assume a `nexys_video` footprint.

---

## Cross-Framework Interoperability

Three legacy frameworks now live side by side in `archive/`, alongside the Main V2 engine at the repository root. Because clones get reused across experiments, every manager's `status` target is mutually aware.

When you run `make status` here:

1. **Candidate scoring.** It computes a byte-for-byte match score for the clone against *this* framework, against Main V2 (`../../files`), and against every sibling legacy framework (`../*/files`), printing one `[CANDIDATE]` line each.
2. **Closest-match selection.** The highest scorer wins. Ties are broken by checking which framework ships a `soc_build_config.yaml` matching the clone's active config — necessary because legacy frameworks share most of their payload.
3. **Targeted diffing.** The config check, deep integrity scan, and documentation check all run against the **winning** framework, not blindly against this directory.
4. **Colour routing.** A match on this framework is `GREEN`; Main V2 or a sibling legacy framework is `YELLOW`, flagging that the clone belongs to a different manager.

The Main V2 manager and the other legacy managers implement the same logic from their own vantage point.
