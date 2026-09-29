# Planned Integrations — the next five

**Queue (developer's order):** `watchdog` → `clint` → `tcm` → `bootrom` → `bram`.

Working notes per candidate: what the RTL is, what the **baseline SoC already
does with it**, and therefore what integration actually means. Each entry ends
with a Phase-0 answer sheet in the shape `README_NEW_PERIPHERAL.md` §2 asks for,
filled in as far as the sources allow, with open questions marked **ASK**.

> **Headline finding — four of the five are already in the tree.** Only `tcm` is
> an unintegrated IP in the sense the framework means. `watchdog` is already a
> shipped V2 def; `clint`, `bootrom` and `bram` are instantiated in the baseline
> `Soc.bsv` before any automation runs. For those four, "integrate" has to mean
> something other than "add a peripheral" — see each entry, and §7.

Source of truth for RTL: `gitlab.com/shaktiproject/uncore/devices` @ `origin/master`
(`60d9dee`), checked 2026-09-23. Baseline SoC facts read from
`gc2025/hw/boards/nexys_video/` and `gc2025/hw/bsvpath`.

---

## 1. `watchdog` — ALREADY INTEGRATED (`watchdog_v1`)

**Status:** shipped, verified to bitstream. **Nothing to do unless the ask is a
second instance or a change of policy.**

**RTL** (`devices/watchdog/watchdog.bsv`, 229 lines, unchanged upstream — the
`origin/master` copy is byte-identical to the pinned `d5dda6d`):
`mkwatchdog` (core) plus two bus wrappers, `mkwatchdog_axi4lite` and
`mkwatchdog_axi4`. Four registers at offsets `0x0` counter-compare, `0x8`
control, `0x10` reset-duration, `0x18` activate/feed. Control bits: `[0]` enable,
`[1]` 0 = interrupt mode / 1 = reset mode, `[2]` soft reset. Two outputs:
`reset_out` (active-low, `always_ready, always_enabled`) and `interrupt`.

**What `watchdog_v1` already does:** adds `devices/watchdog/` to `bsvpath`, adds
the import and an `mkwdt` synthesis wrapper to `mixed_cluster.bsv`, instantiates
wd0 at `0004_0900..09FF` on a dynamically allocated mixed-cluster slot, routes
`interrupt` to the PLIC as a discrete source, and takes `reset_out` to one
dedicated FPGA pin.

Two design decisions already made and documented in the def, worth not
re-litigating by accident:

* **`reset_out` goes to an observation pin, not the SoC reset tree.** Feeding it
  back would touch reset generation in `Soc.bsv` / `DebugSoc.bsv` / `fpga_top.v`
  and would need a raw external reset for the watchdog's own `ext_rst` so it
  survives the reset it causes. Software uses the PLIC interrupt instead.
* **The `mkwdt` wrapper takes no `Reset` argument** and calls
  `exposeCurrentReset` internally. Passing the reset in as a port makes `bsc`
  emit two G0043 multiple-reset warnings, because the AXI transactor and the
  watchdog core then look like different reset domains.
* **`wd_control = 0`, `reset_cycles = 100`** are baked into the wrapper, so the
  watchdog is disabled at power-on and cannot fire before firmware configures it.

**Plausible remaining work, if any — ASK:**
- [ ] A second instance (wd1)? The def is instance-parametric, so this is a config
      entry plus a free address window and pin. Cheap.
- [ ] Promote `reset_out` into the real reset tree? Invasive; the def argues
      against it. This is a design decision, not an integration task.
- [ ] Nothing — treat as done and move to `tcm`.

---

## 2. `clint` — ALREADY IN THE BASELINE SoC

**Status:** instantiated before any automation runs. Not a peripheral-integration
task as the framework defines one.

**Baseline evidence** (`boards/nexys_video/`):

| Where | What |
|---|---|
| `bsvpath` | `devices/clint` present |
| `Soc.defines:` | `` `define Clint_slave_num 1 ``, `` `define ClintBase 'h0200_0000 ``, `` `define ClintEnd 'h020B_FFFF `` |
| `Soc.bsv:47` | `import clint::*;` |
| `Soc.bsv:87` | decoder arm `addr >= \`ClintBase && addr <= \`ClintEnd` |
| `Soc.bsv:328` | `Ifc_clint_axi4#(\`paddr, \`axi4_id_width, \`buswidth, \`USERSPACE, \`num_harts, 512) clint <- mkclint_axi4();` |
| `Soc.bsv:390` | `mkConnection(clint.ma_stop_count, ccore.mv_stop_timer);` |
| `Soc.bsv:426` | `mkConnection(fabric.v_to_slaves[\`Clint_slave_num], clint.slave);` |

CLINT is core infrastructure — it drives `mtime`/`mtimecmp` and the software and
timer interrupts, is parameterised on `` `num_harts ``, and is bound directly to the
core (`ccore.mv_stop_timer`). It is not on the slow peripheral fabric and has no
pins.

**So what could "integrate clint" mean? — ASK.** Three readings, very different
work:
1. **Make it framework-managed** — move the hardcoded instantiation into a
   `clint_v1` def so its presence, address window and `num_harts` become
   config-driven. This is the *inversion* of the usual job: writing patches that
   reproduce what the baseline already hardcodes, against a bootstrap seed that
   removes it. Feasible, but it buys configurability, not function.
2. **Multi-hart / multi-core work** — if the real goal is more harts, that is a
   core-configuration change (`` `num_harts ``), not a peripheral integration.
3. **Something specific** the baseline clint doesn't do — needs naming.

Until this is answered there is no Phase-0 sheet to fill in.

---

## 3. `tcm` — THE REAL ONE

**Status:** RTL present, address window reserved, **never instantiated**. This is
the genuine `watchdog_v1`-shaped job: reuse RTL that is wired into nothing.

**Baseline evidence:**

| Where | What |
|---|---|
| `bsvpath` | `devices/tcm` **present** |
| `Soc.defines` | `` `define TCMBase 'h6000_0000 ``, `` `define TCMEnd 'h6FFF_FFFF `` — window reserved |
| `Soc.bsv` | **no import, no instance, no decoder arm, no fabric connection** |

So the address map already reserves a 256 MB window at `6000_0000..6FFF_FFFF`
and nothing answers there. Upstream ships `devices/tcm/TCM.bsv` plus a driver
(`devices/tcm/driver/tcm.c`). Note upstream also carries `devices/mailbox/`,
described in its own history as "mailbox as upgraded version of tcm" — **ASK**
whether the target is `tcm` or `mailbox`.

**Phase-0 answer sheet (to be completed against `TCM.bsv` before writing YAML):**

| # | Question | Answer / status |
|---|---|---|
| 1 | RTL exists? | Yes, `devices/tcm/TCM.bsv` |
| 2 | On the bsvpath? | **Yes already** — so, unlike watchdog, no bsvpath patch |
| 3 | Synthesis wrapper exists? | **TBD** — read `TCM.bsv`; if the module takes `Integer`/`String` params (like `mkbram_axi4` does) it needs an `mkX` wrapper, the `watchdog_v1` `mkwdt` pattern |
| 4 | Ships its own `.bsv`? | No — reuse in place |
| 5 | `always_enabled` methods → TbSoc tie-off? | **TBD** |
| 6 | Which cluster? | **Neither** — at `6000_0000` it is a *top-level* fabric slave like clint/bootrom, not a slow-peripheral-cluster slave. **This is a new shape for the framework**: `fn_slave_map` in `Soc.bsv` + a top-level `Num_Slaves`, not `MixedCluster_*`. Expect the def to look more like the cluster-window work of §24 than like `gptimer_v1`. |
| 7 | Address window | Already reserved: `6000_0000..6FFF_FFFF`. **Verify** it is inside whatever top-level window the outer decoder uses, per UserManual §24 — the failure mode is silent. |
| 8 | Above every existing slave in its cluster? | **CHECK** — top level, so the `…ClusterEnd` rule may not apply in the same form |
| 9 | Slave slot | Top-level slot, **not** `{allocated_slot}` from the mixed pool. **This likely needs a new counter target** — confirm which macro governs top-level slave count |
| 10 | Interrupt? | **TBD** — likely none (a memory), which would make it Rule-3 cheap |
| 11 | Pins? | **None expected** — on-chip memory |
| 12 | IOBUF? | N/A |
| 13 | Modes? | Possibly size/init-file variants — decide only if real |

**Flag for the Python gate.** Item 6/9 is the first candidate in this queue that
may not be expressible with the existing handlers: every shipped def allocates
from a *cluster* pool. If the top-level slave map needs a different counter shape,
that is a §6.1 checklist item to work through honestly — and if confirmed, a §6.3
sign-off, not a quiet engine edit.

---

## 4. `bootrom` — ALREADY IN THE BASELINE (simulation path)

**Status:** instantiated, under `` `ifdef simulate ``.

```bsv
// Soc.bsv:341-345
`ifndef simulate
  Ifc_bram_axi4#(...) boot <- mkbram_axi4('h1000, "boot.mem", "BOOT");
`else
  Ifc_bootrom_axi4#(...) boot <- mkbootrom_axi4(`BootBase);
`endif
```

`bsvpath` carries `devices/bootrom`; `Soc.bsv:66-68` imports it;
`Soc.defines:38,51-52` give it `` `define Boot_slave_num 5 ``,
`` `define BootBase 'h0000_1000 ``, `` `define BootEnd 'h0000_8FFF ``.

**bootrom and bram are the same slave, selected by build mode** — bootrom for
simulation, bram for FPGA. They share `Boot_slave_num` and the `BootBase..BootEnd`
window, and both bind to the identifier `boot`.

**So "integrating bootrom" — ASK.** Candidate readings:
1. Make the boot-memory choice, its size and its init file **config-driven**
   instead of `ifdef`-driven.
2. Add a *second*, separate ROM at a new window (a genuine new slave — this one
   is a real integration).
3. Change what the existing bootrom contains — that is a software/boot-image
   task, not RTL integration.

---

## 5. `bram` — ALREADY IN THE BASELINE (FPGA path)

**Status:** instantiated under `` `ifndef simulate `` — see §4; same slave, same
window, same `boot` binding.

```bsv
Ifc_bram_axi4#(`paddr, `axi4_id_width, `buswidth, `USERSPACE, 13)
  boot <- mkbram_axi4('h1000, "boot.mem", "BOOT");
```

Parameters are baked in at the call site: base `'h1000`, init file `boot.mem`,
memory name `"BOOT"`, index width 13. This is exactly the kind of hardcoding a
def could lift into YAML.

**Most plausible real task — ASK to confirm:** add an **additional** BRAM as a
scratchpad/data memory at its own window, rather than touching the boot one.
That *is* a normal integration, and it is close in shape to `tcm` (top-level
fabric slave, no pins, likely no interrupt) — so doing `tcm` first and reusing
its def as the archetype is the efficient order.

---

## 6. Suggested order, revised

The developer's order was `watchdog → clint → tcm → bootrom → bram`. On the
evidence above:

| # | What | Why here |
|---|---|---|
| 1 | **`tcm`** | The only genuine unintegrated IP. It also establishes the **top-level fabric slave** archetype the framework does not yet have, which `bram` and any second ROM would then reuse. |
| 2 | **`bram` (second instance)** | Reuses the archetype `tcm` establishes. |
| 3 | **`bootrom` / boot-memory parametrisation** | Same slave as bram; decide the `ifdef` → config question once, apply to both. |
| 4 | **`clint`** | Only if the goal is framework-management or multi-hart — needs its intent defined first. |
| 5 | **`watchdog`** | Already done; revisit only for wd1 or the reset-tree decision. |

---

## 7. The question behind all of this — ASK BEFORE STARTING

Four of the five are already in the tree, which suggests the intent may be
broader than "add peripherals":

> **Is the goal to bring baseline-hardcoded blocks under framework management —
> making the whole SoC composition config-driven — rather than to add new
> hardware?**

If yes, that is a coherent and valuable project, but it is a **different shape of
work** and should be planned as such:

* It inverts the usual job: patches reproduce what the baseline already contains,
  and the **bootstrap seed removes** the hardcoded version so there is an anchor
  to patch. Every such seed edit changes the pristine tree for all eight existing
  peripherals (`README_NEW_PERIPHERAL.md` §11 hold-point #6).
* It needs the **top-level slave map** (`fn_slave_map`) to become patchable —
  today every def works inside a cluster.
* The regression surface is the whole BOM, so the standing test instruction in
  `PLANNED_UPGRADES.md` §1 applies: all 8 peripherals × all 4 configs.

Answering this changes entries 2, 4 and 5 completely. Until then, `tcm` is
unambiguously worth starting and does not depend on the answer.

---

## 8. Additional candidates from `devices/` (surveyed 2026-09-23)

Surveyed every IP in `gitlab.com/shaktiproject/uncore/devices` @ `origin/master`
for peripheral-shaped, not-yet-integrated candidates. Excluded by nature:
`dma`, `plic`/`riscv-plic`, `clic`, `jtagdtm`, `riscvdebug100`/`riscvDebug013`,
`riscv-etrace`, `mcpu`, `fabrics`, `common_bsv`, `common_tb`, `err_slave`, `ecc`
(infrastructure, debug, or unorthodox). Excluded by size/complexity: `sdram`
(7336 lines), `onfi` (6322), `sdcard` (4447), `qspi` (2091).

Already integrated by V2: `rtc`, `uart_v2`, `gpio_v2`, `i2c`, `spi_v2`, `pwm_v2`,
`gptimer`, `watchdog`.

### ✓ `random` — INTEGRATED 2026-09-24 as `random_v1`, gated on `ifdef simulate`

**Status: done.** Integrated with `simulate_ifdef: true` (UP-14), which wraps
every patch in `` `ifdef simulate `` so the IP exists in simulation builds and is
compiled out of synthesis. Verified both ways: on the FPGA path `mkrandom.v` is
not generated and `mkmixed_cluster.v` contains zero `random` references; on the
simulate path `mkrandom.v` is generated and bsc emits `vpi_wrapper_rand32.c`,
confirming the BDPI dependency below.

Original disqualification, kept because it is why the gate exists:

**Do not integrate unconditionally.** Its randomness source has no synthesizable path.
`random.bsv` builds on `Randomizable.bsv`, which reaches the actual generator two
ways, and **both are simulation-only**:

* Verilog path — `import "BVI" ConstrainedRandom` (`Randomizable.bsv:26`). That
  primitive (`$BLUESPECDIR/lib/Verilog/ConstrainedRandom.v`) generates its value
  with **`$random`**, a Verilog system task that exists only in simulation, plus
  a runtime `%` on it. Vivado cannot synthesise it.
* Bluesim path — `import "BDPI" function rand32()` / `srand()`
  (`Randomizable.bsv:112-113`), C foreign functions, Bluesim only.

**The failure mode is the nasty one this framework is built to catch:** it would
pass every rung up to and including `generate_verilog`, and every audit, then
fail (or silently degrade) at Vivado synthesis — i.e. only after a full
place-and-route run. Exactly the "audits prove text landed, not that the design
works" lesson of UserManual §17/§9.3.

A synthesizable RNG would need a real entropy source — an LFSR, or a ring
oscillator with the usual FPGA caveats. That is new RTL and a design decision,
not an integration task.

*(Recorded because `random` otherwise looks like the perfect candidate — AXI4-Lite
slave port only, no pins, no interrupt, no module parameters. Structure alone is
not enough; check for `$random` / `BDPI` / `BVI` imports before recommending an
IP. The other candidates below were re-scanned for these constructs and are
clean.)*

### `random` — original assessment, retained for the shape it illustrates

**Why it wins.** Its AXI4-Lite wrapper's interface is **only the slave port**:

```bsv
interface Ifc_random_axi4lite#(numeric type addr_width, numeric type data_width,
                               numeric type user_width);
    interface AXI4_Lite_Slave_IFC#(addr_width, data_width, user_width) slave;
endinterface

module mkrandom_axi4lite(Ifc_random_axi4lite#(addr_width, data_width, user_width))
```

| Property | Value |
|---|---|
| Bus | **AXI4-Lite** — matches the mixed cluster exactly (`mki2c`, `mkgpio`, `mkcordic` are the same shape) |
| Module params | **None** — no `Integer`, no `String`, no `Reset`. The `(*synthesize*)` wrapper is the simplest possible, simpler than `watchdog_v1`'s `mkwdt` |
| External pins | **None** |
| Interrupt | **None** |
| Registers | `enb_reg` `0x0`, `res_reg` `0x8` (`random/random.defines`) |
| Size | 312 lines across `random.bsv` + `Randomizable.bsv` |
| bsvpath | **Not present** — needs a bsvpath patch (the `watchdog_v1` pattern) |

**This is UserManual §17 Rule 3 in its purest form — "the cheapest independence
is to touch no shared state at all."** No PLIC slot, no interrupt-bus bit, no
`ExtIntWidth`/`PLICWidth`, no pins, no XDC, no `fpga_top.v`, **no `Soc.bsv` patch
at all** (nothing to export to the chip boundary). It is strictly cheaper than
`gpio_v1`, the current cheapest def.

**Estimated patch set (~9, all existing handlers):**

| # | File | Patch | Handler |
|---|---|---|---|
| 1 | `bsvpath` | add `devices/random` | regex |
| 2 | `Soc.defines` | `RandomBase` / `RandomEnd` | regex |
| 3 | `Soc.defines` | `Random_slave_num {allocated_slot}` | regex |
| 4 | `Soc.defines` | `MixedCluster_Num_Slaves` +1 | cluster_config |
| 5 | `Soc.defines` | `MixedCluster_err_slave_num` +1 | cluster_config |
| 6 | `mixed_cluster.bsv` | `import random::*;` | regex |
| 7 | `mixed_cluster.bsv` | `(*synthesize*) module mkrandom(...)` wrapper | regex |
| 8 | `mixed_cluster.bsv` | instantiate + decoder arm | regex |
| 9 | `mixed_cluster.bsv` | `mkConnection` to the fabric | regex |

**Address window:** the mixed cluster has free 256-byte windows above the slots
`rtc_v1` / `gptimer_v1` / `watchdog_v1` claim (`0004_0600`–`0004_09FF`) and below
`XADCBase` (`0004_1000`) — e.g. `0004_0A00..0AFF`. **Below `MixedClusterEnd`, so
no cluster-end bump (§24) and no UP-13 exposure.** Confirm against the pristine
tree at Phase 0.

**Verdict: no framework upgrades needed.** Every patch maps to an existing
handler, and it avoids every known sharp edge.

### `gpt_lite` — trivial but redundant

A lightweight general-purpose timer: `Ifc_gptimer_io` (an `always_enabled`
`input_signal` + `timer_out`), `sb_interrupt`, AXI4-Lite wrapper — i.e.
**structurally identical to the already-integrated `gptimer_v1`**, whose def could
be copied nearly verbatim. Easy, but it adds a second flavour of a timer the SoC
already has. Worth doing only if the *lite* variant is wanted for area.

### `mailbox` — moderate; the "tcm upgrade"

Upstream describes it as "mailbox as upgraded version of tcm". AXI4 (not Lite)
slave plus `return_mail_box_interrupt`; `mkmailbox_axi4#(String modulename,
Integer base_address)` needs a params-baking wrapper. Banked memory → likely a
top-level fabric slave, so **UP-13 applies**. Resolve the `tcm`-vs-`mailbox`
question before starting either.

### `capmatrix` — moderate

AXI4 and AXI4-Lite wrappers, slave port only (no pins), but
`mkcapmatrix_AXI4#(Bit#(awidth) base, String mem_init_file1, String
mem_init_file2, String modulename)` needs a wrapper baking four params including
two memory-init files. Memory-backed → top-level, **UP-13 applies**.

### `iqc` — not a peripheral

76 lines, `Ifc_iqc#(n)` with a single `qualify(Bit#(n) inputs)` method. An input
qualifier/debouncer helper with **no bus interface at all**. Already on the
bsvpath. Nothing to integrate.

### Revised difficulty ranking

| Rank | Candidate | Shape | Framework upgrades needed |
|---|---|---|---|
| ✓ | `random` | **DONE** — mixed-cluster AXI4-Lite slave, `ifdef simulate`-gated | UP-13 + UP-14 (both shipped) |
| 1 | **`gpt_lite`** | copy of `gptimer_v1`; synthesizable, clean scan | none (but redundant in function) |
| 3 | `tcm` | top-level slave, params wrapper | **UP-13** |
| 4 | `bram` (2nd instance) | top-level slave, params wrapper | **UP-13** |
| 5 | `mailbox` / `capmatrix` | top-level slave, params, (mailbox: +interrupt) | **UP-13** |
| 6 | `bootrom` | same slave as `bram`; needs the `ifdef` policy decision first | **ASK** |
| 7 | `clint` | fixed mid-list slave number; full management forces renumbering 5 macros | **UP-13 + renumbering** |
