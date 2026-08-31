# Legacy V1 — UART (Dedicated Pins) Framework User Manual

This is the **mini** reference for `auto_integ_legacy_v1_uart`: the V1
prototype that adds **UART3** wired straight to dedicated FPGA pins (not
through the pinmux — that's the sibling `auto_integ_legacy_v1_uart_pinmux`
framework; see §8 for how to tell them apart if you're not sure which clone
you're looking at). One peripheral, one static config, the same ~670-line
procedural engine shared with the other two legacy frameworks. For the current,
multi-peripheral, dynamically-composed V2 engine, see
`periph_auto_integration/docs/UserManual.md` instead — this file documents only
this frozen prototype.

`README.md` (this framework's root) is the setup-manager quick reference.
`docs/README_AUTOMATION.md` is the original PoC pitch doc, written for *this*
variant (not a copy of the RTC one — see §9). This file is the "how it actually
works" technical reference.

---

## 1. The big picture

```
soc_build_config.yaml          <- ONE static peripheral: uart_v1 (uart3, dedicated pins)
        │
        ├── ip_bsv/uart_v1/uart_v1.yaml   <- 23 patches: anchor + code + skip guard
        │
        ▼
scripts/peripheral_auto_integrator.py   <- ~670-line procedural engine (identical
        │                                   across all 3 legacy frameworks)
        ▼
boards/nexys_video/*   <- PATCHED: Soc.defines, uart_cluster.bsv, TbSoc.bsv,
                           fpga_top.v, constraints.xdc  (NOT Soc.bsv, NOT pinmux.bsv)
        │
        │  make sync_bsv_patches / sync_automated_files
        ▼
hw/ root copies   <- what bsc actually compiles
        │
        ▼
make generate_verilog  ->  build/  ->  make board_build  ->  bitstream
```

UART3 reuses the **existing** `devices/uart_v2/` IP (via a fourth `mkuart()`
instantiation inside `uart_cluster.bsv`) — this def ships **zero new BSV
source**, unlike `rtc_v1`. It patches `uart_cluster.bsv` (the cluster that
already hosts uart0-2), `TbSoc.bsv` (the testbench, which needs a fourth
`UserInterface` for simulation), `Soc.defines` (slave count + memory map), and
`fpga_top.v` (two new top-level ports, `uart3_SIN`/`uart3_SOUT`, wired straight
to `constraints.xdc` pins). It does **not** touch `Soc.bsv`'s pinmux interface
or `pinmux.bsv` at all — that is the entire distinction from the `_pinmux`
sibling.

---

## 2. Repository layout

```
auto_integ_legacy_v1_uart/
├── Makefile                  # setup manager (default CLONE_NAME: gc2025_autouart)
├── README.md
├── configs/
│   └── soc_build_config.yaml # the ONE static BOM
├── docs/
│   ├── README_AUTOMATION.md
│   └── UserManual.md         # this file
└── files/
    ├── Makefile               # hw/ root Makefile + automation targets
    ├── master_constraints.xdc
    ├── ip_bsv/
    │   ├── rtc_v1/             # inherited from the fork this framework started
    │   │                       # from — INTEGRATES NOTHING, deliberately unused,
    │   │                       # left in place only because removing it changes
    │   │                       # `status`'s resemblance scoring. Do not be misled
    │   │                       # into thinking this variant touches RTC.
    │   └── uart_v1/
    │       ├── uart_v1.yaml       # 23 patches
    │       └── uart_validate.sh   # pre/post anchor validation
    └── scripts/
        ├── peripheral_auto_integrator.py  # shared engine, see the RTC manual §3-§5
        └── track_pin_map.py
```

---

## 3. The patch set (23 patches, 4 files)

| # | File | What |
|---|---|---|
| 1 | `Soc.defines` | `UARTCluster_Num_Slaves` 4 → 5 |
| 2 | `Soc.defines` | Adds `UART3_slave_num`, bumps `UARTCluster_err_slave_num` |
| 3 | `Soc.defines` | Memory map: `UART3Base`/`UART3End` |
| 4-10 | `uart_cluster.bsv` | Interface decl, interrupt-width widen (`Bit#(3)`→`Bit#(4)`, twice — interface *and* implementation, since BSV needs both edited separately), `mkuart()` instantiation, address decoder arm, AXI connection, interface wiring, interrupt-return concat |
| 11-13 | `TbSoc.bsv` | Fourth `UserInterface` instantiation, TX loopback path, RX loopback path (simulation only — these don't affect synthesis) |
| 14-15 | `Soc.bsv` | `uart3_io` interface declaration + direct export (`interface uart3_io = uart_cluster.uart3_io;`) — this is the "dedicated pins" signature: UART3 gets its own top-level `Ifc_soc` port, unlike the pinmux variant where nothing new appears on `Soc.bsv` at all |
| 16-20 | `mixed_cluster.bsv` | External-interrupt bus width, PLIC input vector width, PLIC source count parameter, pinmuxtop peripheral-side interrupt width, gpio interrupt-width companion edit — **all plain hardcoded-number patches** (`Bit#(13)`→`Bit#(14)`, `35`→`36`), not the `plic_vector` special handler |
| 21-22 | `fpga_top.v` | Two new top-level ports (`uart3_SIN` in, `uart3_SOUT` out), wired to `mkSoc`'s `uart3_io_SIN/SOUT` ports |
| 23-24 | `constraints.xdc` | Two `xdc_pin_assign` patches (RX, TX), each with independent target/fallback pin + conflict resolution |

Note the PLIC widening here (#17-18 in the raw file numbering) is done with
**plain, hardcoded-value patches** (`35`→`36`), not `handle_plic_vector_patch`.
That is *why* this framework was never exposed to the idempotency bug described
in the RTC manual §5 — there is no dynamic cross-patch context dependency
anywhere in this def. Confirmed by grep: zero patches in `uart_v1.yaml` have a
`{placeholder}` inside `skip_if_contains`.

---

## 4. Phases and targets

Same Makefile target set as the RTC framework (`run_peripheral_bsv`,
`_verilog`, `_xdc`, the `_dry` variants, `run_peripheral_automation`,
`sync_bsv_patches`, `sync_automated_files`, `restore_peripheral_patches`,
`pre_validate_automation`/`post_validate_automation`, `quick_build_automated` =
`build_automated`) — see the RTC manual §4 for the exact composite order, it is
identical. The one difference worth calling out:

### `sync_bsv_patches` / `sync_automated_files` — this framework's own headline bug (fixed 2026-08-24)

Both sync targets originally copied only `Soc.bsv`, `Soc.defines`,
`mixed_cluster.bsv` (correct for the RTC fork this was forked from, wrong
here). This def *also* patches `uart_cluster.bsv` and `TbSoc.bsv`, neither of
which ever reached the `hw/` root — so `generate_verilog` compiled against a
stale `uart_cluster.bsv` that still declared `Bit#(3) uart_interrupts` while
`mixed_cluster.bsv` (which *had* synced) expected `Bit#(4)`, producing a
misleading cascade error naming `pwm_cluster` (a peripheral this framework
never touches — `bsc` was widening the nearest flexible term to make up a
missing bit). Fixed by adding `uart_cluster.bsv` and `TbSoc.bsv` to both sync
lists (`files/Makefile`). Verified 2026-08-24 and re-verified 2026-08-31:
`generate_verilog` now succeeds from the shipped flow with **no manual `cp`**.

The general rule this bug demonstrates: the set of files a def patches is
*derivable* from `anchors[*].file` in its yaml; hardcoding the sync list in a
Makefile is a guaranteed drift source the moment a def's file footprint
changes. See root `HANDOFF.md` §4 and §10 for the fix proposed for V2 (derive
`SYNC_BSV_FILES` from the defs at integration time) — not applied here, since
these three frameworks are frozen historical artifacts.

---

## 5. Idempotency guards (fixed 2026-08-24, unaffected by the 2026-08-31 fix)

Seven patches originally lacked `skip_if_contains` entirely (an anchor miss in
this engine is **fatal**, not skippable — it returns `False`, the transaction
never calls `mark_success()`, and `__exit__` rolls back every file in that
phase to the pristine baseline). Re-running `run_peripheral_automation` on a
half-applied tree left BSV reverted to stock while `constraints.xdc` and
`fpga_top.v` kept their patches — `fpga_top.v` then drove
`.uart3_io_SIN/SOUT` on a `mkSoc` that no longer exported `uart3_io` at all.
Guard coverage is now complete: 23 of 25 patches carry `skip_if_contains` (the
remaining two are the `xdc_pin_assign` pair, which don't use the field — see
the RTC manual §3).

**The 2026-08-31 engine fix** (RTC manual §5: populate
`context['new_plic_width']` on the `plic_vector` skip path; format
`skip_if_contains` through context before the substring check) was applied
here too, since `peripheral_auto_integrator.py` is byte-identical across all
three legacy frameworks and a real correctness bug in shared code should not be
left to diverge. **This framework was never actually exposed to either bug**
(no `plic_vector` patch, no templated `skip_if_contains` anywhere in
`uart_v1.yaml`), confirmed by re-testing after the fix: fresh integration →
`run_peripheral_automation` again → exit 0, every patch printed `Skipping`,
all five touched files md5-identical, `generate_verilog` compiled clean.

---

## 6. Recovery / restore

Identical mechanism to the RTC framework (RTC manual §6):
`restore_peripheral_patches` restores from `.automation_backup/`, explicitly
restores `bsvpath` if present, resyncs `pin_map.yaml`, deletes the backup.
Verified byte-clean (`diff -rq` against a pristine post-clone snapshot, exit 0)
on 2026-08-24 and again on 2026-08-31.

---

## 7. Verification recipes

```bash
make pre_validate_automation          # expect 18/18 PASS
make run_peripheral_automation_dry    # preview, no files touched
make run_peripheral_automation
make sync_bsv_patches
make generate_verilog -j              # success: build/hw/verilog/{mkSoc,mkDebugSoc}.v
make post_validate_automation         # expect 19/19 PASS
make run_peripheral_automation        # idempotency: expect all "Skipping", zero "Patched"
make build_automated                  # full flow to bitstream
make restore_peripheral_patches
```

**RTL-level confirmation** (source inspection alone left this ambiguous
historically — always check the compiled Verilog):
```bash
grep -c "mkuart " build/hw/verilog/mkuart_cluster.v   # expect 4 (was 3 in stock)
grep -c "uart3_io" build/hw/verilog/mkSoc.v            # expect >0: chip_io_uart3_io_SIN/SOUT/SOUT_EN
                                                        #   as genuine top-level mkSoc ports
```

**Full bitstream, verified 2026-08-31** (fresh clone, this machine, `bsc
2026.01`): synthesis 0 errors / 0 critical warnings, WNS +0.584 / TNS 0.000 /
WHS +0.016 / THS 0.000 (timing met), pin placement `uart3_SOUT`→`AA18`
(`ja[10]`), `uart3_SIN`→`AA20` (`ja[9]`) — with the XDC conflict-resolution
fallback logic visibly exercised end to end: `gpio_29` displaced to
`fmc_la_n[06]` (PULLDOWN property preserved through the move) and
`gptimer1_out` displaced to `fmc_la_p[06]` to make room.

---

## 8. Framework-specific gotchas

- **Directory names used to lie.** Before 2026-08-24,
  `auto_integ_legacy_v1_uart_nopinmux` held the *with*-pinmux def and this
  directory's old counterpart held the without-pinmux one, inverted — check
  what a def actually patches, never trust a directory name. This one
  (`_uart`, unchanged name) has always correctly held the without-pinmux def;
  only the sibling was renamed.
- **This framework ships an unused `ip_bsv/rtc_v1/`.** Inherited from the fork,
  integrates nothing, inflates `status`'s resemblance score against a real RTC
  clone slightly (part of why an RTC clone scores 7 against this framework's
  payload rather than 0). Left in place deliberately — see §2.
- **`update_bsvpath` applies "patch #1" whatever it is** — here that's the
  `Soc.defines` slave-count edit (patch #1), *not* a `bsvpath` entry. UART3
  needs no `bsvpath` line at all, since `uart_v2` is already wired into the
  build. Guarded and harmless either way, but don't read the target name
  literally.
- **`board_build`'s exit code lies** — same caveat as every framework here; grep
  the log, don't trust `$?`.
- **`bsc` needs `-cross-info`** (present through bsc 2026.01, removed in
  2026.07) — see root `HANDOFF.md` / `ubuntu_setup/RESTORATION_CONTEXT.md` §8.

---

## 9. Documentation history

Both `README.md` and `docs/README_AUTOMATION.md` were byte-identical copies of
the RTC framework's until 2026-08-24 (that is what "documentation not
complete" meant in earlier notes) — both are now written for this variant
specifically. `uart3_mode` in older notes/configs is the same field as
`uart_mode` used throughout the shipped config and template — the field *name*
is arbitrary per-author choice, only the *value* selects behavior, and V2's
`UserManual.md` was the one place still disagreeing (fixed).
