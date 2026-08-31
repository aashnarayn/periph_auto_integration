# Legacy V1 — UART (Pinmux) Framework User Manual

This is the **mini** reference for `auto_integ_legacy_v1_uart_pinmux`: the V1
prototype that routes **UART3 through the pinmux** (cells 12/13), rather than
to dedicated FPGA pins — that's the sibling `auto_integ_legacy_v1_uart`
framework. Same one-peripheral, one-static-config, ~670-line procedural engine
shared across all three legacy frameworks. For the current, multi-peripheral V2
engine (which folds *both* UART routing modes into one `uart_v1` def selected
by `uart_mode`), see `periph_auto_integration/docs/UserManual.md`.

`README.md` (this framework's root) is the setup-manager quick reference.
`docs/README_AUTOMATION.md` is the PoC pitch doc, written for this variant
specifically (not a copy of RTC's or the dedicated-pin sibling's — see §9 of
that sibling's manual for the history). This file is the "how it actually
works" reference, and in particular the one place the pinmux muxer convention
and its 2026-08-24 bug are written down in full.

---

## 1. The big picture

```
soc_build_config.yaml          <- ONE static peripheral: uart_v1 (uart3, pinmux routing)
        │
        ├── ip_bsv/uart_v1/uart_v1.yaml   <- 28 patches: anchor + code + skip guard
        │
        ▼
scripts/peripheral_auto_integrator.py   <- ~670-line procedural engine (identical
        │                                   across all 3 legacy frameworks)
        ▼
boards/nexys_video/*   <- PATCHED: Soc.defines, uart_cluster.bsv, pinmux.bsv,
                           mixed_cluster.bsv  (NOT fpga_top.v, NOT constraints.xdc —
                           this variant allocates NO new top-level pins)
        │
        │  make sync_bsv_patches / sync_automated_files
        ▼
hw/ root copies   <- what bsc actually compiles
        │
        ▼
make generate_verilog  ->  build/  ->  make board_build  ->  bitstream
```

UART3 reuses the existing `devices/uart_v2/` IP exactly like the dedicated-pin
sibling — this def ships **zero new BSV source**. The distinguishing feature:
UART3's RX/TX are wired **through the pinmux's cell12/cell13 I/O cells**, which
already carry GPIO A5/A6 and PWM1 in other mux positions, rather than getting
their own reserved package pins. `mkSoc` therefore exports **no new top-level
uart3 port at all** — the wiring lives entirely inside `mixed_cluster.bsv`
(`pinmuxtop_peripheral_side.uart3`) and `pinmux.bsv` (the cell12/13 muxer
arms).

---

## 2. Repository layout

```
auto_integ_legacy_v1_uart_pinmux/
├── Makefile                  # setup manager (default CLONE_NAME: gc2025_autouart_pinmux)
├── README.md
├── configs/
│   └── soc_build_config.yaml
├── docs/
│   ├── README_AUTOMATION.md
│   └── UserManual.md         # this file
└── files/
    ├── Makefile
    ├── master_constraints.xdc
    ├── ip_bsv/
    │   ├── rtc_v1/             # inherited from the fork, integrates nothing — see
    │   │                       # the dedicated-pin sibling's manual §2 for why it's kept
    │   └── uart_v1/
    │       ├── uart_v1.yaml       # 28 patches
    │       └── uart_validate.sh
    └── scripts/
        ├── peripheral_auto_integrator.py  # shared engine
        └── track_pin_map.py
```

> **Renamed 2026-08-24.** This directory was previously
> `auto_integ_legacy_v1_uart_nopinmux` — the *inverse* of its contents. If you
> have an old note or script referencing `_nopinmux`, it means this directory.

---

## 3. The patch set (28 patches, 4 files, no XDC)

The BSV/`mixed_cluster.bsv`/interrupt-width patches (Soc.defines slave count,
memory map, `uart_cluster.bsv` interface/instantiation/decoder/AXI/wiring,
PLIC width `35`→`36` via plain hardcoded patches, external-interrupt bus width
`Bit#(13)`→`Bit#(14)`) are structurally the same shape as the dedicated-pin
sibling's — see that framework's manual §3 for the full walkthrough. The parts
that are genuinely different live in `Soc.bsv` and `pinmux.bsv`:

| # | File | What |
|---|---|---|
| 18-19 | `pinmux.bsv` | `PeripheralSide uart3` interface declaration + assignment block (the `rx.get`/`tx.put` methods `Soc.bsv` calls into) |
| 20-21 | `Soc.bsv` | TX: `pinmuxtop_peripheral_side.uart3.tx.put(uart_cluster.uart3_io.sout)`. RX: `let pinmux_uart3_rx <- pinmuxtop_peripheral_side.uart3.rx.get; uart_cluster.uart3_io.sin(pinmux_uart3_rx);` — **this is the entire top-level wiring**; there is no `fpga_top.v` or `constraints.xdc` patch anywhere in this def |
| 13-17 | `pinmux.bsv` | The cell12 (RX) and cell13 (TX) muxer arms — see §4, this is the part that had a real bug |

No `xdc_pin_assign` patches, no `fpga_top.v` patches. That absence is
deliberate and by design, not an omission — UART3 doesn't need new package
pins, it borrows cell12/13 which already have package pins assigned to other
functions in other mux positions.

---

## 4. The pinmux cell convention (read this before touching cell12/13)

Every pinmux I/O cell has **two** muxers deciding what the physical pad does:

- `cellN_mux_out` — the value driven **onto** the pin
- `cellN_mux_outen` — the pin's **direction**

**The convention reads backwards from intuition.** From the working reference
cells (uart1 on cell7/8, uart2 on cell9/10):

| Cell | Signal | `_mux_out` | `_mux_outen` |
|---|---|---|---|
| 7 | uart1_rx (input) | `val0` | **`val1`** |
| 8 | uart1_tx (output) | `wruart1_tx` | **`val0`** |
| 9 | uart2_rx (input) | `val0` | **`val1`** |
| 10 | uart2_tx (output) | `wruart2_tx` | **`val0`** |

**`val1` on `outen` means "the pad is an input."** `val0` means "drive it."
Effectively an active-low output enable. Get this backwards and a UART3-as-RX
cell will *build*, *synthesize*, *place*, *route*, and produce a *valid
bitstream* — it will simply never receive anything, because the pad is
statically driving instead of listening.

### The bug (found and fixed 2026-08-24)

Two of this def's patches were meant to hit cell12's `_mux_out` and `_mux_outen`
arms **separately**. They were **byte-identical** — same `anchor_pattern`, same
`code`, same `skip_if_contains`:

```yaml
anchor_pattern: "wrcell12_mux==1\\?val0: // unused"
position: "replace"
code: "                        wrcell12_mux==1?val1: // uart3_rx is an input"
skip_if_contains: "uart3_rx is an input"
```

That anchor text occurs **twice** in `pinmux.bsv` — once in each muxer,
identical in both, because both muxer arms share the same "unused" placeholder
comment before any peripheral claims the cell. The first patch matched and
replaced the **first** occurrence (the `_out` muxer, wrong target). The second
patch then found its own guard string already present (planted by the first
patch, since the injected text was identical) and self-skipped — so the value
meant for `_outen` never landed anywhere, and `_outen` kept its stock "unused"
value.

Result, as originally built:
```verilog
// cell12 (uart3_rx) — BROKEN
2'd1: iocell_side_io12_cell_out = 1'b1;                      // val1 landed in the WRONG muxer
assign iocell_side_io12_cell_outen =
         mux_lines_cell12_mux_in == 2'd0 && ~...;             // = 1'b0 when mux==1
```
At `mux==1`, `outen=0` puts the pad in **drive** mode with `out=1` — UART3 RX
held statically high as an **output**. The rule `wruart3_rx <=
cell12_mux_in` still existed and was semantically correct, but the pad was
never configured as an input, so nothing it computed ever reached anywhere.
This compiled and synthesized clean; the defect was invisible at every stage
except the generated Verilog and real hardware.

**The fix:** each muxer now anchors on its **enclosing block**, including the
*preceding* `mux==0` arm — which differs between the two muxers and so makes
each anchor unique — and each carries its own distinct guard:

```yaml
# out muxer
anchor_pattern: "wrcell12_mux==0\\?wrgpioa_a5_out:\\s*\\n\\s*wrcell12_mux==1\\?val0: // unused"
skip_if_contains: "wrcell12_mux==1?val0: // uart3_rx is an input"

# outen muxer
anchor_pattern: "wrcell12_mux==0\\?wrgpioa_a5_outen: // bi-directional\\s*\\n\\s*wrcell12_mux==1\\?val0: // unused"
skip_if_contains: "wrcell12_mux==1?val1: // uart3_rx is an input"
```

Verified in the generated Verilog, now structurally identical to the cell7
reference:
```verilog
2'd1, 2'd3: iocell_side_io12_cell_out = 1'b0;   // bsc merged the two val0 arms
2'd1:       iocell_side_io12_cell_outen = 1'b1; // matches cell7 exactly
```
**Re-confirmed 2026-08-31** on a fresh clone: identical output.

**Cell13 (TX) had the same latent duplicate-anchor defect** (patches #16/#17
sharing anchor + guard), but it was benign by luck — `val0` is the correct
`_outen` value for a TX cell, so the arm that silently failed to patch already
held the right value; only the "// unused" comment stayed stale. Re-anchored
properly anyway on the same principle: a known duplicate-anchor pair is a
defect class regardless of whether this particular instance happened to be
harmless.

### The general lesson

**Never let two patches in the same def share both an anchor and a guard.**
The first consumes the anchor; the second sees its own (identical) output
already present and silently no-ops — and nothing reports the collision. There
is no lint for this in the V1 engine (V2's roadmap proposes one — root
`HANDOFF.md` §10, item 2 — twenty lines that would have caught this
immediately).

---

## 5. Deliberately unfixed (documented, not a defect to "fix" casually)

- **Pinmux idle-high.** The reference cells set `wruartN_rx <= val1`
  (idle-high) in every non-UART mux branch of the corresponding rule;
  `wruart3_rx` is not, so it defaults to the DWire's `0` — a constant
  break-condition read when cell12 is muxed away from uart3. Harmless in the
  operating configuration (uart3 not selected then means nothing reads it),
  but it doesn't match the reference pattern. Three small patches would close
  this; left alone as out of scope for a frozen prototype.
- **`assign_wruart3_tx_on_cell13`'s body** still assigns `wrgpioa_a6_in<=val0`
  — a renamed rule with a stale (cosmetic) body reference. TX cell, so it has
  no functional effect.

---

## 6. Idempotency (2026-08-24 guards, 2026-08-31 shared-engine fix)

Guard coverage: 28 of 28 patches carry `skip_if_contains` (this variant has no
`xdc_pin_assign` patches at all, so there's no exemption to note). Seven
originally lacked a guard and were added 2026-08-24, alongside the sync-list
fix (§7) and the cell12/13 re-anchoring (§4) — all three were needed together
before this framework could build *and* re-run cleanly from the shipped flow.

The 2026-08-31 shared-engine fix (context population on the `plic_vector` skip
path; formatting `skip_if_contains` through context before the substring
check — full writeup in the dedicated-pin sibling's manual §5, and the RTC
manual §5 for the original diagnosis) was applied here too for consistency
across the three identical engine copies, but **this framework was never
exposed to either bug**: PLIC widening here is a plain hardcoded `35`→`36`
patch, not the `plic_vector` handler, and grepping `uart_v1.yaml` for
`skip_if_contains:.*{` returns nothing. Re-verified after the fix: fresh
integration → re-run → exit 0, every one of 28 patches printed `Skipping`,
zero printed `Patched`, all touched files md5-identical.

---

## 7. Recovery, sync, and the framework's own headline bug (2026-08-24)

Same restore mechanism as the other two frameworks (`restore_peripheral_patches`
— see the RTC manual §6). Verified byte-clean 2026-08-24 and 2026-08-31.

**`sync_bsv_patches`/`sync_automated_files` originally missed this def's actual
footprint**, inherited from the RTC fork's file list (`Soc.bsv`,
`Soc.defines`, `mixed_cluster.bsv` only). This def also patches
`uart_cluster.bsv` and `pinmux.bsv`, neither of which reached the `hw/` root —
`generate_verilog` failed with `Field 'uart3' is not in the type
'pinmux::PeripheralSide'` against the stale `pinmux.bsv`. Fixed by adding both
files to both sync lists. Verified fresh 2026-08-24 and re-verified 2026-08-31:
`generate_verilog` succeeds with no manual `cp`.

---

## 8. Verification recipes

```bash
make pre_validate_automation          # expect 20/20 PASS
make run_peripheral_automation_dry
make run_peripheral_automation
make sync_bsv_patches
make generate_verilog -j              # success: build/hw/verilog/{mkSoc,mkDebugSoc}.v
make post_validate_automation         # expect 27/27 PASS
make run_peripheral_automation        # idempotency: expect all "Skipping", zero "Patched"
make build_automated                  # full flow to bitstream
make restore_peripheral_patches
```

**RTL-level confirmation** — the pinmux post-checks specifically must assert
the muxer *arms*, not just that a string exists somewhere in the file: the old
check (`grep -qF "uart3_rx is an input"`) passed even with the §4 bug fully
live, because the string was present — just attached to the wrong muxer.
```bash
grep -A2 "value method iocell_side_io12_cell_outen" build/hw/verilog/mkpinmux.v
# expect:  2'd1: iocell_side_io12_cell_outen = 1'b1;   (matches cell7)
grep -c "uart3_io" build/hw/verilog/mkSoc.v
# expect internal-wire matches only (uart_cluster_uart3_io_SIN/SOUT) — NOT
# chip_io_-prefixed top-level ports; those belong to the dedicated-pin sibling
```

**Full bitstream, verified 2026-08-31** (fresh clone, this machine, `bsc
2026.01`): synthesis 0 errors / 0 critical warnings, WNS +0.492 / TNS 0.000 /
WHS +0.051 / THS 0.000 (timing met — WHS matches the 2026-08-24 session's
number exactly), no new top-level pins allocated (by design), `mkpinmux.v`
carries the renamed rules `uart3_rx_on_cell12` / `uart3_tx_on_cell13`.

---

## 9. Framework-specific gotchas

- **Directory name used to be inverted** — see the `Renamed 2026-08-24` note
  in §2. Verify by content (what a def actually patches), never by name alone.
- **This framework ships an unused `ip_bsv/rtc_v1/`** — same inherited cruft
  as the dedicated-pin sibling, integrates nothing.
- **`pre_validate_automation`'s anchors are pinmux-specific** (`pinmux.bsv`
  cell12/13 text) — do not confuse with the dedicated-pin sibling's checks,
  which target `TbSoc.bsv`/`fpga_top.v` instead. If you ever see one
  framework's pre-checks passing against the *other* framework's clone, that's
  the anchor-retargeting bug this project already hit once (copy-pasted
  pre-checks from this framework into the dedicated-pin sibling, fixed
  2026-08-24 — see that sibling's manual, and the sibling's checks were never
  copied *into* this one, so this direction was always correct).
- **`board_build`'s exit code lies** — grep the log, don't trust `$?`.
- **`bsc` needs `-cross-info`** (present through 2026.01, removed in 2026.07)
  — see root `HANDOFF.md` / `ubuntu_setup/RESTORATION_CONTEXT.md` §8.
