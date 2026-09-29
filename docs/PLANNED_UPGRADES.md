# Planned Upgrades — V2 Framework

**Live register.** Every deferred improvement to the engine, the Makefiles, the
defs or the docs lives here until it is done. Nothing is "remembered" anywhere
else: if an upgrade is not in this table, it is not planned.

**Audience:** the developer and the agent. Add a row the moment a gap is found;
move it to §5 when it ships.

---

## 1. Standing instruction — the test sequence is mandatory

> **Every upgrade in this register is verified with the full test ladder in
> `docs/README_NEW_PERIPHERAL.md` §7, Rungs 0–7, plus Phase 6 (generated-Verilog
> inspection) and — where the change can affect placement or timing — Phase 7
> (bitstream + logs).**
>
> This is not optional and not scaled down for "small" changes. An engine or
> Makefile change is *broader* in blast radius than a new peripheral, not
> narrower: it invalidates the verification of everything already integrated.

Scope of an upgrade's verification run:

| | Requirement |
|---|---|
| Peripherals | **All 8** — `rtc_v1`, `uart_v1` (both modes), `gpio_v1`, `i2c_v1`, `sspi_v1`, `pwm_v1`, `gptimer_v1`, `watchdog_v1` |
| Configs | **All 4** shipped configs in `configs/` (`soc_build_config1..4.yaml`) |
| Subsets | Each affected peripheral standalone, **and** the full BOM (ladder Rung 6) |
| Idempotency | Apply → re-apply byte-identical (Rung 3) |
| Restore | Tree returns to pristine byte-for-byte (Rung 4) |
| Compile | `generate_verilog` passes for every config above (Rung 5) — audits alone prove nothing |
| Structure | Generated Verilog inspected for the thing the upgrade claims to fix (Phase 6) |
| Record | Shared-state deltas (`PLICWidth`, `ExtIntWidth`/`Msb`, `*_Num_Slaves`, `*_err_slave_num`) unchanged vs. the pre-upgrade run, unless the upgrade is *about* them |

Any upgrade that touches `peripheral_auto_integrator.py` or anything under
`scripts/autointeg/` additionally follows the **sign-off
protocol** in `README_NEW_PERIPHERAL.md` §6.3 before a line is written.

---

## 2. Status legend

| Status | Meaning |
|---|---|
| `OPEN` | Agreed as worth doing, not started |
| `IN PROGRESS` | Being worked now |
| `BLOCKED` | Needs a decision or an external dependency (name it) |
| `VERIFYING` | Code written, working through §1's ladder |
| `DONE` | Shipped and verified — move to §5 |

---

## 3. The register

### UP-14 — `ifdef` support for conditional peripherals
**Status:** `DONE` (2026-09-24)  ·  **Tier:** engine  ·  **First consumer:** `random_v1`

A peripheral carrying `<criteria>_ifdef: true` in `soc_build_config.yaml` has
every patch it owns emitted inside `` `ifdef <criteria> `` / `` `endif ``, so the IP is
compiled in only for builds defining that macro. `simulate_ifdef: true` is the
first use: it keeps the simulation-only RNG out of synthesis.

Three behaviours, not one wrap:

| Patch kind | Behaviour |
|---|---|
| `regex` | The formatted block is wrapped. Directives sit 2 columns left of the block; a column-0 block (`.defines`) keeps directives at 0 and steps the body in — both match how the baseline guards `sign_dump`. |
| `cluster_config` | Not a wrap: the counter is **split** into `` `ifdef <criteria> `` *new* `` `else `` *old* `` `endif ``, so the other build keeps its value. Requires UP-13. |
| `xdc_pin_assign` | **Refused.** XDC is TCL and has no `` `ifdef ``; the engine fails with instructions rather than emitting an unguarded constraint. |
| `bsvpath` | Left unwrapped — a plain path list with no preprocessor; an entry nothing imports is never compiled. |

Wrapping is **per patch**, so existing skip guards keep working unchanged (the
text is still literally present) and re-runs stay idempotent.

**Ordering rule:** list ifdef-gated peripherals **last**. They split the cluster
counters into two branches; an unconditional peripheral applied afterwards would
need an `{allocated_slot}` valid in both. The engine detects that case and fails
with this instruction.

**Verified:** dry-run, apply, 3× idempotent, restore-to-pristine, both compile
paths (FPGA: `random` absent from `mkmixed_cluster.v`; simulate: `mkrandom.v`
generated), full BOM + random, and the 4-config A/B regression.

---

### UP-15 — `build_sim_automated`: the automation flow for simulation
**Status:** `DONE` 2026-09-24  ·  **Tier:** Makefile + one engine change

**Shipped as:** `quick_build_sim_automated` and `build_sim_automated` in
`files/Makefile`, plus `post_validate_automation_bsv`. The engine change is a
`phase` parameter on `run_post_audit()` (see "the one blocker" below), wired
from the CLI so `--post-audit --bsv` audits only the phases the sim flow ran.
Verified: post-audit at `phase='all'` byte-identical to the pre-change engine
across all 9 peripherals (199 lines of audit output); `--bsv` scoping drops
exactly the 25 `fpga_top.v` and 37 XDC checks and nothing else; on a BSV-only
tree the unscoped audit fails with 38 spurious errors and the scoped audit
passes with 0.

**The stock sim flow.** `build_sim` → `quick_build_sim` → `run_setup_simulate`
→ `generate_verilog -j` → `generate_boot_files_sim link_verilator`.

`run_setup_simulate` differs from `run_setup_build` only in which spec dir it
feeds `soc_config`: `sample_config/c64_<board>_sim/` instead of
`sample_config/c64_<board>/`. Diffing the two shows three changes, all in
`core64.yaml`:

```
compile_target: 'fpga'  ->  'sim'
top_module: mkDebugSoc  ->  mkTbSoc
top_file:   DebugSoc.bsv -> TbSoc.bsv
(plus trace_dump/trace true)
```

**`compile_target: sim` is what defines `simulate`.** `soc_config/configure.py:248`:

```python
if foo['bsc_compile_options']['compile_target'] == 'sim':
    macros += ' simulate'
```

So the sim path automatically defines the macro that `random_v1` (and the
baseline's `sign_dump` / bootrom) are gated on — an ifdef-gated peripheral needs
no special handling to appear in a sim build.

**What `build_sim_automated` would be.** Structurally `quick_build_automated`
with three substitutions:

| Step | FPGA flow | Sim flow |
|---|---|---|
| setup | `run_setup_build` | `run_setup_simulate` |
| patch phases | `--bsv`, `--verilog`, `--xdc` | **`--bsv` only** |
| sync | `sync_automated_files` | `sync_bsv_patches` |
| finish | `generate_boot_files ip_build board_build` | `generate_boot_files_sim link_verilator` |

**Why only the BSV phase.** Phase ownership is by target file
(`classify_patch_phase`): the `verilog` phase targets `fpga_top.v` and the `xdc`
phase targets `constraints.xdc`. The simulation top is `mkTbSoc`, which
instantiates `mkSoc` directly and never goes through `fpga_top.v` or a
constraints file. `TbSoc.bsv` — which sim *does* need, for the `uart_v1`
without-pinmux wiring and the `gptimer_v1` tie-offs — is already in
`SYNC_BSV_FILES`, so `sync_bsv_patches` covers it.

**Open problems to settle before writing it:**

1. **`post_validate_automation` will fail.** The post-audit checks every patch of
   every enabled peripheral, including the `fpga_top.v` and XDC ones that a sim
   build deliberately never applies. It needs a phase-scoped mode (audit only
   the phases that ran) or the target must skip it. This is the one real piece of
   engine work in the feature.
2. **`run_setup_simulate` is destructive**, same as `run_setup_build`: it
   `pip3 uninstall -r requirements.txt -y` then reinstalls, and does
   `cp -r boards/$(BOARD)/. .` — so the sync must come after it, exactly as the
   FPGA flow already handles.
3. **`mkdir build` without `-p`** in `run_setup_simulate` fails on a tree that
   already has `build/`. Pre-existing wart; the automated target should `rm -rf
   build` first or the mkdir should gain `-p`.
4. **Address-map divergence.** With an ifdef-gated peripheral present, the sim
   build has one more slave than the FPGA build (UP-14), so a sim-built
   `plic_inputs` / slave map is not the FPGA one. Firmware and the §25 tables
   must be read from the build being run.

**Acceptance criteria**
- [ ] `make BOARD=<b> build_sim_automated` runs the whole flow on a fresh clone
- [ ] `link_verilator` produces a simulation executable
- [ ] `random_v1` present in the sim build, absent in the FPGA build, from the same config
- [ ] Post-validation either phase-scoped or explicitly skipped, with the reason recorded
- [ ] §1 ladder on the FPGA flow still green (no regression to `quick_build_automated`)

---

### UP-01 — Replace `PLICVectorHandler` with a general concat-scoped audit
**Status:** `OPEN`  ·  **Tier:** engine  ·  **Closes:** `HANDOFF.md` §9.3

**The situation.** No V2 def has ever declared `type: "plic_vector"` — verified
across all commits. All eight defs insert discrete PLIC signals with the plain
regex handler at a dedicated marker anchor, guarded by `skip_if_contains`. The
handler is inherited from V1 (where all three legacy defs still use it) and was
superseded by the §21 derived-macro refactor: with `Bit#(\`PLICWidth)` in
`mixed_cluster.bsv` its width branch cannot fire, as its own code notes at
`peripheral_auto_integrator.py:1353`.

**What is worth salvaging.** Its `audit_post`
(`peripheral_auto_integrator.py:1366`) locates the concat block and checks the
signal is **inside that span**. The regex handler's post-audit is a substring
check over the whole file, so text that landed in the wrong place — or survives
only in a comment — still passes. That is exactly the §9.3 weakness: *an audit
that greps for a string cannot tell live code from a corpse.*

**The upgrade.** Give the regex handler an optional `audit_within: <anchor_name>`
so **any** patch can assert "my injected text landed inside *this* block",
generalising the one good idea to all ~170 patches instead of one handler.

**Then, and only then, delete the class.**

> **Removal condition (explicit).** `PLICVectorHandler` is removed from
> `peripheral_auto_integrator.py`, dropped from the `HANDLERS` registry
> (`:1714`), and struck from `UserManual.md` §3's handler table **only after**
> the `audit_within` upgrade is implemented, tested and verified against the
> **entire set of eight peripherals and all four shipped config lists** per §1.
> Until that verification is on record, the class stays — marked legacy, unused,
> but present. Do not delete it as a tidy-up.

**Acceptance criteria**
- [ ] `audit_within` implemented on the regex handler, defaulting to today's behaviour when absent
- [ ] At least the PLIC-insert patches in `rtc_v1`, `i2c_v1`, `gptimer_v1`, `watchdog_v1` migrated to use it
- [ ] A deliberately mis-anchored patch is **caught** by the new audit (negative test — prove it fails)
- [ ] §1 ladder green: 8 peripherals × 4 configs
- [ ] `PLICVectorHandler` deleted, registry entry removed, `UserManual.md` §3 updated
- [ ] `README_NEW_PERIPHERAL.md` Finding B removed (it becomes history, not a sharp edge)

---

### UP-02 — Seed the resource tracker with baseline address windows
**Status:** `OPEN`  ·  **Tier:** engine  ·  **Source:** `README_NEW_PERIPHERAL.md` §14 Finding A

`ResourceTracker.used_addresses` is populated only from declared peripherals
(`:566`); `ingest_static_state` (`:492`) reads `Soc.defines` but records macro
**names** only, never their ranges. A new window overlapping XADC
(`0004_1000..13FF`), Pinmux (`0004_1500..15FF`) or Eth (`0004_4000`) passes the
pre-audit silently.

**Upgrade:** parse `<X>Base`/`<X>End` pairs in `ingest_static_state` and reserve
them, so the existing overlap check covers the baseline map too.

**Acceptance criteria**
- [ ] Baseline windows reserved at ingest
- [ ] Negative test: a config with a deliberately colliding window **fails** pre-audit
- [ ] No false positives — all 4 shipped configs still pass clean
- [ ] Turns §11 hold-point #1 from "check by hand" into "the engine checks it"

---

### UP-03 — Duplicate `(anchor, guard)` lint per def
**Status:** `OPEN`  ·  **Tier:** lint  ·  **Closes:** `HANDOFF.md` §9.4

Two patches sharing both an anchor and a guard collide silently: the first
consumes the anchor, the second self-skips, nothing reports it. That is the
cell12 **and** cell13 bug class, and nothing prevents a recurrence. ~20 lines.

**Acceptance criteria**
- [ ] Lint fails on a def with a duplicated `(anchor_pattern, skip_if_contains)` pair
- [ ] All 8 shipped defs pass
- [ ] Wired into `pre_validate_automation` (or a `make lint_defs` target)

---

### UP-04 — YAML lint over the whole tree
**Status:** `OPEN`  ·  **Tier:** lint  ·  **Closes:** `HANDOFF.md` §9.8

`files/templates/pwm_v1.template.yaml` was invalid YAML from the initial commit
until it was caught by hand. Nothing parses the templates, so nothing noticed.
Two lines; parse every `**/*.yaml`.

---

### UP-05 — Derive `SYNC_BSV_FILES` from the defs
**Status:** `OPEN`  ·  **Tier:** Makefile  ·  **Closes:** `HANDOFF.md` §9.2

The sync list is maintained by hand in four places and is derivable from
`anchors[*].file`. This was V1's headline bug (a patched file that was never
copied to the `hw/` root, so the compiler never saw it) and the same hazard is
still latent in V2: adding a peripheral that patches a new board file requires
remembering to edit `files/Makefile`.

**Acceptance criteria**
- [ ] List emitted from the defs (or a generated `.mk` fragment the Makefile includes)
- [ ] Generated list matches today's hand-written one exactly for the full BOM
- [ ] Negative test: a def patching a new board file gets it synced with no Makefile edit

---

### UP-06 — Give `board_build` a real exit code
**Status:** `OPEN`  ·  **Tier:** Makefile  ·  **Closes:** `HANDOFF.md` §10 Tier-1 #4

Its second Vivado call ends in `|| (echo "ERROR: While running synthesis")` with
no `exit 1`, so the target reports success when synthesis fails. Anyone trusting
`$?` — including any automated flow — gets a false green. Until this ships,
bitstream success must be confirmed by artifact existence and timestamp
(`README_NEW_PERIPHERAL.md` §9).

---

### UP-07 — Pin the upstream `gc2025` commit and warn on drift
**Status:** `OPEN`  ·  **Tier:** engine  ·  **Closes:** `HANDOFF.md` §9.1

The whole system is regex-bound to upstream source text; nothing records which
upstream commit the anchors were written against. A user cloning a newer
`gc2025` gets anchor failures with no way to tell whether their tree drifted or
the def is wrong. Record the validated SHA in `soc_build_config.yaml` and have
`pre_validate` compare and warn.

---

### UP-08 — Emit a PLIC / address-map manifest for the SDK
**Status:** `OPEN`  ·  **Tier:** engine  ·  **Closes:** `HANDOFF.md` §9.5

Peripheral order in the config is part of the software ABI: reordering silently
renumbers PLIC source ids, the hardware still builds, and the failure lands in
firmware later. Emit the final assignment (JSON or a generated header) at commit
time and have GCSDK consume it instead of a hand-maintained table — `UserManual`
§25 records that the PLIC table "was wrong, and had been for a long time",
which is precisely this failure mode.

---

### UP-09 — Schema-validate the IP defs
**Status:** `OPEN`  ·  **Tier:** lint  ·  **Source:** `HANDOFF.md` §10 Tier-3 #10

A JSON Schema for `<ip>.yaml` would catch typos and missing keys at authoring
time rather than mid-integration. Natural companion to UP-03 and UP-04; worth
doing once the def schema stops moving.

---

### UP-10 — Multi-board verification
**Status:** `BLOCKED` — needs a second board and a bootstrap seed for it
**Tier:** verification  ·  **Source:** `HANDOFF.md` §10 Tier-3 #9

The engine is board-agnostic *by design* and `nexys_video`-proven *only*. That
claim is currently untested. Adding a board needs a
`files/scripts/bootstrap/<board>/bootstrap.yaml` seed and that board's pin map —
no engine or schema change, per the root `README.md`.

---

### UP-11 — Stale `pwm_v1_nopinmux` reference in the bootstrap seed
**Status:** `DONE` (2026-09-24) — removed with the bootstrap comment trim
**Was:** `OPEN`  ·  **Tier:** docs  ·  **Effort:** trivial

`files/scripts/bootstrap/nexys_video/bootstrap.yaml:61` injects a comment block
naming `pwm_v1_nopinmux`. The def has been `pwm_v1` since the mode unification.
It is seed text injected into `Soc.defines`, so the wrong name lands in every
integrated tree. Harmless functionally, misleading to read.

*(Carried over from `development/autointeg_verification_2026-08-24/HANDOFF.md`
before that folder was removed on 2026-09-23.)*

---

### UP-13 — `cluster_config` / `extract_define_value` read only the FIRST `define of a macro
**Status:** `DONE` (2026-09-24)  ·  **Tier:** engine  ·  **Found:** 2026-09-23, during clint reconnaissance

`extract_define_value` (`peripheral_auto_integrator.py:53`) uses `re.search`, so
it returns the **first** matching `` `define `` in the file, and the `replace`
anchors used by `cluster_config` counters rewrite that same first occurrence.

That is correct for every counter in use today — all the cluster counters
(`MixedCluster_Num_Slaves`, `SPICluster_err_slave_num`, `ExtIntWidth`, …) are
defined exactly once, unconditionally. It breaks for a macro defined **twice
under `` `ifdef ``**, which is how the top-level fast-fabric slave count is written:

```
`ifdef simulate
  `define Num_Fast_Slaves 7
`else
  `define Num_Fast_Slaves 6
`endif
```

A counter targeting `Num_Fast_Slaves` would read 7 (the simulate branch, which
appears first), rewrite only that line, and leave the `` `else `` value stale — so
FPGA builds would carry a slave count one short, silently. Note the top-level
*slave numbers* are mode-dependent too (`` `define Sign_slave_num 6 `` exists only
under `simulate`).

**Blocks:** any top-level fabric slave work — `tcm`, a second `bram`, an extra
ROM. Does not affect any of the 8 shipped peripherals.

**Acceptance criteria**
- [x] A counter can target a macro with multiple `` `ifdef ``-guarded definitions and update every branch, each from its own current value
- [x] `{allocated_slot}` ambiguity across disagreeing branches **fails loudly** with the fix instruction
- [x] All 8 peripherals × 4 configs byte-identical old-engine vs new-engine (A/B diff, 2026-09-24)

**How:** `find_define_sites()` walks the file tracking the `` `ifdef ``/`` `else ``/`` `endif `` stack and returns every site with its branch; `select_define_sites()` picks the ones a patch owns (an unconditional patch owns all branches, a gated patch owns only its own positive arm). `ClusterConfigHandler` reads and rewrites those sites instead of `re.search`'s first hit.

---

### UP-12 — Extract `status` to a shared script
**Status:** `OPEN`  ·  **Tier:** architecture  ·  **Source:** `HANDOFF.md` §6.3, §10 Tier-3 #8

~500 lines of duplicated Make shell across the four managers. The trade-off is
real and was consciously accepted: extracting it costs the legacy frameworks
their self-containment. Do this only if the managers start changing often.

---

## 4. Explicitly not planned

Recorded so they are not re-litigated each session.

| Item | Decision |
|---|---|
| Consolidating the three V1 legacy engines | **No.** They are frozen historical prototypes; duplication is the price of each archive directory staying independently runnable (`HANDOFF.md` §9.7). Fix a legacy defect only if it makes a framework unbuildable. |
| Removing unused `ip_bsv/rtc_v1/` from the UART legacy frameworks | **No** — inflates payload and resemblance scores, but touching frozen prototypes costs more than it saves (`HANDOFF.md` §10 #12). |
| Pinmux idle-high for `wruart3_rx`; `assign_wruart3_tx_on_cell13` body | **Deliberately unfixed**, documented in the legacy UART-pinmux docs. |

---

## 5. Completed

| ID | Upgrade | Shipped | Verified by |
|---|---|---|---|
| | | | |

| UP-11 | Bootstrap seed comment trim (32 → 18 injected lines; stale `pwm_v1_nopinmux` gone) | 2026-09-24 | code byte-identical ignoring comments, all 4 configs |
| UP-13 | Conditional-aware counter read/write | 2026-09-24 | 8 peripherals × 4 configs A/B byte-identical |
| UP-14 | `<criteria>_ifdef` conditional compilation | 2026-09-24 | both compile paths + full BOM + idempotency + restore |
| UP-15 | `build_sim_automated` + phase-scoped post-audit | 2026-09-24 | post-audit A/B identical at `phase=all`; sim flow to `mkTbSoc.v` + Verilator link |
| UP-16 | `--port` / `--smoke-test` removed as redundant | 2026-09-24 | no residual refs; all remaining CLI actions re-run clean |
| UP-17 | Audit `\`define` regexes made indentation-aware | 2026-09-24 | random_v1 post-audit 2 FAIL -> 0; non-ifdef configs unchanged |


---

## 6. Late additions (2026-09-24)

### UP-16 — `--port` / `--smoke-test` removed
**Status:** `DONE`

Deployment to a fresh clone is owned by the setup-manager Makefile
(`setup_full` / `setup_base` / `update_framework`), which clones the repo,
backs the stock Makefile up to `Makefile.orig`, deploys a *selectable* config
and initialises the pin map. `--port` duplicated that from a live `hw/` instead
of from the repo, and **appended** to the Makefile rather than replacing it —
so a `--port`ed clone had no `Makefile.orig` and `remove_automation` could not
scrub it back to stock. Removed: `autointeg/porting.py`, both CLI flags, the
README invocation.

If the "did the deployment actually work" check is wanted back, it belongs as a
`smoke_test` target in the setup-manager Makefile after `_setup_core`, not in
the engine.

### UP-17 — Audit `` `define `` regexes were indentation-blind
**Status:** `DONE`

`ClusterConfigHandler.audit_post` matched `` rf"^`define\s+{macro}" `` at column
zero. A `` `define `` inside an `` `ifdef `` branch is indented, so **every**
ifdef-gated peripheral failed post-audit with "Macro not found" even though the
macro was present and correct two lines above. Same bug class as the one fixed
in `find_define_sites` during UP-13; the sibling on the audit path was missed
because no shipped config was ifdef-gated at the time.

Fixed by allowing `^[ \t]*`. Found by running the real sim flow for `random_v1`
— a reminder that A/B inertness proofs across existing configs cannot catch a
bug that only the *new* capability reaches.
