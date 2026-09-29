# README — Integrating a New Peripheral into the V2 Framework

**Audience:** the developer and the coding agent, working together. This is a
working document — refine it as each new peripheral teaches something.

**This is not a tutorial for the framework.** It assumes `docs/UserManual.md` is
the reference and cites it rather than restating it. What this document adds is
the **procedure**: the order of operations, the decisions that belong to the
human, the test ladder that proves an integration, and the hard rule about when
Python may be touched.

| Document | Read it for |
|---|---|
| `docs/UserManual.md` | The authoritative reference. Mechanism, schema, every peripheral's story, every bug that shaped the design. |
| `docs/README_AUTOMATION.md` | Starter orientation inside a clone. |
| `README.md` (repo root) | The **setup manager** — cloning, injecting, `status`, removal. |
| `HANDOFF.md` §9–§10 | Known structural risks and the optimisation roadmap. Read before proposing any engine change. |
| `docs/PLANNED_UPGRADES.md` | The live upgrade register. Check it before proposing an engine change — your gap may already be queued. Every upgrade is verified with §7's ladder. |
| **this file** | How to actually add peripheral #9 and prove it works. |

---

## 0. How to use this document

Work the phases in order. Each phase ends in a verifiable state; do not start
the next until the current one is green.

```
Phase 0  Reconnaissance        -> a filled-in answer sheet, user-approved
Phase 1  Pick the archetype    -> the def you will copy
Phase 2  Write ip_bsv/<name>/<name>.yaml
Phase 3  Write the config item + files/templates/<name>.template.yaml
Phase 4  The Python gate       -> almost always "no Python", and prove it
Phase 5  The test ladder       -> rungs 0..7, each gates the next
Phase 6  Read the generated Verilog
Phase 7  Bitstream + log analysis
Phase 8  Hand off to software (GCSDK)
```

**Agent protocol.** Anything in a **HOLD** box is a decision the agent must not
make alone. Stop, state the options and the recommendation, and wait. §11
collects every hold point in one table. Everything else the agent should do
autonomously and report.

---

## 1. What the eight existing integrations teach

Eight peripherals are integrated, and between them they cover every shape a new
one can take. Find the row your peripheral resembles — that def is your starting
point.

| Def | Shape | What it proves / why you'd copy it |
|---|---|---|
| `gptimer_v1` | **Extend an existing family.** New slave beside gptimer0–3, discrete PLIC source, plain in/out pins, no new BSV, no bsvpath entry. | The cleanest, most complete reference. Copy this unless you have a reason not to. Its header comment is a model for documenting independence + idempotency. |
| `watchdog_v1` | **Reuse RTL that was wired into nothing.** Adds a `bsvpath` entry, an import, and a `(*synthesize*)` wrapper. | The pattern when the IP exists in `devices/` but has never been instantiated. Also the reset-domain trap (UserManual §20). |
| `rtc_v1` | **New RTL shipped with the def** (`rtc_v1.bsv`, `rtc.defines`), bidirectional pad with `IOBUF`, two instances, **and** the only user of a Python context generator. | Copy for the IOBUF/pad pattern. Do **not** copy its context-generator dependency — see §6. |
| `i2c_v1` | **Two more of an existing controller**, mixed-cluster slaves, discrete PLIC sources. | Owns the `MixedClusterEnd` window bump (UserManual §24). The reference for "my address is above every existing slave in my cluster". |
| `sspi_v1` | **A different cluster** (SPI, not Mixed), interrupts on the **shared bus**, own cluster slave pool. | The reference for a non-Mixed cluster, and for a bus-bit (not discrete) interrupt. Its standalone-compile bug is the cautionary tale of UserManual §21. |
| `uart_v1` | **One def, two routing modes** (`with_pinmux` / `without_pinmux`), selected per instance. Patches `pinmux.bsv` in one mode, `fpga_top.v` + `TbSoc.bsv` in the other. | Copy when your peripheral can reach the pins two different ways. Owns the `UARTClusterEnd` bump. |
| `pwm_v1` | **Widen an existing slave instead of adding one.** Channels 6→10 on the single `mkpwm`. Sits at the **LSB** end of the interrupt bus. | Copy when the hardware is already there and only a parameter grows. Proves the derived-width macro system. |
| `gpio_v1` | **Widen an on-chip interface** 32→48 lines. No new slave, no PLIC, no cluster counter. | The cheapest shape: touches almost no shared state. Also the 64-bit register consequence in software (UserManual §25). |

**The two axes that actually matter** when choosing:

1. *Do you add a slave, or widen something that exists?* Adding a slave means
   address window + cluster slave counter + decoder arm + cluster-end check.
   Widening means a macro counter and nothing else.
2. *Does it interrupt, and how?* Discrete PLIC source (rtc, i2c, gptimer,
   watchdog) → insert a signal into `plic_inputs` + `PLICWidth` +N. Shared-bus
   bit (uart, sspi, pwm) → grow `ExtIntWidth`/`ExtIntMsb` too, and insert into
   `connect_interrupt_lines`. No interrupt (gpio) → touch nothing.

---

## 2. Phase 0 — Reconnaissance

Answer every question below **before** writing YAML. Most answers come from
reading the pristine board sources in `boards/<board>/`; the rest are the
developer's to decide.

### 2.1 The IP itself

| # | Question | Where the answer comes from |
|---|---|---|
| 1 | Does the RTL already exist in `devices/`? | `ls devices/` in the clone |
| 2 | Is it already on the `bsvpath`? | `cat hw/bsvpath` — if not, you need a bsvpath patch (`watchdog_v1`) |
| 3 | Is there already a `(*synthesize*)` wrapper module for it? | grep the cluster `.bsv` — if not, you inject one (`watchdog_v1`) |
| 4 | Does the def need to ship its own `.bsv` / `.defines`? | Only if the RTL does not exist at all (`rtc_v1`) |
| 5 | What is the interface type, and does it have `(*always_enabled*)` methods? | The IP's `.bsv`. Always-enabled inputs **must** be tied off in `TbSoc.bsv` or `bsc` rejects `mkTbSoc` (`gptimer_v1`) |

### 2.2 Placement in the SoC

| # | Question | Notes |
|---|---|---|
| 6 | Which cluster does it belong to? | Mixed / UART / SPI / PWM. Decides which `*_Num_Slaves`, `*_err_slave_num` and `*ClusterEnd` you touch. |
| 7 | Address window? | **HOLD** — see below. |
| 8 | Is that window above every existing slave in the cluster? | If yes you **must** also bump `<Cluster>End`. UserManual §24. This is the silent killer. |
| 9 | Slave slot number? | You never choose one. Use `{allocated_slot}`. UserManual §17 Rule 1. |
| 10 | Interrupt: discrete PLIC source, shared-bus bit, or none? | §1 above. Decides which macros you grow. |
| 11 | Pins: how many, which, what IO standard? | **HOLD** — see below. |
| 12 | Does the pin need an `IOBUF` (bidirectional pad) or is it a plain in/out? | Mirror whatever the neighbouring family does. gptimer = plain; rtc/pwm/gpio/i2c = IOBUF. |
| 13 | Does it need more than one routing mode? | Only if there's a genuine second path (pinmux vs dedicated). Don't invent modes. |

> **HOLD — address window.** The agent proposes a free window (first unused
> 256-byte slot above the cluster's current highest slave) and states which
> existing macros bracket it. The developer confirms. **The engine will not catch
> a collision with a baseline slave** — see §14 Finding A.

> **HOLD — pin selection and IO standard.** Pins are physical, bank-voltage
> dependent, and may already be claimed as another peripheral's *fallback*. The
> agent proposes from `boards/<board>/pin_map.yaml` and
> `files/master_constraints.xdc`, showing which pins are free as primary *and*
> fallback. The developer confirms. On nexys_video the FMC-LA bank is **1.2 V
> (VADJ)** → `LVCMOS12`; getting this wrong is a hardware risk, not a build error.

### 2.3 Shared-state budget

Write down, explicitly, the delta this peripheral applies to every shared
counter. This one table is what makes the peripheral independent (UserManual
§17) and it is what you check against after the run:

```
PLICWidth              +N   (N = number of PLIC-routed instances)
ExtIntWidth / ExtIntMsb +M  (M = bus bits, 0 for discrete sources)
PWMCluster_Channels     +0  (unless you widen PWM)
<Cluster>_Num_Slaves    +K  (K = number of slaves added)
<Cluster>_err_slave_num +K
<Cluster>End            bumped? yes/no -> to what
```

---

## 3. Phase 1 — Pick the archetype and copy a def

```bash
cp -r files/ip_bsv/gptimer_v1 files/ip_bsv/<name>_v1
mv files/ip_bsv/<name>_v1/gptimer_v1.yaml files/ip_bsv/<name>_v1/<name>_v1.yaml
```

Then rewrite the header `description:` **first**, not last. The shipped defs put
the whole design argument in that block — address rationale, independence proof,
idempotency proof, electricals. Writing it first forces the Phase 0 answers to be
consistent; if you cannot write the independence paragraph, the design is not
ready.

---

## 4. Phase 2 — Structuring the peripheral YAML

### 4.1 File skeleton

```yaml
name: "<name>_v1"
yaml_version: "2.0"
description: >
  WHAT it adds, WHERE (address + cluster), HOW it connects (pins, IOBUF or not),
  ADDRESS MAP rationale, INDEPENDENCE proof, IDEMPOTENCY proof, ELECTRICALS.

anchors:        # named regex insertion points, instance-agnostic
patches:        # code to inject, referencing an anchor

# ANCHOR REFERENCE comment block: every pre-audit anchor and post-audit
# expectation, listed. Keep it accurate — it is the review checklist.
```

### 4.2 Anchor schema

| Key | Meaning |
|---|---|
| `file` | Target path, templated from **base_context** (e.g. `{board_dir}/Soc.defines`, `{xdc_file}`). Instance fields are **not** available here. |
| `pattern` | Python regex, matched with `re.MULTILINE`. |
| `position` | `after` \| `before` \| `replace`. |
| `multiline` | `true` adds `re.DOTALL` for block patterns. |
| `type` | Only on the XDC anchor (`xdc_pin_assign`). |

**Anchor ownership (UserManual §6).** One anchor, one owner. If you extend a
family, anchor on that family's last member (`output gptimer3_out,`). If you do
not, anchor on the dedicated marker comment in `fpga_top.v`
(`// ---- direct-connected peripheral ports (added by automation) ----`). Do not
pile a fifth def onto an anchor three others already use — the output ordering
becomes meaningless.

**Never `replace` a shared structure.** `connect_interrupt_lines` is written
one-signal-per-line specifically so IPs **insert** into it. Two IPs that both
`replace` it will silently clobber each other (UserManual §17 Rule 2b).

### 4.3 Patch schema

| Key | Meaning |
|---|---|
| `anchor_ref` | Name of the anchor. |
| `code` | Text to inject. `{placeholders}` resolve from base_context + instance. |
| `skip_if_contains` | Literal idempotency guard, template-resolved, whitespace-sensitive. |
| `skip_if_matches` | Regex guard, for when a literal can't be made unique. |
| `apply_once` | Patch emits one block regardless of instance count (counters, width declarations). |
| `instance_aware` | Patch emits once **per instance**. |
| `reverse_instances` | Emit this patch's instances in reversed config order — for MSB-first concatenations. |
| `indent_context` | Names the base indent from `INDENT_PROFILE` (UserManual §5). Required for every BSV patch. |
| `blank_line_before` | Cosmetic separation in the output. |
| `mode` | Restricts the patch to one routing mode; untagged patches are the shared core. |
| `plic_gated` | Patch applies only to instances with `connect_to_plic: true`. Use it on **both** the PLIC signal patch and its width counter. |
| `type` | `cluster_config` \| `xdc_pin_assign`; omit for the default regex handler. |

**`cluster_config` extras:** `target_macro` (the macro to rewrite),
`increment_by_instances: true`, and `{calculated_val}` in the code.
`{allocated_slot}` is published onto the instance by the `err_slave_num` counter
patch — so any patch using `{allocated_slot}` must be listed **after** it.

**`xdc_pin_assign` extras:** `port_name`, `target_board_pin`, `fallback_pin`,
`io_standard` (all template-resolved), plus a `skip_if_contains` on
`get_ports {<port>}`.

### 4.4 Guard rules — the ones that have actually caused bugs

1. **Key the guard on the code you inject**, never on a trailing comment.
   Guards are matched against a **comment-stripped** copy of the file, so a
   guard keyed on comment text can never match and the patch re-applies every
   run (UserManual §7).
2. **Never build a guard from `{calculated_val}`** — it is only known at apply
   time, can never match, and the counter grows on every run. Guard on a
   per-instance macro the same run defines (`` `define {slave_id_macro} ``).
3. **A guard must be unique to its own patch's output.** Two patches sharing an
   anchor *and* a guard collide silently: the first consumes the anchor, the
   second self-skips, nothing reports it. That is the cell12/cell13 class of bug
   (HANDOFF §9.4). There is still no lint for this — check by hand.
4. **A `replace` patch must re-emit everything its pattern consumed**, and its
   guard must key on the *post* value so a re-apply is a no-op (UserManual §24).
5. **Dead code in the baseline is a trap.** A commented-out `` `define SPI2Base ``
   once suppressed a real patch and produced a slave nothing decoded to. The fix
   is to delete the corpse from the board source via the bootstrap seed — not to
   make the guard cleverer. **HOLD**: seed edits change the pristine tree for
   every peripheral; get sign-off.

### 4.5 Patch ordering inside the file

Order matters in exactly three places; everything else is free:

1. The `PLICWidth` counter is listed **first**, guarded on a macro a *later*
   patch writes (`` `define {base_macro} ``), so on a first run it still sees a
   pristine `Soc.defines`.
2. `err_slave_num` (which computes `{allocated_slot}`) must precede any patch
   that uses `{allocated_slot}`.
3. Patches that share an anchor emit in **reverse apply order** — last applied
   ends up on top. If two of your own patches share an anchor, order them with
   that in mind.

### 4.6 The per-file checklist

Walk this list and decide *touch / don't touch* for each. Every existing def is
a subset of it.

| File | What goes in | Skip it when |
|---|---|---|
| `bsvpath` | `devices/<ip>` path entry | The IP is already on the path |
| `Soc.defines` | `<IP>Base`/`<IP>End`, `<IP>_slave_num` (next to its family), `<Cluster>_Num_Slaves`, `<Cluster>_err_slave_num`, `PLICWidth`, `ExtIntWidth`/`ExtIntMsb`, `<Cluster>End` bump | You add no slave and no interrupt |
| `<cluster>.bsv` | import, `(*synthesize*)` wrapper, instantiation, interface decl + wire, decoder arm, AXI `mkConnection`, PLIC concat insert | — |
| `Soc.bsv` | `chip_io` interface decl + binding, pinmux routing, interrupt concat insert | Nothing reaches the chip boundary |
| `TbSoc.bsv` | Tie-offs for `(*always_enabled*)` inputs now on `chip_io` | No always-enabled inputs |
| `fpga_top.v` | Port decls, wires, SoC port map, `IOBUF` | Routed through the pinmux only |
| `constraints.xdc` | `xdc_pin_assign` per port | Routed through the pinmux only |
| `pinmux.bsv` | Cell mux arms + rules | Not pinmux-routed |

---

## 5. Phase 3 — The config item and the template

Two artefacts, same content, different jobs:

* **`files/templates/<name>_v1.template.yaml`** — the shipped, fully commented
  item a user copies into their own config. One comment per field. Include a
  commented-out **second instance** showing exactly what changes for instance
  #2 (own window, own pins, own PLIC index). Every shipped template does this;
  it is the fastest way for a reader to learn what is per-instance.
* **the entry in `configs/soc_build_configN.yaml`** — the same block, minus the
  teaching comments, in a config you will actually test.

### 5.1 The item shape

```yaml
- name: <name>_v1                      # must match ip_bsv/<name>_v1/
  def_path: ip_bsv/<name>_v1/<name>_v1.yaml
  # ...or, for a multi-mode peripheral:
  # modes:
  #   <mode_a>: ip_bsv/<name>_v1/<name>_v1.yaml
  #   <mode_b>: ip_bsv/<name>_v1/<name>_v1.yaml

  base_context:                        # shared by every instance
    xdc_file: '{board_dir}/constraints.xdc'   # REQUIRED if you assign pins:
                                              # anchor file paths resolve from
                                              # base_context, never the instance
    <name>_io_standard: LVCMOS12

  instances:
  - instance_id: '0'                   # names everything: <ip>0, <ip>0_io, ...
    slave_id_macro: <IP>0_slave_num
    base_macro: <IP>0Base
    base_addr: '0004_0A00'
    end_macro: <IP>0End
    end_addr: '0004_0AFF'
    connect_to_plic: true
    plic_index: 49                     # uniqueness bookkeeping — NOT the real id
    <name>_pin: fmc_la_p[06]
    <name>_fallback: fmc_la_n[06]
```

### 5.2 base_context vs instance — the rule

Anchor **file paths** are resolved with base_context *before* instances are
expanded. So anything appearing in an anchor's `file:` (i.e. `xdc_file`) must
live in `base_context`. Per-instance data that only appears in patch **code**
(pins, addresses, modes) lives in the instance.

### 5.3 Modes

`modes:` maps a value → def_path. The active mode is discovered by scanning
instance fields for one whose **value** names a `modes` key — the field name is
arbitrary (`uart_mode` by convention). All instances must agree. A config using
a plain `def_path` declares no mode, and the def's own `default_mode:` decides
which tagged patches run. Only add modes when there is a genuine second routing
path.

### 5.4 What `plic_index` actually does

It is **bookkeeping**: the pre-audit uses it to detect two instances claiming
the same index, and a def may use it to order signals *within its own batch*.
It does **not** set the PLIC source id. The real id follows from the signal's
position in the `plic_inputs` concatenation, which depends on the **order
peripherals are listed in the config** (UserManual §17).

> **HOLD — peripheral order in the config is part of the software ABI.**
> Inserting a new peripheral above existing ones renumbers their PLIC source ids.
> The build still succeeds and every audit still passes; firmware breaks later,
> silently. **Append new peripherals at the end of `automated_peripherals`**
> unless the developer explicitly decides otherwise, and re-check the generated
> `plic_inputs` line either way.

---

## 6. Phase 4 — The Python gate

**Seven of the eight shipped peripherals required zero Python changes.** The only
exception is `rtc_v1`, whose `autointeg/registry.py` context generator
auto-derives naming conventions — and even that is optional sugar: every other
def writes the same names explicitly in YAML. Treat "no Python" as the expected
outcome, not the lucky one.

### 6.1 Before claiming Python is needed, prove all of these are false

- [ ] The injection is a plain insert/replace at a regex anchor → `regex` handler.
- [ ] It's a counter in `Soc.defines` → `cluster_config` + `increment_by_instances`.
- [ ] It's a pin constraint → `xdc_pin_assign`.
- [ ] It's conditional on interrupt routing → `plic_gated`.
- [ ] It's one of two routing paths → `modes` + `mode:` on the patches.
- [ ] It's per-instance vs once → `instance_aware` / `apply_once`.
- [ ] It's an MSB-first concat → `reverse_instances`.
- [ ] The anchor doesn't exist in a pristine tree → **add a bootstrap seed**
      (`files/scripts/bootstrap/<board>/bootstrap.yaml`), not engine code.
- [ ] The value is only known at apply time → it's already published onto the
      instance (`{allocated_slot}`, `{calculated_val}`).
- [ ] A naming convention repeats across instances → write it out in YAML, or
      (last resort) add a `ContextGenerator` to `autointeg/registry.py`, which
      is additive and touches no engine logic.

If every box is ticked and it still cannot be expressed, you have found a
genuine framework gap.

### 6.2 The rule

> **Do not modify `peripheral_auto_integrator.py` or anything under
> `scripts/autointeg/` to make one peripheral work.**
>
> A confirmed gap is not a blocker to route around — it is the one moment where
> the framework itself should be upgraded, deliberately and with the developer's
> sign-off. Patching the engine in-flight to unblock a peripheral produces
> exactly the kind of special case the V2 rewrite existed to remove.

### 6.3 Sign-off protocol when the gate is genuinely hit

1. **Agent writes up the gap**: what the peripheral needs, which handler is
   closest, why each item in §6.1 was ruled out, with file:line evidence.
2. **Stop.** Do not write engine code yet.
3. **Joint review.** Developer confirms the gap is real, or names the YAML
   expression that was missed.
4. **Widen the scope deliberately.** A confirmed gap is the right moment to also
   pick up the relevant roadmap items from `HANDOFF.md` §10 — e.g. the
   duplicate-`(anchor, guard)` lint (§9.4), deriving `SYNC_BSV_FILES` from the
   defs (§9.2), a def schema (§10.10). Decide together what rides along.
5. **Design before code.** New handler type vs. new patch flag vs. generator:
   agree which, and how it stays general rather than peripheral-specific.
6. **Regression cost is part of the decision.** An engine change invalidates the
   verification of all eight existing peripherals. The full-BOM run (Rung 5) and
   the subset matrix (Rung 6) must be re-run — budget for it.

### 6.4 Changes that are *not* engine changes (and are fine)

Additive, low-risk, still worth mentioning to the developer:

* A new `files/templates/<name>.template.yaml`.
* A new bootstrap seed hunk for an anchor a fresh clone lacks.
* A new `ContextGenerator` entry in `autointeg/registry.py`.
* Adding your file to `SYNC_BSV_FILES` in `files/Makefile` **if** your def
  patches a board file not already synced — check this explicitly; it is the
  V1 headline bug and is still latent in V2 (HANDOFF §9.2).

---

## 7. Phase 5 — The iterative test ladder

Each rung gates the next. Never skip a rung because the previous one "obviously"
implies it — Rungs 3 and 5 exist precisely because audits passed on broken RTL.

### Rung 0 — a clean clone

```bash
# from the framework root
make setup_base CLONE_NAME=gc2025_<name>test CONFIG_SELECT=soc_build_config1.yaml \
                BOARD=nexys_video SETUP_PATH=../..
cd ../../gc2025_<name>test/hw
```

Expect: clone created, framework injected, `hw/Makefile.orig` present. Confirm
with `make status` from the framework root (it should score V2 as the match).

> Use a **dedicated clone per peripheral under development** and delete it when
> done (`make remove_automation` then `make delete_clone`). A reused clone with a
> stale baseline is the single most common source of phantom failures.

### Rung 1 — dry run: does every anchor resolve?

```bash
make run_autointeg_complete_dry
```

Expect: no `[FAIL] Anchor pattern not found`. A failure here means a regex
doesn't match the pristine tree — fix the pattern, or the anchor needs a
bootstrap seed.

### Rung 2 — apply, and diff what it did

```bash
make run_autointeg_complete
git -C .. diff --stat        # or: diff -wB against .automation_backup/
```

Expect: exactly the files your checklist (§4.6) predicted, and no others.
**Read the diff.** This is the cheapest place to catch code landing at the wrong
anchor, at the wrong indent, or in the wrong family group.

Check your Phase 0 shared-state budget against reality:

```bash
grep -E "PLICWidth|ExtIntWidth|ExtIntMsb|_Num_Slaves|_err_slave_num|ClusterEnd" \
     boards/nexys_video/Soc.defines
```

### Rung 3 — idempotency

```bash
cp -r boards/nexys_video /tmp/after_first_run
make run_autointeg_complete
diff -r boards/nexys_video /tmp/after_first_run    # must be EMPTY
```

Expect: byte-identical. Any difference is a guard bug (§4.4) — a counter that
grew, a block that duplicated, a guard that never matches.

### Rung 4 — restore

```bash
make restore_autointeg_patches
git status        # board sources, hw/ root copies AND bsvpath back to pristine
```

Expect: a clean tree, and `.automation_backup/` **retained**. Then re-apply for
the next rung. If restore reports success while changing nothing, the baseline
is poisoned (UserManual §8).

### Rung 5 — compile (the gold standard)

```bash
make run_autointeg_complete
make sync_bsv_patches
make generate_verilog BOARD=nexys_video -j$(nproc)
```

Expect: `build/` contains `mkSoc.v`, `mkDebugSoc.v`, no errors.

> **A green `post_validate_automation` proves nothing about the RTL.** The audits
> check that text landed, not that the design elaborates. `sspi_v1` sat in the
> verified matrix as PASS while failing to compile at all. Compile, always.

Watch specifically for:
* `S0015 index N out-of-range` → an absolute bit index into a bus that moved.
* `T0035 Bit vector of unknown size` → arithmetic in a bit slice; use a
  precomputed `…Msb` macro.
* `mkTbSoc` rejected → an `(*always_enabled*)` input not tied off in `TbSoc.bsv`.

### Rung 6 — the subset matrix

Your peripheral must compile **alone** and **with the others**, in any order.

```bash
# minimal config: only your peripheral
make restore_autointeg_patches && make run_autointeg_complete && \
  make sync_bsv_patches && make generate_verilog -j$(nproc)
# then the full BOM
# then swap your entry's position with its neighbour and repeat
```

Record a row in the UserManual §17 matrix style: `ExtIntWidth`, `PLICWidth`,
slave slots, `bsc` PASS. A peripheral that only compiles *after* another one has
run has a hidden dependency — that is Bug 1 of UserManual §21.

### Rung 7 — pin/XDC sanity

```bash
make track_pin_map
grep -A1 "<your_port>" boards/nexys_video/constraints.xdc
```

Expect: your ports placed on the pins you chose (not the fallbacks — a fallback
means a conflict you should understand), with the right `IOSTANDARD`. A literal
`IOSTANDARD {…}` in the file means a placeholder key missing from the context.

---

## 8. Phase 6 — Reading the generated Verilog

Source inspection is ambiguous; generated Verilog is not. This is the strongest
structural check available and it is under-used (HANDOFF §9.3).

```bash
cd build   # or wherever generate_verilog put it
```

| Check | How |
|---|---|
| Your slave exists | `grep -n "mk<ip>" mkmixed_cluster.v` (or the right cluster) |
| The decoder actually routes to it | `grep -n "<IP>Base\|slave_num" mkmixed_cluster.v` — a slave with no decoder arm is the §24/§7 failure: it exists, it compiles, nothing reaches it |
| PLIC width matches your budget | `grep -n "plic_inputs\|PLICWidth" mkmixed_cluster.v` |
| Your ports reached the top level | `grep -n "<port>" mkSoc.v mkDebugSoc.v` |
| Pinmux direction arms (if pinmux-routed) | `grep -n "cell..\_mux_out\b\|cell..\_mux_outen" mkpinmux.v` — `outen` is **active-low**: `val1` = input, `val0` = drive |

---

## 9. Phase 7 — Bitstream and terminal logs

> **HOLD — a bitstream run costs a full synthesis (tens of minutes).** Only run
> it when Rungs 1–7 and Phase 6 are green, and confirm with the developer first.

```bash
make BOARD=nexys_video quick_build_automated   # full flow, ends in board_build
# or, if already integrated and compiled:
make generate_boot_files ip_build board_build
```

What to read in the logs, in order:

1. **`pre_validate_automation`** — one `[PASS]`/`[FAIL]` per check, then a
   verdict line. Exit 1 on failure.
2. **`bsc` output** — the errors in Rung 5.
3. **Vivado synthesis** — search the log for `CRITICAL WARNING` and for
   *unconstrained* ports. An unplaced port means your XDC patch didn't land.
4. **Timing** — WNS/WHS must stay positive. Record them; compare against the
   previous run so a regression is visible. (Reference point from the legacy
   work: WHS +0.051.)
5. **`post_validate_automation`** — presence check only. Necessary, not
   sufficient.

> **Trap: `board_build` can report success when synthesis failed.** Its second
> Vivado call ends in `|| (echo "ERROR: While running synthesis")` with no
> `exit 1`, so `$?` is a false green (HANDOFF §10, Tier 1 item 4). **Do not
> trust the exit code — confirm the bitstream file exists and its timestamp is
> from this run.**

---

## 10. Phase 8 — Software bring-up handoff

RTL that is synthesised and addressable is still not usable. The GCSDK side
(`TARGET=ganga`) needs: an entry in the address table in `platform.h`, the PLIC
source id, and a driver. UserManual §25 has the full procedure and a
hardware-free test ordering (steps 0–6 need no external hardware).

Two things to hand over explicitly:

* **The real PLIC source id**, read off the generated `plic_inputs` — not the
  `plic_index` from the config (§5.4).
* **The real base address**, and whether it is *off-stride* relative to its
  family (both `GPTimer4` and the new I2Cs are — that breaks table-driven
  drivers that assume a constant stride).

A peripheral reading back all-zeros or all-ones is almost always decoding to the
error slave — check the cluster window (§24) before suspecting the IP.

---

## 11. Hold-point register

Everything the agent must not decide alone, in one place.

| # | Decision | Why it's the developer's | Phase |
|---|---|---|---|
| 1 | Address window | Collides silently with baseline slaves the engine never checks (§14 Finding A) | 0 |
| 2 | Pin selection + IO standard | Physical, bank-voltage dependent; wrong = hardware risk, not a build error | 0 |
| 3 | Position in `automated_peripherals` | Renumbers PLIC source ids = software ABI break, silently | 3 |
| 4 | Whether to add routing modes | Adds permanent complexity to the def | 0 |
| 5 | Any engine/script change | §6 — this is a framework upgrade, not a peripheral fix | 4 |
| 6 | Editing the bootstrap seed | Changes the pristine tree for every peripheral | 2 |
| 7 | Deleting dead code from board sources | Correct fix for guard traps, but it is a baseline change | 2 |
| 8 | Reset/clock-tree participation | e.g. watchdog `reset_out`: observation pin vs. real reset tree (UserManual §20) | 0 |
| 9 | Running a bitstream build | Tens of minutes of machine time | 7 |
| 10 | Toolchain version changes | `bsc` / Vivado version pinning is environment-wide | any |

---

## 12. Trap checklist (tick before declaring done)

- [ ] Cluster `…End` bumped if my slave sits above every existing one (§24)
- [ ] No guard keyed on comment text (§7)
- [ ] No guard built from `{calculated_val}` (§7)
- [ ] No two patches sharing both anchor and guard (HANDOFF §9.4)
- [ ] No absolute bit index or literal bus width anywhere (§21)
- [ ] Inserted into `connect_interrupt_lines`, never replaced it (§17 Rule 2b)
- [ ] Every `replace` patch re-emits everything it consumed, guarded on the post value
- [ ] `apply_once` vs `instance_aware` correct on every patch
- [ ] `indent_context` on every BSV patch; `.defines` output has no stray newline (§5)
- [ ] `plic_gated` on both the PLIC signal patch **and** its width counter
- [ ] Anchor ownership respected — family anchor or dedicated marker, not a pile-on (§6)
- [ ] Any newly patched board file is in `SYNC_BSV_FILES` (HANDOFF §9.2)
- [ ] `TbSoc.bsv` tie-offs for every `(*always_enabled*)` input
- [ ] Compiled standalone **and** in the full BOM (§17 — audits don't compile)
- [ ] Bitstream verified by artifact existence, not by exit code (§9)

---

## 13. Definition of done

A peripheral is integrated when **all** of the following hold:

1. `run_autointeg_complete_dry` resolves every anchor on a pristine clone.
2. Apply → re-apply is byte-identical (Rung 3).
3. `restore_autointeg_patches` returns the tree to pristine, byte-for-byte (Rung 4).
4. `generate_verilog` passes **standalone** and in the **full BOM** (Rungs 5–6).
5. The generated Verilog shows the slave, its decoder arm, its ports, and the
   expected PLIC width (Phase 6).
6. Shared-state deltas match the Phase 0 budget exactly.
7. `files/templates/<name>_v1.template.yaml` exists, is valid YAML, and is fully
   commented including a second-instance example.
8. `docs/UserManual.md` has a new section for the peripheral in the style of
   §18–§20, and §17's matrix has a new row with real `bsc` results.
9. The test clone is removed (`remove_automation` → `delete_clone`).
10. PLIC id and base address handed to the software side (Phase 8).

---

## 14. Findings from this review not covered elsewhere

Verified against the code on 2026-09-23. These are gaps in the current docs, not
instructions — treat them as known sharp edges.

**Finding A — the resource tracker does not know about baseline slaves.**
`ResourceTracker.used_addresses` is populated **only** from `base_addr`/`end_addr`
of declared automated peripherals (`audit_instances`,
`peripheral_auto_integrator.py:566`). `ingest_static_state` reads `Soc.defines`
but records only macro **names**, never their address ranges
(`peripheral_auto_integrator.py:492`). Consequence: a new window overlapping an
existing baseline slave — XADC (`0004_1000..13FF`), Pinmux (`0004_1500..15FF`),
Eth (`0004_4000`) — **passes the pre-audit silently**. Address-window choice must
be checked by hand against `Soc.defines`. This is why §2.2 Q7 is a HOLD.

**Finding B — `plic_vector` is superseded by design, not merely unused.**
Across all eight defs only two explicit handler types appear: `cluster_config`
(27 uses) and `xdc_pin_assign` (22). Discrete PLIC sources are inserted by the
**plain regex handler** with `position: before` on a one-signal-per-line concat,
guarded by `skip_if_contains` (see `gptimer_v1.yaml` -> `mixed_cluster_plic_vector`,
which is an anchor *name*, not a `type:`). No V2 def has ever declared it, in any
commit; it is inherited from V1, where all three legacy defs still use it
(`archive/*/files/ip_bsv/rtc_v1/rtc_v1.yaml:95`). UserManual §21's derived-macro
refactor took width ownership away from it -- with `Bit#(`PLICWidth)` in
`mixed_cluster.bsv` its width branch cannot fire, which the handler's own code
states at `peripheral_auto_integrator.py:1353`. `UserManual.md` §3 now marks it
LEGACY. **For a new peripheral: copy the plain-insert pattern.** Removal is
queued as `PLANNED_UPGRADES.md` UP-01, together with the one idea worth
salvaging from it -- a concat-scoped post-audit, generalised to every patch.

**Finding C — `plic_index` is bookkeeping, not placement.** Covered in §5.4. It
feeds the uniqueness audit and can order signals within one peripheral's batch;
it never determines the PLIC source id.

---

## 15. Refinement log

Append one entry per peripheral integrated with this document. Record what the
document got wrong or failed to anticipate — that is what makes it better.

| Date | Peripheral | What this doc missed / should say |
|---|---|---|
| 2026-09-24 | `random_v1` | **Phase 0 needs a synthesizability check.** Structure (AXI4-Lite slave, no pins, no interrupt, no params) said "easiest possible"; the IP turned out to be simulation-only. Add to §2.1: grep the RTL for `$random`, `import "BDPI"`, `import "BVI"`, `$readmem`, file I/O **before** recommending it. The failure would otherwise surface only at Vivado synthesis, after every ladder rung passed. |
| 2026-09-24 | `random_v1` | **Anchor on a pristine macro, not another peripheral's output.** The first draft anchored its address window on `` `define WatchDog0End ``, which only exists if `watchdog_v1` ran. Rung 1 caught it. §4.2 should say explicitly: an anchor must match the **pristine** tree, and must not be owned by another def (checked: gptimer→GPTimer3End, watchdog→WatchDog0End, i2c→I2C1End, sspi→SPI1End, uart→UART2End; `PinmuxEnd` was free). |
| 2026-09-24 | `random_v1` | **`generate_verilog` may need several invocations.** bsc builds dependencies first; the top-level `mkSoc.v` appeared only after repeated `make generate_verilog` passes. Rung 5 should say: loop until `mkSoc.v` exists, rather than reading one exit code as completion. |
| 2026-09-24 | `random_v1` | **`run_setup_build` regenerates `makefile.inc` from `bsvpath`.** Switching config without re-running it leaves `BSVINCDIR` stale, and the compile fails with "Cannot find package" for peripherals the new config needs. Add to Rung 5. |
