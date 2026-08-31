# HANDOFF — Engineering Context and Review

**Written 2026-08-24 on `iriz-lap-pc`, for whoever (human or Claude) picks this
up next — in particular a fresh session on the lab PC with the files but none of
the conversation.**

This is **not** user documentation. `README.md` is the manual for the setup
manager; `docs/README_AUTOMATION.md` is the quick reference for the V2 engine;
`docs/UserManual.md` (2450+ lines) is the authority on how the engine actually
works and stays the place to look for patch schema, handler types, the
indentation system, and the per-peripheral walkthroughs. None of that is
repeated here.

What **is** here: the reasoning behind changes that the diff cannot show, an
honest technical review of the project, and the list of traps that cost real
time to find. Read §1–§3 to orient, §4–§6 if you are touching the legacy
frameworks, §8–§10 before proposing architectural changes, and §11 before you
touch anything at all.

---

## Table of contents

1. [Orientation](#1-orientation)
2. [Repository map](#2-repository-map)
3. [What changed in the 2026-08-24 session](#3-what-changed-in-the-2026-08-24-session)
4. [Deep dive — the sync work](#4-deep-dive--the-sync-work)
5. [Deep dive — the cell12 muxer fix](#5-deep-dive--the-cell12-muxer-fix)
6. [Deep dive — the status-target redesign](#6-deep-dive--the-status-target-redesign)
7. [Verification evidence](#7-verification-evidence)
8. [Review — what this project gets right](#8-review--what-this-project-gets-right)
9. [Review — structural risks](#9-review--structural-risks)
10. [Optimisation roadmap](#10-optimisation-roadmap)
11. [Pitfalls, traps and wrong assumptions](#11-pitfalls-traps-and-wrong-assumptions)
12. [Environment and toolchain](#12-environment-and-toolchain)
13. [Open items](#13-open-items)
14. [Update — 2026-08-31 session](#14-update--2026-08-31-session)

---

## 1. Orientation

This repository automates peripheral integration into the **Shakti GC2025**
RISC-V SoC. Instead of hand-editing BSV, Verilog and XDC to add a peripheral,
you declare it in YAML and a Python engine patches the RTL sources, address map,
PLIC routing, FPGA top-level ports and pin constraints — then the flow compiles
to Verilog and builds a bitstream to prove it worked.

There are **two generations**, and it matters constantly which one you are in:

- **V2 (current)** — the repository root. A modular engine
  (`files/scripts/peripheral_auto_integrator.py`, ~2300 lines) that composes
  **eight** peripherals in one run, with an in-memory VFS, atomic commit,
  dynamic resource allocation and a real indentation system. This is production.
- **V1 legacy (three archived prototypes)** — `archive/`. Procedural,
  hardcoded, **one peripheral each**, ~670-line engine. Kept as reference and
  for isolated testing. They are the "before" picture and are genuinely useful
  as a teaching artifact: nearly every V2 design decision is a direct response
  to something V1 got wrong.

The three legacy frameworks are `_rtc` (the original), `_uart` (UART3 on
dedicated pins) and `_uart_pinmux` (UART3 through the pinmux). The two UART ones
were forked from the RTC one and, until this session, had **never been verified
to build**.

---

## 2. Repository map

```
periph_auto_integration/
├── Makefile                  # V2 setup manager (clone, inject, status, remove, delete)
├── README.md                 # user-facing manual for the manager
├── HANDOFF.md                # this file
├── configs/                  # 4 ready-made V2 BOMs (soc_build_config1..4.yaml)
├── docs/
│   ├── README_AUTOMATION.md  # V2 quick reference (deployed into the clone)
│   └── UserManual.md         # V2 full reference — the authority
├── files/                    # the V2 payload injected into a clone's hw/
│   ├── Makefile              # automation-enabled hw/ Makefile
│   ├── master_constraints.xdc
│   ├── ip_bsv/               # 8 IP defs: rtc gpio gptimer i2c pwm sspi uart watchdog
│   ├── templates/            # per-peripheral config templates
│   └── scripts/              # engine, utils, indent_tools, xdc_analyzer,
│                             # peripheral_registry, bootstrap seeds, port/verify helpers
└── archive/
    ├── auto_integ_legacy_v1_rtc/          # RTC, dedicated pins (PMOD JA)
    ├── auto_integ_legacy_v1_uart/         # UART3, dedicated pins
    └── auto_integ_legacy_v1_uart_pinmux/  # UART3, via pinmux cells 12/13
```

Each legacy framework mirrors the manager layout *within its own directory*:
`Makefile`, `README.md`, `configs/soc_build_config.yaml` (one static BOM, no
`CONFIG_SELECT`), `docs/README_AUTOMATION.md`, `files/`.

**Note:** both UART frameworks still ship `files/ip_bsv/rtc_v1/` — inherited
from the RTC fork and **completely unused** by their configs. It is deliberately
left in place (removing it changes `status` resemblance scoring), but do not be
misled into thinking those frameworks integrate an RTC. They do not.

---

## 3. What changed in the 2026-08-24 session

Before this session both UART frameworks were **unbuildable** and neither had
ever been compiled. All of the following is applied and verified.

### 3.1 Directory rename

`auto_integ_legacy_v1_uart_nopinmux` → **`auto_integ_legacy_v1_uart_pinmux`**.

The two directory names were **the inverse of their contents**: the one named
`_uart` held the *without*-pinmux def, and the one named `_uart_nopinmux` held
the *with*-pinmux def. Confirmed from three independent signals — the IP def's
patch targets, the config keys (`uart_rx_cell`/`uart_tx_mux` vs
`uart_rx_board_pin`), and the post-validation checks.

The resolution keeps `_uart` (already correct for the without-pinmux variant)
and renames only the other. **Nothing on disk records this trap any more** —
hence this note.

### 3.2 Sync lists (the headline bug) — see [§4](#4-deep-dive--the-sync-work)

`sync_bsv_patches` and `sync_automated_files` copied only three files, inherited
from the RTC framework. Neither UART framework could reach synthesis.

### 3.3 Idempotency guards

Seven patches lacked `skip_if_contains` (four in `_uart`, three in
`_uart_pinmux`). In this V1 engine an anchor miss is **fatal, not skippable** —
it returns `False` inside the `with PatchTransaction(...)` block, `mark_success()`
never runs, and `__exit__` rolls **every** file back to the pristine baseline.

The observable symptom was ugly: re-running `make run_peripheral_automation`
exited 2 and left `_uart` in a **half-integrated, unbuildable state** — BSV
reverted to stock while `constraints.xdc` and `fpga_top.v` kept their UART3
patches, so `fpga_top.v` drove `.uart3_io_SIN/SOUT` on a `mkSoc` with no
`uart3_io`. Guard coverage is now complete: `_uart` 23 of 25 patches carry
`skip_if_contains` (the other two are `xdc_pin_assign`, whose handler has its
own idempotency and does not use the field), and `_uart_pinmux` 28 of 28.

### 3.4 The cell12 muxer fix — see [§5](#5-deep-dive--the-cell12-muxer-fix)

UART3 RX was dead in hardware in the pinmux variant. Builds fine; simply cannot
receive.

### 3.5 Validation scripts

- **pinmux post-checks** now assert the specific muxer arms. The old check
  (`grep -qF "uart3_rx is an input"`) passed even with the bug live, because the
  string was present — just in the wrong muxer.
- **`_uart` pre-checks** were the pinmux ones, copy-pasted. They checked
  `pinmux.bsv` cell12/13 anchors that this variant never patches, and passed
  because those anchors exist in a stock clone. Retargeted to the `TbSoc.bsv`
  and `fpga_top.v` anchors it actually consumes (20 checks → 18, all meaningful).

### 3.6 Manager homologation

All three managers defaulted `CLONE_NAME` to `gc2025_autortc` and carried a
`Legacy V1 framework (RTC)` banner. Now per-framework: `gc2025_autortc`,
`gc2025_autouart`, `gc2025_autouart_pinmux`, with matching banners.

### 3.7 Status targets — see [§6](#6-deep-dive--the-status-target-redesign)

All four rewritten. The V2 one had been pointing at `archive/auto_integ_legacy_v1`,
a path that no longer exists after the archive split.

### 3.8 Documentation

Both UART frameworks' `README.md` and `docs/README_AUTOMATION.md` were
**byte-identical copies of the RTC ones** — that is what "documentation not
complete" meant. Both now written for their own variant. Plus: RTC README
updated for the three-framework topology; root README legacy section rewritten;
stale paths fixed (`files/docs/` → `docs/`, `templates/` → `files/templates/`);
duplicate `uart_v1/` rows removed from `README_AUTOMATION.md` and `UserManual.md`;
a stale `_nopinmux` def reference corrected; and `uart3_mode` → `uart_mode`
throughout the docs (every shipped config and the template use `uart_mode`; only
`UserManual.md` disagreed).

### 3.9 Housekeeping

- `files/templates/pwm_v1.template.yaml` had a stray tab-only line making it
  **invalid YAML** since the initial commit. Fixed.
- Removed from both UART frameworks' `ip_bsv/uart_v1/`: `uart_v1.yaml.bak`
  (0 bytes), `uart.defines` (unreferenced — UART3 reuses `devices/uart_v2/`),
  `patch_dryrun_preview.txt` (stale capture). These were being copied into every
  clone.

---

## 4. Deep dive — the sync work

This is the part most worth understanding, because the same class of bug is
latent in V2.

### 4.1 What sync is for

The build has two copies of every board source:

```
boards/<board>/*.bsv        <- the PATCHED source of truth (what the engine edits)
        |
        |  make sync_bsv_patches      (cp board -> root)
        v
hw/*.bsv                    <- the COMPILE INPUTS (what bsc actually reads)
```

The engine patches `boards/<board>/`. The compiler reads the `hw/` root. A
`sync_*` target bridges them. **A def that patches a file absent from the sync
list compiles against a stale copy** — silently, because the patch itself
succeeded.

### 4.2 The bug

Both UART frameworks inherited the RTC framework's list verbatim:

```make
@cp -f boards/$(BOARD)/Soc.bsv .
@cp -f boards/$(BOARD)/Soc.defines .
@cp -f boards/$(BOARD)/mixed_cluster.bsv .
```

Correct for RTC, which patches exactly those three (plus `fpga_top.v` and the
XDC, handled by `sync_automated_files`). But the UART defs also patch
`uart_cluster.bsv` (both), `TbSoc.bsv` (`_uart`) and `pinmux.bsv`
(`_uart_pinmux`) — none of which ever reached the root.

### 4.3 The fix

`_uart`:
```make
@cp -f boards/$(BOARD)/uart_cluster.bsv .
@cp -f boards/$(BOARD)/TbSoc.bsv .
@touch Soc.bsv Soc.defines mixed_cluster.bsv uart_cluster.bsv TbSoc.bsv bsvpath 2>/dev/null || true
```

`_uart_pinmux`:
```make
@cp -f boards/$(BOARD)/uart_cluster.bsv .
@cp -f boards/$(BOARD)/pinmux.bsv .
@touch Soc.bsv Soc.defines mixed_cluster.bsv uart_cluster.bsv pinmux.bsv bsvpath 2>/dev/null || true
```

Applied to **both** `sync_bsv_patches` and `sync_automated_files`. The `touch`
matters: it forces make to see the copies as newer than the `.bo` artifacts.

### 4.4 How it presented

```
_uart:         Field `uart3_io' is not in the type `uart_cluster::Ifc_uart_cluster'
_uart_pinmux:  Field `uart3' is not in the type `pinmux::PeripheralSide'
both:          pwm_cluster.pwm0_sb_interrupt — Expected type Bit#(2), Inferred type Bit#(1)
```

The first two are obvious once you know the cause. **The third is a cascade and
badly misleading** — it names PWM, which neither framework touches.
`mixed_cluster` had been widened to want `Bit#(14)`, but the stale root
`uart_cluster.bsv` still returned `Bit#(3)` from `uart_interrupts`. The concat
feeding `mixed_cluster.interrupts(...)` was therefore one bit short, and `bsc`
tried to make up the difference by widening the nearest flexible term —
`pwm_cluster.pwm0_sb_interrupt` — to `Bit#(2)`.

**Lesson: BSV width-inference errors surface far from their cause.** Do not
start debugging at the file the error names.

### 4.5 Why the stock control mattered

Before concluding the automation was at fault, a **pristine clone was compiled**
(`run_setup_build` then `generate_verilog` with no patches): exit 0, clean. That
one control turned "three confusing errors" into "three errors definitely caused
by our changes" and made the rest of the diagnosis fast. **Always establish the
stock baseline before debugging an integration failure.**

### 4.6 The generalisable rule

The set of files a def patches is **derivable from the def itself** — it is
exactly the set of `anchors[*].file`. This snippet produced the authoritative
list in seconds:

```python
import yaml, glob
fs = set()
for f in glob.glob('files/ip_bsv/*/*.yaml'):
    d = yaml.safe_load(open(f)) or {}
    for a in (d.get('anchors') or {}).values():
        fs.add(a['file'].replace('{board_dir}/', ''))
print(sorted(fs))
```

Hardcoding that set in a Makefile is a guaranteed drift source. **V2 has the
same structural weakness**: `files/Makefile` defines

```make
SYNC_BSV_FILES := Soc.bsv Soc.defines mixed_cluster.bsv uart_cluster.bsv \
                  pinmux.bsv spi_cluster.bsv pwm_cluster.bsv TbSoc.bsv
SYNC_ALL_FILES := $(SYNC_BSV_FILES) constraints.xdc fpga_top.v
```

That list is **complete today** — verified against all eight defs. But it is
maintained by hand, so the first def that touches a new board file reintroduces
this exact bug. See [§10](#10-optimisation-roadmap) for the fix.

(`bsvpath` is deliberately excluded from the sync lists: it lives only at the
`hw/` root, the engine patches it in place, and it is tracked in the backup
manifest. That is documented behaviour, not an oversight.)

---

## 5. Deep dive — the cell12 muxer fix

### 5.1 The structure

Every pinmux IO cell has **two** muxers deciding what the physical pad does:

- `cellN_mux_out` — the value driven **onto** the pin
- `cellN_mux_outen` — the **direction** control

### 5.2 The convention (this is the trap)

Direction control reads backwards from intuition. From the working baseline
cells:

| Cell | Signal | `_mux_out` | `_mux_outen` |
|---|---|---|---|
| 7 | uart1_rx (input) | `val0` | **`val1`** |
| 8 | uart1_tx (output) | `wruart1_tx` | **`val0`** |
| 9 | uart2_rx (input) | `val0` | **`val1`** |
| 10 | uart2_tx (output) | `wruart2_tx` | **`val0`** |

**`val1` on `outen` means "the pad is an input". `val0` means "drive it".**
Effectively an active-low output enable.

### 5.3 The bug

Patches #13 and #14 were meant to hit cell12's two muxers separately. They were
**byte-identical** — same `anchor_pattern`, same `code`, same `skip_if_contains`:

```yaml
anchor_pattern: "wrcell12_mux==1\\?val0: // unused"
position: "replace"
code: "                        wrcell12_mux==1?val1: // uart3_rx is an input"
skip_if_contains: "uart3_rx is an input"
```

That anchor text occurs **twice** in `pinmux.bsv` — once in each muxer,
identical in both. Patch #13 replaced the **first** match (the *out* muxer).
Patch #14 then found its own guard already in the file and skipped.

Result: the value intended for `outen` landed in `out`, and `outen` was never
touched:

```verilog
// cell12 (uart3_rx) — BROKEN, as built
2'd1: iocell_side_io12_cell_out = 1'b1;                      // val1 in the wrong muxer
assign iocell_side_io12_cell_outen =
         mux_lines_cell12_mux_in == 2'd0 && ~...;            // = 1'b0 when mux==1
```

At `mux==1`, `outen=0` put the pad in **drive** mode and `out=1`, so the UART3
RX pin was held statically high **as an output**. The rule
`wruart3_rx <= cell12_mux_in` still existed, but the pad was not an input, so
nothing ever arrived. It compiled, synthesised, placed, routed and produced a
valid bitstream. It simply could not receive.

### 5.4 The fix

Each muxer now anchors on its **enclosing block** — including the preceding
`mux==0` arm, which differs between the two — and carries its own guard:

```yaml
# out muxer
anchor_pattern: "wrcell12_mux==0\\?wrgpioa_a5_out:\\s*\\n\\s*wrcell12_mux==1\\?val0: // unused"
skip_if_contains: "wrcell12_mux==1?val0: // uart3_rx is an input"

# outen muxer
anchor_pattern: "wrcell12_mux==0\\?wrgpioa_a5_outen: // bi-directional\\s*\\n\\s*wrcell12_mux==1\\?val0: // unused"
skip_if_contains: "wrcell12_mux==1?val1: // uart3_rx is an input"
```

Verified in the generated Verilog, now structurally identical to cell7:

```verilog
2'd1, 2'd3: iocell_side_io12_cell_out = 1'b0;   // bsc merged the two val0 arms
2'd1:       iocell_side_io12_cell_outen = 1'b1; // matches cell7 exactly
```

### 5.5 Cell13 had the same latent defect

Patches #16/#17 shared an anchor and guard identically. It was **benign only by
luck** — `val0` is the correct `outen` for a TX cell, so the arm that failed to
patch already held the right value; only the `// unused` comment was stale.
Re-anchored properly anyway, since leaving a known duplicate-anchor pair in
place is the defect class we were eliminating.

---

## 6. Deep dive — the status-target redesign

### 6.1 The problem

`status` used to be a two-way comparison: "does this clone look like V2 or like
*the* legacy framework?" With three legacy frameworks that question is
meaningless, and the V2 manager was still pointing at
`archive/auto_integ_legacy_v1` — a path deleted in the archive split.

### 6.2 The design

1. **Glob-discovered candidates.** V2 scores against `files/` plus every
   `archive/*/files`; each legacy manager scores against its own `files/`, V2 at
   `../../files`, and every sibling at `../*/files`. Adding a fourth legacy
   framework requires **no edit to any status target**.
2. **Scoring.** A byte-for-byte match count over `scripts ip_bsv templates
   Makefile master_constraints.xdc`, using `diff -wBq`. One `[CANDIDATE]` line
   is printed per framework, so you see the margin rather than just the verdict.
3. **Tie-break.** Necessary because the two UART frameworks share their *entire*
   `scripts/` directory. Ties resolve to whichever framework ships a
   `soc_build_config.yaml` matching the clone's active config; failing that, V2
   first, then alphabetical. This does fire in practice.
4. **Targeted diffing.** Config check, deep integrity scan and docs check all run
   against the **winner**, using its own `configs/` and `docs/`.
5. **Colour.** V2 manager: V2 green, legacy yellow (deprecation). Legacy
   managers: self green, V2 and siblings yellow.

### 6.3 Design trade-off recorded

The logic lives **inline in each Makefile** — four near-identical ~170-line
recipes. That was chosen deliberately so each legacy directory stays
self-contained and runnable if lifted out of the repo. If you decide
self-containment is not worth it, extracting to
`scripts/framework_status.sh <base-path>` would remove ~500 lines of duplicated
shell. See [§10](#10-optimisation-roadmap).

### 6.4 Verified

Full 4×3 matrix, every cell correct:

```
MGR\CLONE     v_uart            v_uart_pinmux     v_rtc
V2            uart              uart_pinmux       rtc
rtc           sib:uart          sib:uart_pinmux   SELF:rtc
uart          SELF:uart         sib:uart_pinmux   sib:rtc
uart_pinmux   sib:uart          SELF:uart_pinmux  sib:rtc
```

---

## 7. Verification evidence

Everything below was run on **fresh clones**, after all fixes.

| Check | `_uart` | `_uart_pinmux` |
|---|---|---|
| `setup_base` (clone → inject → track_pin_map) | pass, ~20 s | pass, ~20 s |
| `pre_validate_automation` | 18/18 | 20/20 |
| `run_peripheral_automation` | exit 0 | exit 0 |
| **Re-run (idempotency)** | exit 0, 0 rollbacks, md5-identical | exit 0, 0 rollbacks, md5-identical |
| `post_validate_automation` | 19/19 | 27/27 |
| `generate_verilog` **from shipped flow** | exit 0 | exit 0 |
| `restore_peripheral_patches` | byte-clean to stock | byte-clean to stock |
| Synthesis | 0 err, 0 crit warn | 0 err, 0 crit warn |
| Post-route WNS / TNS | **+0.370** / 0.000 | **+0.392** / 0.000 |
| WHS / THS | +0.013 / 0.000 | +0.051 / 0.000 |
| Bitstream + MCS | written | written |

**RTL-level confirmation** (not just source):

- `mkuart_cluster.v` instantiates **4** `mkuart` in both (was 3).
- `_uart`: `mkSoc` exports `chip_io_uart3_io_SIN/SOUT/SOUT_EN` as genuine
  top-level ports → `fpga_top.v` → pins.
- `_uart_pinmux`: `mkSoc` has **no** top-level uart3 ports by design;
  `mkpinmux.v` carries `peripheral_side_uart3_tx_put` / `_rx_get` and the
  renamed rules `uart3_rx_on_cell12` / `uart3_tx_on_cell13`.

**Pin placement** (`fpga_top_io_placed.rpt`, `_uart` only — the pinmux variant
allocates no new pins by design):

```
AA18  uart3_SOUT     OUTPUT  LVCMOS33  bank 14   (ja[10])
AA20  uart3_SIN      INPUT   LVCMOS33  bank 14   (ja[9])
N22   gptimer1_out   OUTPUT  LVCMOS12  bank 15   (displaced to fmc_la_p[06])
M22   gpio_29        BIDIR   LVCMOS12  bank 15   (displaced to fmc_la_n[06], PULLDOWN preserved)
```

The XDC conflict-resolution and fallback logic therefore works end-to-end
through to placement, and carries the `PULLDOWN` property with the relocated
port. No `NSTD-1`/`UCIO-1` unconstrained-IO DRCs in either build.

> **Scope limit, stated plainly:** all of this is `nexys_video` only. Both
> frameworks accept a `BOARD` argument but the V1 scripts assume that footprint.
> None of the above generalises to another board without re-verification.

---

## 8. Review — what this project gets right

I went looking for problems and still came away thinking the core design is
sound. The good ideas, in rough order of how much they matter:

**Derived interrupt-width macros are the keystone.** `ExtIntWidth`, `ExtIntMsb`,
`PLICWidth`, `PWMCluster_Channels`, `PWMChannelsMsb` in `Soc.defines` mean no IP
def ever names an absolute bit index. Each contributing peripheral *grows* the
macros through a `cluster_config` counter. That single decision is what makes
"any subset of peripherals, in any order" actually work, and it is a genuinely
non-obvious insight — BSV accepts neither arithmetic inside `Bit#(...)` nor in a
static slice bound, so the literals have to be generated. `UserManual.md` §21
tells the story of the two bugs that forced it; it is worth reading.

**YAML as the single source of truth.** Address map, wiring, interrupt routing
and pin export all derive from one BOM. Adding a peripheral needs zero Python.
That promise is actually kept — seven of the eight defs ship no BSV at all;
only `rtc_v1` introduces new RTL.

**`{allocated_slot}` dynamic slave numbering.** Slave numbers are allocated at
apply time from the current err-slot rather than hardcoded, so a def can sit
anywhere in the config list.

**V2's VFS + atomic commit.** Patches apply to an in-memory dict and flush only
after every patch succeeds. Compare V1, which writes files directly and relies
on a rollback in `__exit__` — a design that produced the half-integrated tree
described in §3.3.

**Comment-stripped guard probing.** V2 tests idempotency guards against
comment-stripped content. This came from a real bug: `spi_cluster.bsv` shipped a
commented-out `/*else if(addr>= \`SPI2Base ...*/` decoder arm, `sspi_v1`'s guard
matched the comment, SPI2's decoder arm was never injected, and SPI2 became an
unreachable slave — *and the post-audit passed*, because it grepped for the same
string and also found the comment. Excellent bug, excellent fix.

**The baseline-poisoning guard.** V2 refuses to create a backup baseline when a
run has nothing left to apply and no manifest exists, because that would record
*patched* files as pristine and make every future `--restore` a silent no-op.
Someone clearly got burned and then defended against it properly.

**Per-board bootstrap seeds.** `files/scripts/bootstrap/<board>/bootstrap.yaml`
injects the anchors a fresh clone lacks, discovered automatically from
`target_board`. Adding a board needs no engine or schema change.

**Centralised `INDENT_PROFILE`.** Patches name a semantic `indent_context`
(`module_body`, `rule_body`, …) and the engine looks up the width. Far better
than per-patch guessing — and the contrast with V1, which injects at column 0,
is stark.

---

## 9. Review — structural risks

Ranked by how much damage each can do.

### 9.1 Anchor fragility is the fundamental architectural bet

The entire system is regex-bound to the *text* of upstream `gc2025` sources. Any
upstream refactor — even reformatting — breaks anchors. `pre_validate_automation`
catches it loudly, which is good, but **nothing records which upstream commit
the anchors were written against**. A user cloning a newer `gc2025` gets anchor
failures with no way to know whether their tree drifted or the def is wrong.

*Mitigation:* record the validated `gc2025` commit SHA in
`soc_build_config.yaml`, and have `pre_validate` compare and warn on mismatch.
Cheap, and turns a confusing failure into a diagnosis.

### 9.2 Hardcoded sync lists

Covered in §4.6. Derivable from the defs; currently maintained by hand in four
places. This session's headline bug, and still latent in V2.

### 9.3 Validation shares its assumptions with the patcher

This is the subtle one. Post-checks `grep` for the same strings the patches
inject. That can verify *presence* but never *placement* — which is exactly why
cell12 passed 27/27 post-validation while being functionally dead. The validator
and the patcher fail together because they encode the same belief.

*Mitigation:* assert structure, not substrings. The strongest check available is
already in the flow and underused — **compile, then inspect the generated
Verilog**. `mkpinmux.v` settled the cell12 question in seconds when source
inspection had been ambiguous.

### 9.4 Guard uniqueness is unenforced

Two patches sharing both an anchor and a guard collide silently: the first
consumes the anchor, the second self-skips, and nothing reports it. That is the
cell12 *and* cell13 bug, and there is no reason it cannot recur.

*Mitigation:* a lint over each def that fails when two patches share
`(anchor_pattern, skip_if_contains)`. Twenty lines. Would have caught both.

### 9.5 PLIC bit positions are order-sensitive

Documented in `UserManual.md` §17, but worth restating as a *risk*: peripheral
order in `soc_build_config.yaml` is part of the software ABI. Reordering
silently renumbers PLIC source IDs, and the hardware still builds. The failure
lands in software, later, far away.

*Mitigation:* emit a generated PLIC manifest (JSON/header) at commit time
recording the final assignment, and have the SDK consume it rather than a
hand-maintained table. `UserManual.md` §25 notes the PLIC table "was wrong — and
had been for a long time", which is precisely this failure mode.

### 9.6 V1's cross-phase rollback is actively harmful

Within a phase the transaction is sound. Across phases it is not: BSV, Verilog
and XDC are separate engine invocations, so a failure in a later phase reverts
nothing while a failure in an *earlier* re-run reverts only BSV — leaving the
tree internally inconsistent and unbuildable. Guards now prevent the trigger,
but the hazard remains for anyone adding a patch without one.

### 9.7 Engine triplication across the legacy frameworks

`peripheral_auto_integrator.py` and `track_pin_map.py` are **byte-identical**
across all three legacy frameworks. A legacy engine bug must be fixed three
times, and nothing enforces that it is.

*Trade-off:* the current arrangement keeps each archive directory independently
runnable. My recommendation is to **keep the duplication** — these are frozen
prototypes whose value is being self-contained historical artifacts, and the
cost of divergence is low precisely because nobody should be developing them.
But it should be a conscious choice, and if you *do* start actively maintaining
them, consolidate to `archive/_v1_engine/` first.

### 9.8 No YAML lint

`files/templates/pwm_v1.template.yaml` was invalid YAML from the initial commit
until this session. Nothing parses the templates, so nothing noticed.

*Mitigation:* a two-line CI/pre-commit step parsing every `**/*.yaml`.

---

## 10. Optimisation roadmap

Ordered by value ÷ effort.

**Tier 1 — cheap, high value**

1. **YAML lint over the whole tree.** Two lines. Prevents §9.8 recurring.
2. **Duplicate-(anchor, guard) lint per def.** ~20 lines. Prevents §9.4, the
   cell12 class.
3. **Derive `SYNC_BSV_FILES` from the defs.** Emit it from
   `anchors[*].file` at integration time (or generate a `.mk` fragment the
   Makefile includes). Kills §9.2 permanently. The derivation is six lines and
   already written in §4.6.
4. **Give `board_build` a real exit code.** Its second Vivado call ends in
   `|| (echo "ERROR: While running synthesis")` with no `exit 1`, so the target
   reports success when synthesis fails. Anyone trusting `$?` gets a false green.

**Tier 2 — moderate effort, removes whole failure classes**

5. **Pin the upstream `gc2025` commit** and warn on drift in `pre_validate` (§9.1).
6. **Emit a PLIC/address-map manifest** at commit time for the SDK to consume (§9.5).
7. **Structural post-validation.** Extend `post_validate` to optionally compile
   and assert against the generated Verilog for the checks that matter (the
   pinmux direction arms being the obvious first case). Closes §9.3.

**Tier 3 — architectural, only if the project keeps growing**

8. **Extract `status` to a shared script** (§6.3) — removes ~500 lines of
   duplicated Make shell, at the cost of legacy self-containment.
9. **Multi-board verification.** Everything is `nexys_video`-proven only. The V2
   engine is genuinely board-agnostic by design; that claim is currently untested.
10. **Schema-validate the IP defs.** A JSON Schema for `<ip>.yaml` would catch
    typos and missing keys at authoring time rather than mid-integration.

**Legacy-specific**

11. Leave the three prototypes frozen. They are worth more as a clean historical
    record than as maintained code. If a defect is found, document it in that
    framework's README rather than diverging the engines — with the exception of
    defects that make a framework *unbuildable*, which is why this session fixed
    the sync lists and guards rather than merely noting them.
12. Consider removing the unused `ip_bsv/rtc_v1/` from both UART frameworks. It
    inflates the payload, ships into every clone, and inflates cross-framework
    resemblance scores (it is part of why the UART frameworks score 7 against an
    RTC clone). Left in place this session only because it was out of scope.

---

## 11. Pitfalls, traps and wrong assumptions

Everything below cost real time. Read this section before touching the project.

### About the codebase

- **Directory names lied.** `_uart` vs `_uart_nopinmux` were inverted. **Verify
  by content, not by name** — check what the def actually patches.
- **`board_build`'s exit code lies.** No `exit 1` on the synthesis call. Read
  `artybuild.log`; grep for `write_bitstream completed` / `Synthesis finished`.
- **V1 dry-run is not read-only.** It creates `.automation_backup/` while
  reporting "no files modified". It does not touch the *sources*, but the side
  effect surprises.
- **`update_bsvpath` is a misnomer in V1.** The bsvpath phase applies "patch #1"
  whatever it happens to be. The UART defs need no bsvpath entry (UART3 reuses
  `devices/uart_v2/`), so patch #1 is a `Soc.defines` edit. Guarded and
  harmless, but do not read the target name literally.
- **YAML comment numbering ≠ list index.** The defs have headers like
  `# === 22. ...` and even a `24b`, so "patch #23" in a comment is not
  `patches[22]`. Derive indices programmatically; I confused the two initially
  and reported the wrong patch numbers.
- **Both UART frameworks ship an unused `ip_bsv/rtc_v1/`.** Inherited from the
  fork. It integrates nothing.
- **BSV width-inference errors surface far from their cause.** See §4.4.

### About writing guards

- **The naive guard breaks the *first* run.** Several patches in one file all
  produce `Bit#(4) uart_interrupts`. Guarding them all on that string means
  patch A applies, then patch B sees the string and skips — on a *fresh* tree.
  **Each guard must match its own patch's unique output**, which usually means
  including surrounding context (`"USERSPACE) slave;\nmethod Bit#(4) uart_interrupts;"`)
  or distinctive leading whitespace.
- `skip_if_contains` is a plain substring test (`skip_str in content`) in V1;
  V2 adds `skip_if_matches` (regex) and probes comment-stripped content.
- **Never let two patches share an anchor *and* a guard.** §5.

### About the shell and Make

- **Process substitution `< <(...)` fails in Make recipes.** Make uses `/bin/sh`
  = dash on Ubuntu. Use a temp file.
- **Dash has no function scoping.** A helper function reusing a caller's
  variable name silently clobbers it. My `cfg_matches()` used `for c in ...`
  while the caller was mid-`while read -r n b c d s`, which corrupted
  `config_base` and produced a status report that contradicted itself. Prefix
  helper locals (`__c`, `__cdir`).
- **`cd` persists between tool calls in this harness.** I ran the RTC manager's
  `status` while believing I was running V2's, and misread the result. Use
  absolute paths or re-`cd` explicitly.

### About verification method

- **Always compile a stock clone first.** §4.5.
- **Verify at the generated-Verilog level, not the source.** Source inspection
  left cell12 ambiguous; `mkpinmux.v` settled it immediately.
- **Compare against a working reference in the same file.** cells 7/8/9/10 were
  the ground truth for what a correct UART cell looks like. Every pinmux
  conclusion was anchored to them rather than to reasoning from first
  principles — which would have got the inverted `outen` convention wrong.
- **A passing post-validation is weak evidence.** It greps for what the patch
  injects. 27/27 passed on a design that could not receive.

### About this machine

- `/tmp` is configured `D /tmp` in tmpfiles.d and is **emptied at every boot**.
  Never leave anything durable in a session scratchpad.

---

## 12. Environment and toolchain

Verified working on `iriz-lap-pc`:

| Tool | Path |
|---|---|
| `bsc`, `bluetcl` | `/opt/tools/bsc/latest/bin` |
| Vivado 2018.3 | `/opt/Xilinx/Vivado/2018.3/bin` |
| RISC-V GCC | `/opt/tools/riscv_multilib/bin` |
| `repomanager`, `soc_config` | pyenv 3.10.12 (**not** system python) |

**A fresh `gc2025` clone vendors none of its dependencies.** `c-class`,
`devices`, `fabrics`, `caches_mmu`, `csrbox`, `common_bsv` are all empty and
there is no `makefile.inc`. Only `make run_setup_build` populates them.

**`run_setup_build` has side effects worth knowing before you run it:**

- `pip3 uninstall -r requirements.txt -y` then `pip install -r requirements.txt`
  — churns the shared pyenv.
- `repomanager --yaml deps.yaml -cup` — fetches ~7 repos from GitLab (network).
- `soc_config ...` — generates `makefile.inc`.
- `cp -r boards/$(BOARD)/. .` — **overwrites hw/ root files with board copies**.
  This is why it must run *before* patching in the `quick_build_automated` order.

Rough timings on this laptop: clone + inject ≈ 20 s; `run_setup_build` ≈ 1–2 min;
`generate_verilog -j16` ≈ 30–70 s; `ip_build` + `board_build` to bitstream well
under an hour per clone.

Test clones used this session live in `~/Desktop/shakti-dev/workspace3/`
(`gc2025_v_uart`, `gc2025_v_uart_pinmux`, `gc2025_v_rtc`, plus older
`gc2025_autouart*` from before the fixes).

---

## 13. Open items

> **Superseded in part by §14.** This section is the 2026-08-24 session's
> record as written. §14 covers a follow-up session (2026-08-31, on
> `rise-OptiPlex-5070`) that fixed a real idempotency bug found by actually
> exercising the targets this section didn't get to, and gives the
> **current, complete** commit-split recommendation — read §14's git-state
> note instead of the one at the bottom of this section for what to commit.

### Deliberately not fixed

- **Pinmux idle-high.** The reference cells set `wruartN_rx <= val1` (idle-high)
  in every non-UART mux branch; `wruart3_rx` is not, so it defaults to the
  DWire's 0 — a constant break condition when cell12 is muxed away from uart3.
  Harmless in the operating configuration (uart3 is not in use then), but it
  does not match the reference. Three small patches would fix it.
- **`assign_wruart3_tx_on_cell13`** was renamed but its body still assigns
  `wrgpioa_a6_in<=val0`. Cosmetic for a TX cell.
- **`files/scripts/bootstrap/nexys_video/bootstrap.yaml`** still says
  `pwm_v1_nopinmux` in a comment inside an injected seed string. Left alone
  because it is *functional seed text*, not documentation — editing it changes
  what gets written into `Soc.defines` and risks the seeding logic.

### Git state — needs a decision

Nothing from this session is committed. Current state:

```
 M Makefile                          # status target rewrite
 M README.md                         # legacy section + path fixes
 M docs/README_AUTOMATION.md
 M docs/UserManual.md
 M files/templates/pwm_v1.template.yaml
 D archive/auto_integ_legacy_v1/**   # 12 files — the old pre-split directory
?? archive/auto_integ_legacy_v1_rtc/
?? archive/auto_integ_legacy_v1_uart/
?? archive/auto_integ_legacy_v1_uart_pinmux/
```

**The three archive directories are untracked**, which means every fix made to
the legacy frameworks is currently invisible to git — it will all land as one
bulk "add" unless staged deliberately.

Recommended three-commit split, requiring `git add -N` on the untracked
directories first so the fixes show up as reviewable diffs:

1. **Archive restructure** — the `auto_integ_legacy_v1/` → three-directory split
   and the `_nopinmux` → `_pinmux` rename.
2. **Legacy defect fixes** — sync lists, idempotency guards, cell12/cell13
   muxers, validate scripts, manager homologation, cruft removal.
3. **Status rework and documentation** — the four `status` targets, all READMEs,
   `docs/` corrections, the template YAML fix.

---

## 14. Update — 2026-08-31 session

**Written on `rise-OptiPlex-5070` (the lab PC), following on directly from the
2026-08-24 session above** — this is the first time these three legacy
frameworks were exercised end-to-end on a machine other than the laptop, and
the first time any of them was tested for idempotency across a *full* fresh
clone → integrate → re-run cycle rather than just the two sync/guard fixes
already covered in §3–§4.

### 14.1 What this session did

1. **Full re-verification, fresh clones, this machine.** All three legacy
   frameworks — `setup_base`, `status`, `update_framework`, `remove_automation`,
   `delete_clone` — tested to completion, plus the full 4×4 cross-compatibility
   status matrix (V2 + 3 legacy managers × 4 clones), reproducing §6.4's design
   exactly on fresh state. Then all three built to full bitstream
   (`build_automated`): synthesis 0 errors / 0 critical warnings on every
   clone, timing met on every clone (`_rtc` WNS +0.421, `_uart` WNS +0.584,
   `_uart_pinmux` WNS +0.492/WHS +0.051 — the last number matches this
   session's own §7 table exactly), the cell12 fix (§5) re-confirmed
   structurally identical to the cell7 reference in fresh generated Verilog,
   `_uart`'s 4×`mkuart` instantiation and AA18/AA20 placement re-confirmed.
2. **A real toolchain blocker, found and fixed, unrelated to this project.**
   `generate_verilog` failed machine-wide with `Unrecognized flag: -cross-info`
   — `bsc` here had drifted to an unpinned build (2026.07, four days old at the
   time) that removed a flag `gc2025`'s `soc_config` still hardcodes into every
   `makefile.inc`. Root-caused via `bsc`'s own git history (the flag was
   present continuously from 2021.07 through 2026.01; removed only in 2026.07),
   fixed by building bsc 2026.01 alongside the existing install and re-pointing
   `/opt/tools/bsc/latest`. Full writeup, including the systemic unpinned-clone
   risk across `auto_restore.sh`'s other seven source-built tools, is in
   `ubuntu_setup/RESTORATION_CONTEXT.md` §8 — not repeated here since it's an
   environment issue, not a defect in this project.
3. **A real, previously-undiagnosed idempotency bug in the shared engine —
   found, root-caused, and fixed.** See §14.2. This is the headline finding of
   this session, the same class of thing §3–§4 already are for the 2026-08-24
   session.
4. **Three new per-framework `docs/UserManual.md` files added**, one per
   legacy framework (`archive/auto_integ_legacy_v1_{rtc,uart,uart_pinmux}/docs/UserManual.md`).
   Each is the "how this specific frozen prototype actually works" reference —
   patch schema, phase order, idempotency model (including the §14.2 fix),
   restore mechanism, verification recipes, framework-specific gotchas — mini
   counterparts to the root `docs/UserManual.md`. Read the RTC one first; it
   carries the fullest writeup of §14.2, and the other two cross-reference it
   rather than repeating it.
5. **All test clones deleted** after verification (`gc2025_verify_{v2,rtc,uart,uart_pinmux}`
   in `workspace3/`, via each framework's own `delete_clone` target — itself
   further target coverage). Nothing from this session's testing is left on
   disk outside the framework source tree itself.

### 14.2 The idempotency bug: two root causes, one shared engine, RTC-only trigger

Re-running `run_peripheral_automation` on an **already-integrated** RTC clone
crashed:
```
KeyError: 'new_plic_width'
  peripheral_auto_integrator.py:581 (as it was), in execute_integration
```
Root cause A: `handle_plic_vector_patch` (the `plic_vector` special handler,
used only by `rtc_v1.yaml`'s patch #12) only set `context['new_plic_width']` on
its *apply* path. On a re-run its own guard correctly detects the signal
already present and returns early — before the key is ever set — and a
*different*, later patch (#13, the `mkplic` wrapper width) needs that key just
to evaluate its own guard, and crashed instead of skipping.

Fixing only that (making the skip path set the key to the *current*, not
`old_w + 1`, width) revealed a second, larger bug underneath: the generic
plain-patch path formats `code` through the peripheral's context
(`patch['code'].format(**ctx)`) on the same line it passes `skip_if_contains`
**raw** — unformatted — to the file-check helper. Any patch whose
`skip_if_contains` contains a `{placeholder}` therefore could never recognize
its own prior output and **reapplied silently on every re-run**, no error, no
crash — confirmed by a re-run duplicating the `RTCBase`/`RTCEnd` memory-map
block in `Soc.defines` in the tree after the first fix alone. Seven of
`rtc_v1.yaml`'s patches carry a templated `skip_if_contains` and were all
silently exposed to this.

Both are genuine bugs in `scripts/peripheral_auto_integrator.py`, now fixed:
the `plic_vector` skip path sets `new_plic_width` correctly, and the generic
patch path formats `skip_if_contains` through context before checking it — the
same pattern the codebase already used correctly for the `plic_vector` handler
and the XDC `additional_properties` injection, just missing from this one call
site. Full technical writeup, including the exact diff, is in the RTC
framework's new `docs/UserManual.md` §5 (§14.1.4 above).

**Scope: RTC-specific trigger, shared-engine fix.**
`peripheral_auto_integrator.py` is byte-identical across all three legacy
frameworks (§9.7), so the fix was applied to all three for consistency — but
neither UART framework was ever actually exposed to either bug. Neither
`uart_v1.yaml` (in either variant) has a `plic_vector`-typed patch (both widen
PLIC with plain hardcoded-value patches, `35`→`36`) or any templated
`skip_if_contains`, confirmed by grep and by re-running idempotency on both
after the fix: clean before, clean after, byte-for-byte no difference in
behaviour.

**A secondary observation, not chased further:** the crash from bug A (before
either fix), left mid-transaction, produced a tree that `restore_peripheral_patches`
could **not** fully undo on the first attempt — a duplicated `RTCBase`/`RTCEnd`
block survived a restore that reported success. This looks like the V1
backup/restore mechanism not correctly handling a *second* write to a file
whose backup already exists from a prior successful run (i.e. it restores to
"file existed in `.automation_backup/`" rather than re-diffing against current
content), which is a `PatchTransaction`-level question orthogonal to the two
fixes above. Recorded here because a future maintainer hitting a restore that
doesn't fully clean up should know this isn't unprecedented — but with both
root causes above fixed, the corrupting re-run that triggered it shouldn't be
reachable through normal use any more.

### 14.3 Verified state as of this session

Full 21/21 (RTC), 23/23 (`_uart`), 28/28 (`_uart_pinmux`) idempotent re-run —
every patch prints `Skipping`, zero prints `Patched`, every touched file comes
back md5-identical — confirmed on all three frameworks, immediately followed by
a clean `generate_verilog` (exit 0, no `bsc` errors, `mkSoc.v`/`mkDebugSoc.v`
produced) on all three. This is the first time any of the three legacy
frameworks has been confirmed idempotent-safe through an actual re-run rather
than by guard-coverage counting alone.

### 14.4 Git state — supersedes §13's note

Still nothing from either session committed. The working tree now also
contains (all inside the still-untracked `archive/auto_integ_legacy_v1_*/`
directories, so still invisible to git without `git add -N`):

- The §14.2 fix in all three copies of `peripheral_auto_integrator.py`.
- Three new files: `archive/auto_integ_legacy_v1_{rtc,uart,uart_pinmux}/docs/UserManual.md`.
- This section of `HANDOFF.md` and the ubuntu_setup toolchain note (separate
  repo — commit that independently).

**Updated recommended split** — the §13 three-commit plan is still the right
shape for the 2026-08-24 work; add a fourth on top for this session:

4. **Idempotency fix + per-framework manuals** — the `peripheral_auto_integrator.py`
   fix (all three copies) and the three new `docs/UserManual.md` files, plus
   this `HANDOFF.md` section.

Same caveat as §13: `git add -N` on the three archive directories first, or
this lands as one bulk add with no reviewable diff for either session's work.

