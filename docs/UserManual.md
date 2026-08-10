# Shakti SoC — Peripheral Auto-Integration User Manual

This manual explains the **data-driven peripheral integration framework** that
lives in `hw/`. It lets you add a peripheral to the SoC (address map, BSV
wiring, interrupt/PLIC routing, FPGA top-level ports, and XDC pin constraints)
by editing **YAML only** — no hand-edits to the BSV/Verilog sources.

It also documents the **indentation system**, because that is where the visible
"it looks messy" problems come from and how they were fixed.

---

## 1. The big picture

```
soc_build_config.yaml         <- WHAT to integrate (bill of materials)
        │
        ├── ip_bsv/<ip>/<ip>.yaml   <- HOW to integrate it (anchors + patches)
        │
        ▼
scripts/peripheral_auto_integrator.py   <- the engine
        │  (reads config, applies patches in-memory, atomic commit + backup)
        ▼
boards/nexys_video/*   <- PATCHED here (Soc.bsv, mixed_cluster.bsv,
   Soc.defines, uart_cluster.bsv, pinmux.bsv, fpga_top.v, constraints.xdc)
        │
        │  make sync_bsv_patches / sync_automated_files
        ▼
hw/ root copies   <- what the compiler actually reads
        │
        ▼
make generate_verilog  ->  build/  ->  make board_build  ->  bitstream
```

The board directory (`boards/nexys_video/`) is the **source of truth** that gets
patched. The `hw/` root copies are **compile inputs**; a `sync_*` target copies
board → root before compilation.

### Component map

| Path | Role |
|---|---|
| `soc_build_config.yaml` | Bill of materials: which peripherals + instances, and the per-instance context (slave numbers, address ranges, PLIC indices, pins, and any **mode** such as `uart3_mode`) |
| `ip_bsv/<ip>/<ip>.yaml` | Per-IP definition: **anchors** (regex insertion points) + **patches** (code to inject at an anchor) |
| `ip_bsv/uart_v1/`, `ip_bsv/uart_v1/` | The two UART3 routing variants — through the pinmux vs. wired to dedicated FPGA pins. Selected with a per-instance `uart3_mode` (see §13) |
| `ip_bsv/gpio_v1/` | Widens on-chip GPIO 32 → 48 and pins the 16 new lines to the FMC-LA bank; one instance per new line (see §14) |
| `ip_bsv/i2c_v1/` | Adds two more direct-routed I2C controllers (I2C2, I2C3) beside the baseline I2C0/I2C1, with PLIC interrupts (see §15) |
| `ip_bsv/sspi_v1/` | Adds two more direct-connected SSPI controllers (SPI2, SPI3) beside the baseline SPI0/SPI1, with PLIC interrupts routed through the shared interrupt bus (see §16) |
| `ip_bsv/pwm_v1/` | Adds four direct-connected PWM channels (PWM6–PWM9) by widening the single `mkpwm` slave 6 → 10 channels. No PLIC, no shared state — the reference for a fully order-independent peripheral (see §18) |
| `ip_bsv/gptimer_v1/` | Adds one direct-connected general-purpose timer (GPTimer4) beside GPTimer0–3, with a discrete PLIC interrupt. Adds no BSV source and no `bsvpath` entry — the IP and its `mkgptimer` wrapper already exist (see §19) |
| `ip_bsv/watchdog_v1/` | Adds one direct-connected watchdog (wd0) with a discrete PLIC interrupt. Reuses the previously-unwired `devices/watchdog/` RTL: adds the `bsvpath` entry, the import, and a `mkwdt` synthesis wrapper. `reset_out` goes to an observation pin only, **not** the SoC reset tree (see §20) |
| `ip_bsv/<ip>/<ip>.bsv`, `*.defines` | The peripheral's actual RTL + register-map defines |
| `scripts/peripheral_auto_integrator.py` | The engine: VFS, handlers, atomic commit, restore |
| `scripts/utils.py` | Logging, template resolution, `IndentationAnalyzer` + `INDENT_PROFILE` |
| `scripts/indent_tools.py` | Unified indentation toolkit — `normalize` (whole-file tab→space + blank-run cap), `cleanup` (surgical one-time fixes), `audit` (policy audit of automation-added lines); see §6 |
| `scripts/xdc_analyzer.py` | Board pin-map / XDC constraint handling |
| `.automation_backup/` | The pristine baseline: pre-automation snapshots + `manifest.json`. Captured once on the first real run, kept across restores, dropped only by `make reset_autointeg_baseline` (see §8) |
| `boards/<board>/pin_map.yaml` | Exported pin state, regenerated on every commit |

Currently integrated (see `soc_build_config.yaml`): **`rtc_v1`** (two instances,
RTC0 + RTC1), **`uart_v1`** (adds UART3, in one of two routing modes chosen by a
per-instance `uart3_mode` — see §13), **`gpio_v1`** (widens GPIO 32 → 48 and
pins the 16 new lines to FMC — see §14), **`i2c_v1`** (adds I2C2 + I2C3
beside the baseline I2C0/I2C1 — see §15), **`sspi_v1`** (adds SPI2 + SPI3
beside the baseline SPI0/SPI1, direct-connected, with PLIC interrupts — see §16),
**`gptimer_v1`** (adds GPTimer4, direct-connected, discrete PLIC
interrupt — see §19), **`watchdog_v1`** (adds wd0, direct-connected,
discrete PLIC interrupt — see §20), and **`pwm_v1`** (adds PWM6–PWM9,
direct-connected, no PLIC — see §18).

---

## 2. Quick start

```bash
cd hw/

# Simulate everything — validates every anchor, writes nothing
make run_autointeg_complete_dry

# Apply all phases (BSV + Verilog + XDC) and re-export the pin map
make run_autointeg_complete

# Copy patched board files to the hw/ root so the compiler sees them
make sync_bsv_patches

# Compile BSV -> Verilog (gold-standard correctness check)
make generate_verilog BOARD=nexys_video -j$(nproc)
```

### Everyday targets

| Target | What it does |
|---|---|
| `run_autointeg_complete[_dry]` | Apply (or just simulate) all phases |
| `run_autointeg_bsv[_dry]` / `_verilog[_dry]` / `_xdc[_dry]` | Phase-scoped runs |
| `sync_bsv_patches` | Back up, then copy `$(SYNC_BSV_FILES)` — `Soc.bsv`, `Soc.defines`, `mixed_cluster.bsv`, `uart_cluster.bsv`, `pinmux.bsv`, `spi_cluster.bsv`, `pwm_cluster.bsv`, `TbSoc.bsv` — from `boards/$(BOARD)/` → `hw/` root |
| `sync_automated_files` | Same as above **plus** `constraints.xdc` and `fpga_top.v`, and re-tracks the pin map |
| `restore_autointeg_patches` | Roll every patched file — board sources **and** the `hw/` root copies — back to its pre-automation snapshot. The baseline is **kept**, so the target is repeatable |
| `reset_autointeg_baseline` | Deliberately discard `.automation_backup/`. The next integration run captures a new baseline from whatever is then on disk. Run only from a restored/clean tree |
| `pre_validate_automation` / `post_validate_automation` | Anchor audits before / after patching. Both print one `[PASS]`/`[FAIL]` line per check and a final `[PASS] Pre-audit passed` / `[PASS] Post-audit passed` verdict (exit 1 on failure). Both are also idempotency-aware: a patch whose `skip_if_contains` **or** `skip_if_matches` guard already matches is counted as applied, so re-running the audits on an already-patched tree passes. |
| `track_pin_map` | Re-export `pin_map.yaml` from the current XDC |

`bsvpath` is not in the sync lists: it lives only at the `hw/` root, the engine
patches it in place, and it is tracked in the backup manifest like any other
target. The two sync targets call `--backup` on the root copies *before*
overwriting them, which is what lets `restore_autointeg_patches` rewind the root
as well as `boards/$(BOARD)/`.

**Re-running is safe.** Every patch carries an idempotency guard
(`skip_if_contains` / `skip_if_matches`), so a second `run_autointeg_complete`
changes nothing. This is verified for every peripheral individually and for the
full BOM (§17).

### One-shot full build

`make quick_build_automated` (or `make build_automated`) runs the whole flow:
pre-validate → `update_bsvpath` → `run_setup_build` → dry-run → BSV patch →
sync → `generate_verilog` → Verilog patch → XDC patch → sync → post-validate →
`generate_boot_files ip_build board_build` (bitstream).

---

## 3. How a patch is defined

Each IP's `<ip>.yaml` has two sections.

**anchors** — named regex insertion points:

```yaml
anchors:
  uart_cluster_instantiation:
    file: "{board_dir}/uart_cluster.bsv"
    pattern: 'let uart2 <- mkuart\(\);'
    position: "after"          # after | before | replace
    # multiline: true          # adds re.DOTALL for block patterns
```

**patches** — code that references an anchor:

```yaml
patches:
  - anchor_ref: "uart_cluster_instantiation"
    code: "let uart3 <- mkuart();"
    skip_if_contains: "let uart3 <-"   # idempotency guard
    apply_once: true
    indent_context: "module_body"      # see §5
```

`{board_dir}`, `{slave_id_macro}`, … are resolved from the `context` /
`base_context` / per-instance blocks in `soc_build_config.yaml`.

### Handler types (`type:` on a patch)

| `type` | Used for |
|---|---|
| `regex` (default) | Plain insert / replace at an anchor |
| `plic_vector` | Interrupt-vector width + concat management (width-agnostic) |
| `cluster_config` | Macro counters in `Soc.defines` (e.g. `*_Num_Slaves`) and derived widths |
| `xdc_pin_assign` | Pin placement with automatic conflict fallback |

---

## 4. The five patch phases (what actually changes)

Phases are decided by a patch's **target file**, never by its position in the
YAML: `bsvpath` → the `bsvpath` phase, `fpga_top.v` → `verilog`, an
`xdc_pin_assign` type → `xdc`, everything else → `bsv`. (`make update_bsvpath`
therefore touches `bsvpath` and nothing else; `run_autointeg_bsv` carries the
`bsvpath` patches too, so a standalone BSV run still registers the include path.)

For the current RTC + UART3 + GPIO + I2C + SPI + PWM integration, patches touch:

| File | What is injected |
|---|---|
| `Soc.defines` | `RTCx Base/End`, `UART3 Base/End`, slave numbers, `*_Num_Slaves`, `err_slave_num`; *(I2C)* `I2C2/I2C3 Base/End`, slave nums, MixedCluster counters +2 |
| `mixed_cluster.bsv` | `mkrtc` instances, RTC interfaces, address-decoder arms, AXI connections, PLIC vector widened, `interrupts(Bit#(14))`; *(GPIO)* `GPIO#(32)`→`GPIO#(48)` width bump; *(I2C)* `mki2c` instances, decoder arms, AXI, `I2C_out` interfaces, `i2c{2,3}.isint` into PLIC |
| `uart_cluster.bsv` | `uart3` instance, `uart3_io` interface, decoder arm, AXI connection, `Bit#(4) uart_interrupts` |
| `pinmux.bsv` | *(UART3 `with_pinmux` mode only)* UART3 tx/rx wires, `PeripheralSideUART uart3`, cell mux arms + rules |
| `pwm_cluster.bsv` | *(PWM)* the single `mkpwm` slave widened `channels` 6 → 10; four `Bit#(1) pwm{6..9}_io` interface methods + their `pwm0.io.pwm_o[N]` bindings |
| `Soc.bsv` | RTC interfaces; UART3 routing — `with_pinmux`: `uart3.tx.put`/`uart3.rx.get` through the pinmux; `without_pinmux`: direct `chip_io.uart3_io` export; *(GPIO)* per-line `wr_gpioN_in` wire, `chip_io` method trio, `gpio_in_combined` entry; *(I2C)* `chip_io` `I2C_out i2c{2,3}_out` decl + impl; *(PWM)* `chip_io` `Bit#(1) pwm{6..9}_io` decl + binding |
| `fpga_top.v` | `rtcX_out_pmod` ports, `wire_rtcX_out`, `.rtcX_io_rtc_clock_signal(...)`, `IOBUF rtcX_io_inst`; *(UART3 `without_pinmux` mode)* dedicated `uart3_SIN`/`uart3_SOUT` ports + SoC mapping; *(GPIO)* per-line `inout gpio_N` port, wires, SoC port maps, `IOBUF`; *(I2C)* `inout i2c{2,3}_sda/scl` ports, wires, SoC port maps, IOBUFs; *(PWM)* `output pwm{6..9}` ports, `wire_pwmN`, SoC port maps, IOBUFs |
| `TbSoc.bsv` | *(UART3 `without_pinmux` mode only)* `uart3` sim instance + tx/rx wiring to `chip_io.uart3_io` |
| `constraints.xdc` | `PACKAGE_PIN` assignments (with fallback pins on conflict); *(UART3 `without_pinmux`)* dedicated UART3 RX/TX pins; *(GPIO)* one FMC pin per new `gpio_N`; *(I2C)* `sda`/`scl` pins per new controller; *(PWM)* one FMC pin per new `pwmN` |

The UART3 rows depend on the per-instance `uart3_mode` (see §13); the GPIO rows
come from `gpio_v1` (see §14); the I2C rows come from `i2c_v1` (see §15); the SPI
rows from `sspi_v1` (see §16); the PWM rows from `pwm_v1` (see §18).
Note that PWM touches **no** `Soc.defines`, `mixed_cluster.bsv` or PLIC state.

---

## 5. The indentation system (spaces, 2 per level)

**Policy: injected code is always spaces, 2 per structural level.** The engine
never guesses per-patch — a patch names an `indent_context` and the engine looks
up the base indent in a central table (`INDENT_PROFILE` in `scripts/utils.py`).

| `indent_context` | Width | Use for |
|---|---|---|
| `package_body` | 2 | imports, `(*synthesize*)`, module/interface headers |
| `interface_body` | 4 | members of an interface declaration |
| `module_body` | 4 | `let`, `mkConnection`, `Wire`, method/rule headers |
| `function_body` | 4 | statements in package-level functions |
| `method_body` | 6 | statements inside a method body |
| `rule_body` | 6 | statements inside a rule body |
| `branch_body` | 6 | statement under an if/else-if without begin/end |
| `mux_option` | 6 | pinmux ternary-chain continuation lines |
| `pinmux_module_body` | 6 | pinmux.bsv module body |
| `pinmux_interface_body` | 12 | `PeripheralSide` members in pinmux.bsv |

Author the YAML `code:` block with **relative** structure, spaces only, 2 per
level. The engine strips the block's common indent, quantises each line to the
2-space grid, prepends the resolved base indent, and trims trailing whitespace.
`.defines` lines are emitted at column 0 (contexts don't apply there).

If a leading indent it reads from the file contains tabs, each tab is counted as
2 columns and the total is floored to the 2-space grid — so a tab-indented
anchor can't push injected code onto an odd column.

### Blank lines around `.defines` replacements

`IndentationAnalyzer.apply_defines_indent()` always terminates its output with a
newline. The `replace` anchors used by `cluster_config` patches
(`MixedCluster_Num_Slaves`, `MixedCluster_err_slave_num`, …) match the define
line **without** its trailing newline, so the text following the match already
begins with one. `ClusterConfigHandler` therefore strips the appended newline
before splicing, and re-adds it only when the anchor is the file's last line.

Without that strip, every `cluster_config` patch left one stray blank line
behind, and the gap **grew by one line per peripheral** — two blank lines with
`rtc`+`i2c`, three once `gptimer` was added. The `regex` handler already
`.rstrip("\n")`s its `.defines` output for the same reason; the two paths now
agree. If you add a handler that writes `.defines`, strip the trailing newline.

---

## 6. Why the source files were re-indented (and how)

**The problem you saw in the screenshots:** the legacy board sources were
**tab-indented**, but the automation always injects **space-indented** code.
Tabs render at a different visual width than spaces, so an injected line such as
`uart3.tx.put(...)` (6 spaces) sat visibly misaligned next to `uart1`/`uart2`
(which were `\t\t   …`). Same story for the RTC lines and the gptimer ports.
BSV/Verilog ignore whitespace, so this was cosmetic — but it looked unprofessional.

**The fix:** all indentation tooling now lives in **one script**,
`scripts/indent_tools.py`, with three subcommands (`normalize`, `cleanup`,
`audit`). It replaces the former `normalize_indent.py`,
`one_time_indent_cleanup.py` and `indent_audit.py`.

### `normalize` — whole-file tab→space conversion

Converts the affected board files to the same space-only, 2-space grid the
automation uses, so legacy and injected code share one grid. It:

- rewrites any **leading** whitespace that contains a tab as spaces
  (tab = 2 columns, floored to the 2-space grid);
- leaves **pure-space** leading indentation byte-for-byte (this protects
  space-literal anchors, notably `fpga_top.v`'s `'   wire ip2intc_irpt;'`);
- collapses inner alignment tabs (`code\t\t// comment`) to a single space;
- strips trailing whitespace and blanks out whitespace-only lines;
- **collapses runs of more than 2 consecutive blank lines to 2** (the files'
  own block-separation style). Earlier automation splices had stacked up to
  4 blank lines between the SoC instantiation's `);` and
  `assign interrupts = 2'b0 ;` in `fpga_top.v` — this is the fix. Safe for
  every anchor: multi-line anchor patterns match whitespace with `\s*\n\s*`;
- **(Verilog only)** re-indents the SoC instantiation port list
  (`mkDebugSoc core( … );`, delimited by paren balance) so every `.port(...)`
  and `// comment` line sits at the block's modal indent (8 spaces). The legacy
  file mixed tab-8 alignment with spaces, so plain tab→space conversion left
  some ports (`.spi0_io_miso_*`, `.mem_master_*`) shallower than the 8-space
  majority; this makes the whole list uniform. Other instantiations (which use
  their own indents) are left alone.

```bash
python3 scripts/indent_tools.py normalize --check   # report, write nothing
python3 scripts/indent_tools.py normalize           # apply (idempotent)
```

It is **idempotent** — a second run reports 0 changes. Run it on the restored
baseline **before** `run_autointeg_complete` so the snapshot captured in
`.automation_backup/` is already clean.

### `cleanup` — one-time surgical fixes

A fixed list of line-targeted regex repairs for defects that `normalize`
deliberately cannot touch (it never rewrites pure-space leading indentation):

- indentation defects left by **earlier** automation runs (gptimer interface
  declarations, GPTimer address-decoder bodies, 5-space `mkConnection`s,
  `DebugSoc.bsv`/`TbSoc.bsv` splice damage) that `skip_if_contains`
  idempotency would otherwise preserve forever;
- `fpga_top.v`'s legacy `assign interrupts = 2'b0 ;`, which sat at a 2-space
  indent in a module body that uses 3 spaces everywhere else. The fix is
  line-targeted and cannot collide with the `'   wire ip2intc_irpt;'`
  automation anchor.
- `fpga_top.v`'s `clk_converter` `.s_axi_aresetn(~soc_reset),` port, which sat
  at 4 spaces while every sibling `.s_axi_*` port is at 7. `normalize`'s port
  re-indent only targets the `mkDebugSoc` instance (a `mk\w+` opener), so this
  other instantiation is fixed here instead.

```bash
python3 scripts/indent_tools.py cleanup --check   # report hits, write nothing
python3 scripts/indent_tools.py cleanup           # apply (idempotent)
```

### `audit` — policy audit of automation-added lines

Diffs a pre-automation baseline against the current tree and checks **only
the added/changed lines** for policy violations (tabs in leading whitespace,
odd indent widths, whitespace-only lines, else-if bodies not indented deeper
than their branch), so untouched legacy lines are never flagged. Prints one
`[PASS]`/`[FAIL]` line per file plus a total; exit code 0 = clean.

```bash
python3 scripts/indent_tools.py audit \
    --baseline .automation_backup/boards/nexys_video \
    --current  boards/nexys_video
```

### The stray-comma fix

The RTC port patch injects `.rtcX_io_rtc_clock_signal(...)` **after**
`.ext_interrupts_i(interrupts)` in the SoC instantiation, and adds the comma that
now has to separate the two. The old anchor
`'\.ext_interrupts_i\(interrupts\)\s*,?'` let `\s*` swallow the newline before
`);`, so the comma landed on its own line (`   ,`). Idempotency is already
guaranteed by `skip_if_contains`, so the `\s*,?` was unnecessary; the anchor is
now `'\.ext_interrupts_i\(interrupts\),?'` and the output is:

```verilog
        .ext_interrupts_i(interrupts),
        .rtc0_io_rtc_clock_signal(wire_rtc0_out),
        .rtc1_io_rtc_clock_signal(wire_rtc1_out)
   );
```

### Where injected code LANDS: one anchor, one owner

**If N peripherals share an anchor, their injected lines come out in reverse apply
order** — the engine splices each new block immediately after the anchor, so the
last one applied ends up on top. That is deterministic for a fixed
`soc_build_config.yaml`, but it is not *meaningful* order, and it made the output
look shuffled: `gptimer4`'s ports landed *below* `pwm6..9`, `wd0` and `rtc`, even
though `gptimer4` belongs next to `gptimer0..3`.

Four IP defs were all anchored on `output gptimer3_out,` (and three on
`.gptimer3_io_timer_out(...)`, and three on `` `define GPTimer3End ``). The rule now is:

> **An anchor has one owner.** A peripheral that *extends an existing family*
> anchors on that family's last member. Everything else anchors on a dedicated
> marker.

`fpga_top.v` therefore carries two markers in the **board source**:

```verilog
    // ---- direct-connected peripheral ports (added by automation) ----
    // ---- direct-connected peripheral port maps (added by automation) ----
```

- `gptimer_v1` keeps the `gptimer3` anchors → `gptimer4_in/out` land
  immediately after `gptimer3_in/out`, in both the port list and the port map.
- `rtc`, `pwm`, `watchdog` anchor on the markers instead.

The same rule applies in `Soc.defines` (§7 below and §22).

---

## 7. Idempotency: `skip_if_contains` vs `skip_if_matches`

- `skip_if_contains: "literal"` — skip if the (template-resolved) literal is
  already present. Keep it **unique to this patch's own output**; avoid
  embedding whitespace.
- `skip_if_matches: 'regex'` — for cases a literal can't express (e.g. two
  patches emit similar text). The UART width *declaration* and *implementation*
  both emit `Bit#(4) uart_interrupts`, so the implementation checks
  `method Bit#\(4\) uart_interrupts;\s*\n\s*return \{` instead.
- The pre-audit honors **both** guards: a `replace` patch consumes its own
  anchor, so on an already-patched tree only a matching `skip_if_*` guard
  proves the patch landed — without this, `make pre_validate_automation`
  would report the consumed anchors (`Bit#(13)`/`Bit#(3)`) as missing.
- For `cluster_config` counters, never build the skip from `{calculated_val}`
  (only known at apply time — the check would never match and every re-run would
  increment). Use a per-instance macro the same run defines.
- A `replace` patch must re-emit **everything** its pattern consumes (a pattern
  spanning `rule … endrule` must re-emit the rule wrapper).

### Guards are tested against comment-stripped content

**A skip guard asks "has my code already been injected?" — and commented-out code
has not been.** Both `skip_if_contains` and `skip_if_matches` are therefore matched
against a copy of the file with `//…` and `/* … */` blanked out (`strip_comments()`
in `peripheral_auto_integrator.py`; `#` is only treated as a comment in `.xdc`,
because in BSV it is the type-parameter sigil — `Bit#(13)`).

This matters, and here is the bug that forced it. `spi_cluster.bsv` shipped
upstream with a **commented-out** decoder arm:

```bsv
    /*else if(addr>= `SPI2Base && addr<= `SPI2End )
      slave_num =  `SPI2_slave_num;*/
```

`sspi_v1`'s decoder patch guards on `` `{base_macro} `` → `` `SPI2Base ``, which
matched that dead comment. The engine concluded SPI2's arm was already present and
**silently skipped it**. SPI3 (which had no such comment) was injected normally.
The result: `spi2` was instantiated, given pins, an interrupt and a fabric
connection — but **nothing ever decoded to it**. Every access to
`0x0002_0200..02FF` fell through to `SPICluster_err_slave_num`. A completely dead
slave, and it **compiled cleanly**.

Worse, `post_validate_automation` *passed*, because the audit checks presence with
the same guard and also found the comment. That is the real lesson: **an audit that
greps for a string cannot tell live code from a corpse.**

Two consequences for IP authors:

1. **Key a guard on the code you inject, never on a trailing comment.** `uart_v1`
   used to guard its pinmux mux arms on `"uart3_rx is an input"` — comment text.
   With comment-stripping that guard can never match, so the patch would re-apply
   on every run and duplicate the mux arm. Both now guard on the code
   (`"wrcell12_mux==1?val1:"`).
2. **Delete dead code from the board source rather than working around it.** The
   `/* SPI2 */` block was removed from `spi_cluster.bsv`; the fix is not to make
   the guard cleverer.

---

## 8. Recovery / full regeneration

```bash
make restore_autointeg_patches          # roll back to the pre-automation baseline
                                        # (the baseline itself is KEPT)
make run_autointeg_complete             # regenerate everything
make run_autointeg_complete             # optional 2nd run: must change nothing
make sync_bsv_patches                   # refresh hw/ root compile inputs
```

### How the baseline works

`.automation_backup/` holds the **pristine baseline**: the one surviving copy of
every file the automation touches, as it looked *before* the first patch landed.
`manifest.json` lists them (`modified` = restore these, `created` = delete these).

- The baseline is captured **once**, by the first integration run, and
  `snapshot_files()` never re-snapshots a file already in the manifest. That is
  what keeps the baseline pristine across the multi-phase flow
  (`--bsvpath` → `--bsv` → `--verilog` → `--xdc`), where every phase after the
  first reads files the previous phase already patched.
- `--restore` copies the baseline back over the board sources **and** the `hw/`
  root copies, then **keeps** `.automation_backup/`. Restore is therefore
  idempotent: run it twice, get the same tree.
- `--reset-baseline` (`make reset_autointeg_baseline`) is the only thing that
  deletes it. Use it when you have *intentionally* changed the pre-automation
  sources and want the next run to re-baseline from them.

> **Why restore no longer deletes the backup.** It used to. That left the tree
> patched with no baseline, so the *next* integration run happily snapshotted the
> **already-patched** files as "pristine" — and because the manifest is never
> re-snapshotted, that poisoned baseline stuck. Every subsequent
> `make restore_autointeg_patches` then reported success while changing nothing.
> If a run ever finds no baseline **and** has no patch left to apply, it now
> refuses rather than baselining a patched tree:
>
> ```
> [FAIL] [COMMIT] Refusing to create a backup baseline from an already-patched tree.
> ```
>
> Recover the pre-automation sources (e.g. `git checkout -- boards/`), or set
> `AUTOINTEG_FORCE_BASELINE=1` if the current tree really is your intended
> baseline.

### Re-indenting the baseline

`scripts/indent_tools.py normalize` rewrites tabs → spaces. Run it on the
**restored** tree, then `make reset_autointeg_baseline` so the normalized files
become the new baseline, then re-apply:

```bash
make restore_autointeg_patches
python3 scripts/indent_tools.py normalize
make reset_autointeg_baseline           # normalized tree becomes the new baseline
make run_autointeg_complete
```

---

## 9. Verification recipes

```bash
# 1. Every anchor must be found
make run_autointeg_complete_dry

# 2. No tabs / even widths / else-if bodies deeper than their branch
python3 scripts/indent_tools.py audit --baseline <snapshot_dir> --current boards/nexys_video

# 3. Semantic diff, whitespace-insensitive
diff -wB <old_file> <new_file>

# 4. Compile (gold standard)
make generate_verilog BOARD=nexys_video -j$(nproc)
#   success: Verilog under build/  (mkSoc.v, mkDebugSoc.v)
#   Do NOT run run_setup_build/run_setup_fpga just to test — they pip-uninstall
#   packages and clobber hw/ root files with board copies.
```

---

## 10. Adding a new peripheral (zero Python changes)

1. Put sources under `ip_bsv/<name>/` with a `<name>.yaml` (copy `uart_v1.yaml`
   or `rtc_v1.yaml` as a template).
2. Add an entry to `automated_peripherals` in `soc_build_config.yaml`
   (`def_path`, `base_context`, `instances`).
3. In `<name>.yaml`, define **anchors** (where) and **patches** (what), each with
   an `indent_context` and a `skip_if_*` guard.
4. Dry-run, then apply:
   `make run_autointeg_complete_dry && make run_autointeg_complete`.
5. `make sync_bsv_patches && make generate_verilog BOARD=nexys_video -j$(nproc)`.

---

## 11. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `[FAIL] Anchor pattern not found` | The regex no longer matches (file edited, or a `replace` patch consumed it with no skip guard). Repair the pattern / `skip_if_*`; confirm with the dry-run. |
| Patch silently skipped | Its skip string also matches another patch's output — **or a commented-out line**. Skip guards are plain substring/regex checks with no comment awareness, so a dead `//\`define SPI2Base` in the baseline will suppress the real patch. Strip such scaffolding from the baseline (see §16), make skip strings unique, or switch to `skip_if_matches`. |
| `make restore_autointeg_patches` reports success but nothing changes | The baseline was cut from an already-patched tree (poisoned). Check `.automation_backup/<file>` against `git show HEAD:hw/<file>`. Recover the sources, `make reset_autointeg_baseline`, then re-apply. Current builds refuse to create such a baseline (§8). |
| `[FAIL] Refusing to create a backup baseline…` | `.automation_backup/` is missing while the tree is already patched. Restore the pre-automation sources first, or set `AUTOINTEG_FORCE_BASELINE=1` if this state is intentional (§8). |
| `*_Num_Slaves` / `err_slave_num` grows on every build | A phase is re-applying a `cluster_config` counter whose `skip_if_contains` macro that phase never defines. Phases are keyed off the patch's **target file** — check `classify_patch_phase()`; a patch must not run in a phase that omits the patch defining its skip macro. |
| Injected code at wrong depth / with tabs | Patch has no `indent_context`, or the file is still tab-indented. Add the right `indent_context`; run `scripts/indent_tools.py normalize`. |
| Two lines won't align (legacy vs injected) | Legacy tabs. Run `scripts/indent_tools.py normalize` on the restored baseline, then re-apply. |
| A separator comma lands on its own line | An anchor's `\s*` swallowed the newline — tighten the pattern (see §6). |
| Counter macro grows every run | Its skip uses `{calculated_val}` — use a per-instance macro instead (§7). |
| Pin conflict in XDC phase | Handler used `fallback_pin`; check `pin_map.yaml` and `constraints.xdc`. |
| `IOSTANDARD {MY_IO_STD}` literal in the XDC | `io_standard` on an `xdc_pin_assign` patch is template-resolved like the pins — the placeholder key must exist in the peripheral's context. |
| hw/ root out of date after a run | `make sync_bsv_patches` (or `sync_automated_files` for XDC + Verilog). |
| `no mode named in any instance` | A peripheral has a `modes:` map but no instance field is set to one of the mode names (or the value isn't a declared mode). Set e.g. `uart3_mode: with_pinmux` in the instance. See §13. |
| Bitstream aborts: clock net driven by a non-clock-capable pin | See the `CLOCK_DEDICATED_ROUTE` note below. |

### `CLOCK_DEDICATED_ROUTE` in `constraints.xdc`

`constraints.xdc` carries two of these, and neither is cruft — do not "clean them up":

```tcl
set_property CLOCK_DEDICATED_ROUTE BACKBONE [get_nets sys_clk_IBUF]
set_property CLOCK_DEDICATED_ROUTE FALSE    [get_nets external_clk_IBUF]
```

`external_clk` sits on **AB20 = `ja[3]`**, an ordinary I/O pin rather than a
clock-capable (MRCC/SRCC) one — yet it drives a clock net (`.CLK_ext_clk(...)` in
`fpga_top.v`, feeding `gptimer0..3`, plus `rtc0`/`rtc1` and `gptimer4` when those
IPs are enabled). Vivado treats a non-CC pin sourcing a clock as a **placer error**
and aborts bitstream generation; `FALSE` demotes it to a warning and routes the net
on general fabric.

It lives in the **board source**, not in any IP def, because `gptimer0..3` already
use `external_clk` in a pristine tree — so the error exists with *zero* peripherals
enabled. Hiding it inside (say) `rtc_v1.yaml` would mean any configuration without
that IP silently fails to build. It survives the XDC handler's re-render and
`make restore_autointeg_patches` retains it, since it is now part of the baseline.

---

## 12. Switching boards

1. Set `BOARD` (Makefile default or CLI: `make BOARD=<board> …`).
2. Update `target_board`, `board_dir`, `pin_map_path` in
   `soc_build_config.yaml`.
3. The `{board_dir}` placeholders in each `<ip>.yaml` resolve automatically;
   verify the anchor patterns still match the new board's files with
   `make pre_validate_automation`.

---

## 13. UART3 routing modes (with / without pinmux)

UART3 ships in **two interchangeable integrations**. They are identical from the
address map down through the UART cluster (same `Soc.defines` slave map, same
`uart_cluster.bsv` instance / decoder / AXI / interrupt-width, same interrupt and
PLIC behaviour) — only **how tx/rx reach the outside world** differs:

| Mode | IP definition | tx/rx routing | Board pins |
|---|---|---|---|
| `with_pinmux` (default) | `ip_bsv/uart_v1/uart_v1.yaml` | Routed through the pinmux cell mux: `pinmux.bsv` gains mux arms/rules and `Soc.bsv` wires `mixed_cluster.pinmuxtop_peripheral_side.uart3`. | Shares the existing muxed cell pins; **no new** `fpga_top.v` ports. |
| `without_pinmux` | `ip_bsv/uart_v1/uart_v1.yaml` | Exposed directly: `Soc.bsv` exports `chip_io.uart3_io`, `fpga_top.v` gains dedicated `uart3_SIN`/`uart3_SOUT` ports, `TbSoc.bsv` wires it for simulation. `pinmux.bsv` is **untouched**. | Dedicated FPGA pins via `constraints.xdc` (see below). |

### Selecting a mode

The mode is set **per-instance** via `uart3_mode`, sitting beside the pins it
governs (the same place rtc keeps its per-instance data). The `modes` map turns
that value into the IP definition to apply — and that is all you declare:

```yaml
automated_peripherals:
  - name: uart_v1
    modes:                          # value -> def_path
      with_pinmux:    ip_bsv/uart_v1/uart_v1.yaml
      without_pinmux: ip_bsv/uart_v1/uart_v1.yaml
    base_context:
      # xdc_file resolves the XDC anchor path (anchor files use base_context),
      # so it stays here rather than in the instance.
      xdc_file: '{board_dir}/constraints.xdc'
    instances:
      - instance_id: '3'
        uart3_mode: with_pinmux     # <- value names a `modes` key = the mode
        connect_to_plic: true
        plic_index: 36
        # --- used only by without_pinmux (direct-pin routing) ---
        uart3_rx_pin: fmc_la_p[07]        # UART3 RX pin
        uart3_tx_pin: fmc_la_n[07]        # UART3 TX pin
        uart3_rx_fallback: fmc_la_p[08]   # used only on a pin conflict
        uart3_tx_fallback: fmc_la_n[08]
        uart3_io_standard: LVCMOS33
```

`modes` is a **generic** engine feature (`resolve_def_path` in
`scripts/peripheral_auto_integrator.py`): any peripheral may carry a `modes`
map of value → def_path. The active value is discovered with no extra
declaration — it is simply the **instance field whose value names one of the
`modes`** (here `uart3_mode: with_pinmux`, and `with_pinmux` is a `modes` key).
All instances that name a mode must agree, since a peripheral has one def_path.
Peripherals with no `modes` map keep their plain `def_path`, so single-variant
peripherals (`rtc_v1`, `gpio_v1`) are unaffected. If the mode is named nowhere,
instances conflict, or the name is not a declared mode, every phase (apply,
pre-audit, post-audit) fails fast with a clear message.

> A field name is not needed twice: earlier this used an explicit
> `mode_selector: uart3_mode` line, which just repeated the instance field name.
> That is gone — the value-in-`modes` convention finds it. (An explicit
> `mode_selector: <field>` is still honored if you ever want a project-wide
> top-level default instead of a per-instance value.)

> `xdc_file` stays in `base_context`, not the instance: anchor **file paths** are
> resolved with the base context (before instances are expanded), so a template
> like `{xdc_file}` in an anchor's `file:` must resolve there. Per-instance data
> that only appears in patch **code** (pins, mode) lives in the instance.

### Pin choice for `without_pinmux` (nexys_video)

On nexys_video **every PMOD header pin (JA/JB/JC) is already taken** by
gptimer / rtc / i2c / spi / gpio / sd / `external_clk`, so UART3's dedicated pins
default to the free **FMC-LA** bank (`fmc_la_p[07]`=RX, `fmc_la_n[07]`=TX,
LVCMOS33). Change `uart3_*_pin` in the config for a different board or a freed-up
PMOD pin. The XDC handler still applies its normal conflict fallback
(`uart3_*_fallback`) if a chosen pin is occupied. `io_standard` is
template-resolved, so `uart3_io_standard` flows through from the config.

### Switching modes on an already-integrated tree

The two modes touch overlapping files, so switch from a clean baseline:

```bash
make restore_autointeg_patches                 # roll back to pre-automation snapshot
python3 scripts/indent_tools.py normalize      # keep the baseline clean
# edit soc_build_config.yaml: set the uart instance's uart3_mode: without_pinmux
make run_autointeg_complete                    # re-apply in the new mode
make sync_automated_files                      # refresh hw/ root (incl. fpga_top.v + XDC)
make generate_verilog BOARD=nexys_video -j$(nproc)   # compile-check
```

Both modes are compile-clean (`mkDebugSoc.v` builds) and pass
`pre_validate_automation` / `post_validate_automation` and the indent audit.

> Interrupt/PLIC note: in **both** modes UART3's interrupt travels inside
> `uart_cluster.uart_interrupts` (widened `Bit#(3)`→`Bit#(4)`) through the
> existing `connect_interrupt_lines` rule. Because that adds a bit, `uart_v1`
> **owns** the interrupt-bus widening (`mixed_cluster.interrupts` /
> `wr_external_interrupts` Bit#(13)→Bit#(14)) and adds its own
> `wr_external_interrupts[13]` PLIC bit — so uart is self-contained and needs no
> other peripheral. See §17 for the independence rules.

---

## 14. GPIO expansion (32 → 48 lines)

`gpio_v1` (`ip_bsv/gpio_v1/gpio_v1.yaml`) widens the on-chip GPIO from 32 to 48
lines and brings the 16 new lines (`gpio_32` … `gpio_47`) out as dedicated
bidirectional FPGA pins on the free **FMC-LA** bank. It is a good worked example
of an **instance-per-thing** peripheral: one config instance per new GPIO line.

**What it changes**

| File | One-time | Per new line `gpio_N` |
|---|---|---|
| `mixed_cluster.bsv` | `GPIO#(32)`→`GPIO#(48)` and the `mkgpio` `Ifc_gpio_axi4lite` width | — |
| `Soc.bsv` | — | `wr_gpioN_in` wire; `chip_io` method trio (`gpio_N` / `gpio_N_out` / `gpio_N_outen`) declaration **and** implementation; a `gpio_in_combined` concatenation entry |
| `fpga_top.v` | — | `inout gpio_N` port; `gpio_N_in/out/en` wires; three SoC-instance port maps; an `IOBUF` |
| `constraints.xdc` | — | `PACKAGE_PIN` for `gpio_N` (LVCMOS12, with fallback) |

GPIO interrupts are **unaffected** — `mixed_cluster` truncates the GPIO
interrupt vector to `Bit#(16)` regardless of width, so there is no PLIC change.

**Config** (`soc_build_config.yaml`) — one instance per new line, pins from the
instance, the shared IO standard + XDC path in `base_context`:

```yaml
- name: gpio_v1
  def_path: ip_bsv/gpio_v1/gpio_v1.yaml
  base_context:
    xdc_file: '{board_dir}/constraints.xdc'
    gpio_io_standard: LVCMOS12        # matches existing FMC GPIOs (gpio_29/30/31)
  instances:
  - instance_id: '32'                 # ASCENDING — matches gpio_15..31 style
    gpio_pin: fmc_la_n[16]
    gpio_fallback: fmc_la_n[26]
  - instance_id: '33'
    gpio_pin: fmc_la_p[16]
    gpio_fallback: fmc_la_p[26]
  # … up to instance_id: '47'
```

**Instance order: ascending (32 → 47).** List the new lines in natural order —
the injected ports, methods, wires and IOBUFs then read `gpio_32, gpio_33, …`,
matching the existing `gpio_15..31` blocks in every file.

The one order-sensitive spot is the `gpio_in_combined` concatenation in
`Soc.bsv`: it is **MSB-first** (`{bit47, …, bit0}`), so its new entries must
*descend* (47→32) to keep input bit *N* aligned with `gpio_out[N]`. Rather than
force the whole config to descend for that one patch, the concatenation patch
sets **`reverse_instances: true`** — a generic engine flag that emits *that
patch's* instances in reversed config order. So the config stays ascending
everywhere, and only the concatenation flips internally. (The engine injects a
patch's instances in list order at a single anchor; `reverse_instances` reverses
that list for the patch that needs it.)

**Pins.** All PMOD headers are full on nexys_video (see §13), so the 16 lines
default to consecutive free FMC-LA pins (`fmc_la_p/n[09..16]`) at **LVCMOS12** —
matching the existing FMC GPIOs `gpio_29/30/31`. Each instance also names a
distinct `gpio_fallback` (`fmc_la_*[19..26]`) that the XDC handler only uses on a
conflict. Change `gpio_pin`/`gpio_io_standard` for a different board or bank.

**Scaling further.** To go beyond 48, bump both width literals in the anchors
(`GPIO#(48)`, `…, 48))`) and add more ascending instances — no other change.
Each new line is guarded by a per-line `skip_if_contains`, so re-running is
idempotent (a second `run_autointeg_complete` changes nothing).

> Verified: `rtc_v1` + `uart_v1` (with_pinmux) + `gpio_v1` (48) compiles clean
> (`mkDebugSoc.v` builds), and passes `pre_validate_automation`,
> `post_validate_automation`, and the indent audit.

---

## 15. Adding more I2C controllers (I2C2, I2C3)

`i2c_v1` (`ip_bsv/i2c_v1/i2c_v1.yaml`) adds two more **direct-routed** I2C
controllers beside the baseline I2C0/I2C1 — one config instance per new
controller. "Direct routing" means each controller's `sda`/`scl` go straight to
dedicated FPGA pins through IOBUFs (not the pinmux), mirroring i2c0/i2c1. It is
the example that also exercises the **shared MixedCluster counters** and the
**PLIC**, so it composes with `rtc_v1`.

**What it changes** (per new controller `i2c{N}`):

| File | Injected |
|---|---|
| `Soc.defines` | `I2C{N}_slave_num`, `I2C{N}Base/End`; and the shared `MixedCluster_Num_Slaves` / `_err_slave_num` counters grow by the number of new instances |
| `mixed_cluster.bsv` | `let i2c{N} <- mki2c;`, address-decoder arm, AXI connection, `I2C_out i2c{N}_out` interface, and `i2c{N}.isint` into the PLIC vector (+ mkplic width synced) |
| `Soc.bsv` | `chip_io` `I2C_out i2c{N}_out` declaration + implementation |
| `fpga_top.v` | `inout i2c{N}_sda/scl` ports, `i2c{N}_*` wires, six SoC-instance port maps, two IOBUFs |
| `constraints.xdc` | dedicated `sda`/`scl` pins (LVCMOS12, with fallback) |

**Shared-resource coordination (the important part).** MixedCluster slave slots
and the PLIC vector are *shared* with rtc, but i2c is **independent of rtc** —
it works standalone or in any combination (see §17 for how):

- **Slave numbers** are auto-allocated from the current err-slot (the
  `{allocated_slot}` mechanism, §17): each new slave takes the next free slot
  and the `_err_slave_num` / `_Num_Slaves` counters move past it. Standalone,
  I2C2/I2C3 take slots 10/11 (err→12, Num→13); after rtc (which took 10/11),
  they take 12/13 (err→14, Num→15). No fixed slave numbers, no ordering rule.
- **PLIC**: each `i2c{N}.isint` is inserted into `plic_inputs` by the
  `plic_vector` handler and the `mkplic` wrapper width is re-synced. I2C
  interrupts are *discrete* signals (not routed through `wr_external_interrupts`),
  so the interrupt-method width is untouched. Standalone the vector is 35→37;
  in the full config it is 40.

**Config** (`soc_build_config.yaml`) — one instance per controller, ascending
(order-independent here: the PLIC handler sorts by `plic_index`, and
ports/methods/IOBUFs don't care). Note there is **no `slave_id_val`** — the slot
is allocated at apply time:

```yaml
- name: i2c_v1
  def_path: ip_bsv/i2c_v1/i2c_v1.yaml
  base_context:
    xdc_file: '{board_dir}/constraints.xdc'
    io_standard: LVCMOS12          # FMC-LA bank, like i2c1 / gpio_29-31
  instances:
  - instance_id: '2'
    slave_id_macro: I2C2_slave_num   # slot value auto-allocated (no slave_id_val)
    base_macro: I2C2Base
    base_addr: '0004_1600'
    end_macro: I2C2End
    end_addr: '0004_16FF'
    connect_to_plic: true
    plic_index: 38
    sda_pin: fmc_la_p[27]
    scl_pin: fmc_la_n[27]
    sda_fallback: fmc_la_p[29]
    scl_fallback: fmc_la_n[29]
  - instance_id: '3'
    # … I2C3 = slot 13, 0004_1700/17FF, plic_index 39, fmc_la_p/n[28] (+[30])
```

**Pins.** i2c0 sits on `ja[1]/ja[2]` and i2c1 on `fmc_la[04]`; the two new ones
default to free FMC-LA pairs (`fmc_la_p/n[27]`, `[28]`) at LVCMOS12. Change
`sda_pin`/`scl_pin` for other pins or a different board.

**Scaling further.** Add more ascending instances with fresh addresses/pins; the
slot counters and PLIC width follow automatically, and per-instance
`skip_if_contains` guards keep re-runs idempotent (the counters do **not**
double-increment on a second `run_autointeg_complete`).

> Verified: `rtc_v1` + `uart_v1` + `gpio_v1` (48) + `i2c_v1` (I2C2/I2C3) compiles
> clean (`mkDebugSoc.v` builds, `mkplic` width 40), and passes
> `pre_validate_automation`, `post_validate_automation`, the indent audit, and a
> re-apply idempotency check.

---

## 16. Adding more SPI controllers (SPI2, SPI3)

`sspi_v1` (`ip_bsv/sspi_v1/sspi_v1.yaml`) adds two more
**direct-connected** SSPI controllers beside the baseline SPI0/SPI1 of the SPI
cluster — one config instance per new controller. "Direct connection" means each
controller's `mosi`/`miso`/`nss`/`sclk` go straight to dedicated FPGA pins through
IOBUFs (mirroring spi0), **not** through the pinmux (spi1's `mspi` path is left
untouched). It composes with the other peripherals, and — like i2c — exercises a
`cluster_config` slave counter and the PLIC; unlike i2c it also grows the **shared
interrupt bus** (see the coupling note below).

The subdirectory is named `sspi_v1/` (like `uart_v1/`) and holds
**only** the YAML — SPI is direct-only here, so there is no `modes:` map.

### Why `spi1_io` is commented out in `Soc.bsv` — leave it that way

`Soc.bsv` contains two commented-out lines that look like an oversight:

```bsv
//    interface Ifc_sspi_io spi1_io;                      // in Ifc_chip_io
//    interface spi1_io = spi_cluster.spi1_io;            // in mkSoc
```

**They are deliberate and load-bearing.** SPI1 is the **pinmuxed** SPI: its outputs
are pushed into the pinmux `mspi` cell, and — critically — its *inputs* are already
driven from the pinmux by `rule connect_pinmux_peripheral_input_lines`:

```bsv
spi_cluster.spi1_io.miso_in(pinmux_spi1_miso);
spi_cluster.spi1_io.mosi_in(pinmux_spi1_mosi);
spi_cluster.spi1_io.sclk_in(pinmux_spi1_clk);
spi_cluster.spi1_io.ncs_in0(pinmux_spi1_nss);
```

Consistently, `fpga_top.v` contains **zero** `spi1` references (versus 21 for
`spi0`) — SPI1 reaches pins through the shared iocells, so it needs no dedicated
ports or IOBUFs.

Exposing `spi1_io` on `chip_io` would re-export those same `Action` input methods
to the chip boundary, giving them **two callers** — the internal pinmux rule and the
external port. `bsc` cannot schedule that, so the rule's condition collapses to
False and every `always_enabled` method it feeds goes undriven. Uncommenting the
two lines and compiling produces exactly that cascade:

```
Error: "Soc.bsv", line 448: (G0066)
  Instance `mixed_cluster' requires the following methods to be always enabled,
  but the conditions for executing the methods are always False:
    gpio_io_gpio_in, pinmuxtop_peripheral_side_mspi_clk_in_get,
    pinmuxtop_peripheral_side_mspi_miso_in_get, … uart1_rx_get, uart2_rx_get …
```

The build is fine with them commented (`mkSoc` elaborates cleanly). **SPI0, SPI2
and SPI3 are on `chip_io` with dedicated pins; SPI1 is not, by design.**

**What it changes** (per new controller `spi{N}`):

| File | Injected |
|---|---|
| `Soc.defines` | `SPI{N}_slave_num`, `SPI{N}Base/End`; the `SPICluster_Num_Slaves` / `_err_slave_num` counters grow by the number of new instances; and `SPIClusterEnd` is widened once (`0002_01FF` → `0002_03FF`) so the outer slow-fabric window covers the new ranges |
| `spi_cluster.bsv` | `let spi{N} <- mkspi();`, address-decoder arm, AXI connection, `Ifc_sspi_io spi{N}_io` interface, and a `spi{N}_sb_interrupt` sideband method |
| `Soc.bsv` | `chip_io` `Ifc_sspi_io spi{N}_io` declaration + binding, and each `spi{N}_sb_interrupt` **prepended at the MSB** of the `mixed_cluster.interrupts({…})` concat |
| `mixed_cluster.bsv` | interrupt bus widened `Bit#(14)` → `Bit#(16)` (method decl + impl + `wr_external_interrupts`), each `wr_external_interrupts[bit]` inserted into the PLIC vector, mkplic width synced (40 → 42) |
| `fpga_top.v` | four `inout spi{N}_mosi/miso/nss/sclk` ports, the 12-line SoC-instance port map, four IOBUFs (mirrors the spi0 block) |
| `TbSoc.bsv` | tie-off of `chip_io.spi{N}_io` inputs (`miso_in`/`mosi_in`/`ncs_in0`/`sclk_in`) for simulation |
| `constraints.xdc` | dedicated `mosi`/`miso`/`nss`/`sclk` pins (LVCMOS12 — the FMC-LA 1.2 V VADJ bank, like i2c/gpio — with fallback) |

**Interrupt coupling (the important, SPI-specific part).** SPI lives in its own
`spi_cluster`, so unlike i2c its interrupt is **not** a discrete signal visible in
the `mixed_cluster` scope. Each `spi{N}_sb_interrupt` instead travels through the
**shared** `mixed_cluster.interrupts(...)` concat → `wr_external_interrupts` →
`plic_inputs`. The new signals are **prepended at the MSB** of that concat, so
every pre-existing bit (0..13) — and therefore every existing `plic_inputs`
slice (`wr_external_interrupts[13]`, `[12:6]`, `[5:0]`) — is preserved unchanged.
`spi2` becomes new bit 14, `spi3` bit 15; each instance's `ext_int_bit` names the
bit the `plic_vector` handler routes to PLIC. Because this bus is **co-owned** with
UART3's `13→14` widening, `sspi_v1` must be listed **after** `rtc_v1` (so the
`plic_width` shared state and the `Bit#(14)` baseline exist first), and its width
guards match "already widened past 14" so a later peripheral can grow the bus
further without breaking idempotency (see §17, Rule 2).

**Baseline prerequisite (idempotency).** The board shipped with commented-out
`SPI2` scaffolding (in `spi_cluster.bsv` and `Soc.defines`). Those dead comments
are **removed once** from the baseline, because a `skip_if_contains "\`define
SPI2Base"` guard would otherwise match the commented `//\`define SPI2Base` line and
wrongly skip the real patch. With the scaffolding gone the guards are unambiguous
and re-applies are exact no-ops.

**Config** (`soc_build_config.yaml`) — one instance per controller, ascending:

```yaml
- name: sspi_v1
  def_path: ip_bsv/sspi_v1/sspi_v1.yaml
  base_context:
    xdc_file: '{board_dir}/constraints.xdc'
    verilog_buffer_type: IOBUF
    spi_io_standard: LVCMOS12        # FMC-LA bank runs at 1.2 V (VADJ)
  instances:
  - instance_id: '2'
    slave_id_macro: SPI2_slave_num   # slot auto-allocated by cluster_config
    base_macro: SPI2Base
    base_addr: '0002_0200'
    end_macro: SPI2End
    end_addr: '0002_02FF'
    connect_to_plic: true
    plic_index: 40
    ext_int_bit: 14                  # wr_external_interrupts bit for SPI2
    mosi_pin: fmc_la_p[02]
    miso_pin: fmc_la_n[02]
    nss_pin: fmc_la_p[03]
    sclk_pin: fmc_la_n[03]
    mosi_fallback: fmc_la_p[31]
    miso_fallback: fmc_la_n[31]
    nss_fallback: fmc_la_p[32]
    sclk_fallback: fmc_la_n[32]
  - instance_id: '3'
    # … SPI3 = slot 3, 0002_0300/03FF, plic_index 41, ext_int_bit 15,
    #   fmc_la_p/n[04] + fmc_la_p/n[05] (fallbacks [33], fmc_la17/18_cc_p)
```

**Pins.** spi0 sits on the `jc` PMOD and spi1 on the pinmux; the two new ones
default to free FMC-LA pins (`fmc_la_p/n[02..05]`) at LVCMOS12. The XDC handler
keeps them at the FMC bank voltage (1.2 V), so a LVCMOS33 request is normalized to
LVCMOS12 to avoid a bank-voltage conflict. Change the `*_pin` fields for other
pins or a different board.

**Scaling further.** Add more ascending instances with fresh addresses/pins/PLIC
indices; the SPI slot counter and PLIC width follow automatically. Because each
extra SPI interrupt widens the shared bus, keep assigning the next `ext_int_bit`
(16, 17, …) and keep `sspi_v1` after the peripherals that establish the current
bus width.

> Verified: `sspi_v1` (SPI2/SPI3) applies clean on top of rtc/uart/gpio/i2c,
> re-applies as an exact no-op (byte-stable), passes `post_validate_automation`
> and the indent audit (0 violations), and both `mkspi_cluster` and
> `mkmixed_cluster`/`mkplic` elaborate under `bsc` (SPICluster 5 slaves, interrupt
> bus `Bit#(16)`, `plic_inputs` `Bit#(42)`, mkplic width 42 — all consistent).

---

## 17. Peripheral independence (any subset, any order)

**Every peripheral compiles standalone and in any combination** — none depends
on another being present. This is enforced by two rules and verified across the
single-peripheral, pairwise, and full configurations.

### Rule 1 — no fixed shared-resource numbers; allocate dynamically

Anything drawn from a **shared pool** (MixedCluster slave slots, PLIC vector
width) is allocated at apply time, never hardcoded per peripheral:

- **Slave slots** — an `err_slave`/`Num_Slaves` `cluster_config` counter with
  `increment_by_instances`. The per-instance slave number uses the
  **`{allocated_slot}`** placeholder, which the handler fills with
  `current_err_value + position_in_batch`. So each new slave claims the slot the
  counter is displacing — standalone `rtc` takes 10/11, standalone `i2c` also
  takes 10/11, standalone `gptimer` and standalone `watchdog` each take 10, and
  `rtc`+`i2c` gives rtc 10/11 then i2c 12/13. No peripheral assumes another's
  numbering, and the config carries no `slave_id_val`.
- **PLIC width** — the `plic_vector` handler is width-agnostic (reads the
  current width, adds this peripheral's signals) and the `mkplic` wrapper
  re-syncs. Contributions compose: `rtc` +2 (its `rtc_sb_interrupt`s), `uart`
  +1 (its bus bit), `i2c` +2 (its `isint`s), `gptimer` +1 (its `sb_interrupt`),
  `watchdog` +1 (its `interrupt`), from the baseline 35.

### Rule 2 — never name an absolute bit index; derive every width from a macro

> **This rule replaced an earlier one that said "each widener claims the new MSB
> bits, so existing positions never move." That claim was false, and it shipped a
> real bug — see §21.** PWM sits at the **LSB** end of the interrupt bus, so
> widening PWM shifts every field above it. And `uart_interrupts` sits *below*
> `spi`, so growing it 3→4 pushed `spi0`/`spi1` up by one. Any hardcoded index is
> therefore only valid for one particular combination of peripherals.

The interrupt bus is a concatenation, MSB → LSB:

```
{ spi.., spi1, spi0, wr_ext_interrupts[1:0], uart_interrupts, pwm..pwm0 }
```

No IP may hardcode a width or a bit index into `mixed_cluster.bsv`. Instead five
macros in `Soc.defines` derive everything, and each contributing IP grows them
with an ordinary `cluster_config` counter:

| Macro | Baseline | Grown by |
|---|---|---|
| `PWMCluster_Channels` / `PWMChannelsMsb` | 6 / 5 | `pwm_v1` +4 |
| `ExtIntWidth` / `ExtIntMsb` | 13 / 12 | `pwm` +4, `uart` +1, `sspi` +2 |
| `PLICWidth` | 35 | all of the above, plus `rtc` +2, `i2c` +2, `gptimer` +1, `watchdog` +1 |

`plic_inputs` then reads the bus through **derived slices** that cover every bit
without naming one:

```bsv
Bit#(`PLICWidth) plic_inputs = {
  wr_external_interrupts[`ExtIntMsb : `PWMCluster_Channels],  // all non-PWM bits
  <discrete signals>, i2c1, i2c0, gptimer3..0, lv_gpio_intr,
  wr_external_interrupts[`PWMChannelsMsb : 0]                 // all PWM channels
};
```

Consequently `uart_v1` no longer patches `mixed_cluster.bsv` at all, and neither
`uart_v1` nor `sspi_v1` injects an absolute index. See §21 for the full story.

### Rule 2b — insert into the interrupt concat; never replace it

`uart_v1` and `sspi_v1` each used to `replace` the whole `connect_interrupt_lines`
rule with a hardcoded block. Whichever ran **last** silently clobbered the other's
signals. The rule is now written one-signal-per-line in the board source, and each
IP **inserts** at a stable anchor:

- `sspi_v1` inserts `spi3`/`spi2` above `spi_cluster.spi1_sb_interrupt,`
- `pwm_v1` inserts `pwm9..pwm6` above `pwm_cluster.pwm5_sb_interrupt,`
  (keeping all ten PWM channels **contiguous** at bus `[9:0]`, so channel *i* is bit *i*)
- `uart_v1` touches the rule **not at all** — `uart_cluster.uart_interrupts` is a
  single `Bit#(N)` method whose width simply grows

Inserts compose in any order; replaces do not.

### Rule 3 — the cheapest independence is to touch no shared state at all

If a peripheral's interrupts can be polled rather than routed, it needs no PLIC
slot, no bus bit, and no `Soc.defines` counter — and then has no ordering
constraint of any kind. (`pwm_v1` *used* to be the reference example
here, because its `sb_interrupt[6..9]` were left dangling. They are now wired, so
it grows the bus like any other interrupting peripheral — see §18/§21.)

### Verified matrix

> **Read this before trusting the table.** An earlier version of this matrix was
> produced by running `pre_validate_automation` / `post_validate_automation`
> standalone — **the audits do not compile.** They only check that anchors resolve
> and injected text is present. `sspi only` was listed as working (bus 16, PLIC 42)
> and in fact **did not compile at all**; the bug had been latent for as long as
> the row had been in this table. Every row below is now verified by an actual
> `bsc` elaboration of `mkSoc`, not by an audit. **When you add a peripheral,
> compile each subset — a green post-audit proves nothing about the RTL.**

| Config | `ExtIntWidth` | `PLICWidth` | slave slots | `bsc` |
|---|---|---|---|---|
| *(pristine — no peripherals)* | 13 | 35 | — | PASS |
| `rtc` only | 13 | 37 | rtc 10/11 | PASS |
| `uart` only (either mode) | **14** | 36 | — | PASS |
| `i2c` only | 13 | 37 | i2c 10/11 | PASS |
| `gpio` only | 13 | 35 | — | PASS |
| `sspi` only | **15** | 37 | SPI2/SPI3 (SPI cluster slots 2/3) | PASS *(was a hard `bsc` error)* |
| `pwm` only | **17** | 39 | — *(widens the existing slave)* | PASS |
| `gptimer` only | 13 | 36 | gptimer4 → 10 | PASS |
| `watchdog` only | 13 | 36 | wd0 → 10 | PASS |
| `uart` + `sspi` (either order) | 16 | — | — | PASS |
| `pwm` + `sspi` (either order) | 19 | — | — | PASS |
| **full BOM (all 8)** | **20** | **48** | rtc 10/11, i2c 12/13, gptimer4 14, wd0 15 | PASS |

Every row is idempotent: applied three times from a pristine tree it produces a
byte-identical result after the first pass, and `make restore_autointeg_patches`
rewinds board sources, the `hw/` root copies **and `bsvpath`** to the baseline
byte-for-byte.

The full-BOM `PLICWidth` of 48 is not a magic number — it is checked
independently: 10 (derived non-PWM bus slice) + 6 (discrete: rtc×2, i2c×2,
gptimer4, wd0) + 2 (i2c0/1) + 4 (gptimer0-3) + 16 (gpio) + 10 (PWM slice) = **48**.

Note the SPI slave slots live in the **separate SPI cluster**
(`SPICluster_Num_Slaves`), not the MixedCluster pool, so they never contend with
rtc/i2c.

### What independence does *not* mean: PLIC bit positions are order-sensitive

`PLICVectorHandler` **prepends** each peripheral's new signals at the MSB of the
`plic_inputs` concatenation. A patch's `plic_index` only orders the signals
*within one peripheral's own batch* — it is **not** an absolute bit position.
So the final bit each source occupies, and therefore its PLIC source ID, depends
on the order peripherals appear in `soc_build_config.yaml`.

Swapping two adjacent entries yields the same vector **width** and the same set
of sources, but a different bit assignment:

| Swap | Result |
|---|---|
| `uart` ↔ `gpio` | identical content (line order only) |
| `gpio` ↔ `i2c` | identical content (line order only) |
| `sspi` ↔ `pwm` | identical content (line order only) |
| `rtc` ↔ `uart` | **`plic_inputs` bit order changes** |
| `i2c` ↔ `sspi` | **`plic_inputs` bit order changes** |
| `gptimer` ↔ `sspi` | **`plic_inputs` bit order changes** (both add PLIC sources) |
| `watchdog` ↔ `gptimer` | **`plic_inputs` bit order changes** (both add PLIC sources) |

The build still succeeds and the post-audit still passes, so nothing warns you.
**Treat the peripheral order in `soc_build_config.yaml` as part of the software
ABI**: reordering entries silently renumbers PLIC sources, and firmware that
hardcodes a source ID will target the wrong interrupt. Keep the declared order
stable, and re-check the generated `plic_inputs` line in `mixed_cluster.bsv`
whenever you change it.

When adding a new peripheral, follow the three rules: prefer touching no shared
state (Rule 3); otherwise draw shared slots/width dynamically
(`{allocated_slot}`, `plic_vector`), and widen a shared bus only for your own
signals **with a width-tolerant guard**.

---

## 18. Adding direct-connected PWM channels (PWM6–PWM9)

`pwm_v1` (`ip_bsv/pwm_v1/pwm_v1.yaml`) adds four
**direct-connected** PWM channels beside the six pinmuxed channels the board
already has. Like `uart_v1/` and `sspi_v1/`, the subdirectory
holds **only** the YAML — PWM is direct-only here, so there is no `modes:` map.

"Direct connection" means each `pwm{N}` line goes straight to a dedicated FPGA
pin through an IOBUF held permanently in output mode (`.T(1'b0)`), exactly as
`rtc0`/`rtc1` do — **not** through the pinmux. Channels `pwm0..pwm5` keep their
existing pinmux routing (cells 10, 12, 13, 16, 17, 18); those cells are untouched.

### How it works: widen the slave, don't add one

`pwm_cluster.bsv` instantiates exactly **one** `mkpwm` slave, and the underlying
IP (`devices/pwm/pwm.bsv`) is parameterized on `channels`. So rather than adding
a second cluster slave, this def widens the existing one:

```bsv
- module mkpwm(Ifc_pwm_axi4lite#(`paddr, `buswidth, `USERSPACE, 32, 6));
+ module mkpwm(Ifc_pwm_axi4lite#(`paddr, `buswidth, `USERSPACE, 32, 10));
```

and surfaces the four new outputs (`instance_id` doubles as the channel index):

```bsv
method Bit#(1) pwm6_io;                      // Ifc_pwm_cluster declaration
method pwm6_io=pwm0.io.pwm_o[6];             // binding to the widened vector
```

**The address map does not change, and there is no software regression.** The
slave decodes `Bit#(TAdd#(TLog#(TAdd#(channels,1)),4))` of the address:

| `channels` | decoded address bits | highest valid offset |
|---|---|---|
| 6 | 7 (`0x00`–`0x7F`) | `6 × 16` = `0x60` |
| 10 | 8 (`0x00`–`0xFF`) | `10 × 16` = `0xA0` |

Both fit inside the existing `PWM0Base`..`PWM0End` window
(`'h0003_0000`..`'h0003_00FF`), and channel *i* keeps its offset of *i* × 16, so
the register map of `pwm0..pwm5` is bit-for-bit identical. **No `Soc.defines`
patch is needed** — neither an address range nor a slave counter.

Verified with `bsc`: the patched `pwm_cluster.bsv` elaborates to Verilog with 0
errors, `mkpwm_cluster.v` exposes `pwm0_io`..`pwm9_io`, and the only new warnings
are the pre-existing per-channel `G0117` action-shadowing pair from the PWM IP
itself (12 warnings at 6 channels → 20 at 10, i.e. 2 per channel, unchanged in kind).

### Interrupts: all ten channels are routed, and contiguous

> **This section previously said the four new interrupts were deliberately left
> unconnected, "and why that is the point."** They are now wired. The old design
> dangled `sb_interrupt[6..9]` purely to dodge the shared-bus coupling — which was
> dodging a design flaw rather than fixing it. §21 fixes the flaw, so PWM can take
> its interrupts like any other peripheral.

The widened slave produces `sb_interrupt[6..9]`; `pwm_cluster.bsv` now surfaces
them as `pwm6..pwm9_sb_interrupt` methods (each carrying
`(*always_ready, always_enabled*)`, like `pwm0..pwm5` — required, not cosmetic,
since `mkpwm_cluster` is a `(*synthesize*)` boundary and would otherwise emit
`RDY_` companion ports), and `Soc.bsv` inserts them into the interrupt concat.

**PWM sits at the LSB end of the bus**, so the four new lines are inserted
immediately *above* `pwm5`, keeping all ten channels **contiguous at bus `[9:0]`**:

```
{ spi3, spi2, spi1, spi0, wr_ext[1:0], uart_interrupts, pwm9..pwm6, pwm5..pwm0 }
                                                        ^^^^^^^^^^^^^^^^^^^^^^^ bits [9:0]
```

so `pwm_o[i]` → bus bit `[i]` → PWM channel *i*, with no gaps. That is the whole
point of "correct ordering": channel index *is* bit index.

Widening PWM therefore shifts `uart` / `wr_ext` / `spi` **up by 4**, which is
exactly why no IP may hardcode an absolute bus index any more. `pwm_v1`
grows the derived macros by 4 (`PWMCluster_Channels` 6→10, `PWMChannelsMsb` 5→9,
`ExtIntWidth` 13→17, `ExtIntMsb` 12→16, `PLICWidth` 35→39) and everything else
follows automatically. See §21.

It remains **order-independent**: it *inserts* into the concat at a stable anchor
rather than replacing the rule, and the widths it grows are counters, not
literals. Standalone it yields `ExtIntWidth` 17 / `PLICWidth` 39; in the full BOM,
20 / 48. Both compile.

### What it changes (per channel `pwm{N}`, N ∈ 6..9)

| File | Injected |
|---|---|
| `pwm_cluster.bsv` | `channels` 6 → 10 (once); `method Bit#(1) pwm{N}_io;` and `method pwm{N}_io=pwm0.io.pwm_o[{N}];` |
| `Soc.bsv` | `chip_io` declaration `(*always_enabled,always_ready*) method Bit#(1) pwm{N}_io;` and binding `method pwm{N}_io = pwm_cluster.pwm{N}_io;` |
| `fpga_top.v` | `output pwm{N},`, `wire wire_pwm{N};`, `.pwm{N}_io(wire_pwm{N}),` in the SoC port map, and `IOBUF pwm{N}_io_inst(.O(),.IO(pwm{N}),.I(wire_pwm{N}),.T(1'b0));` |
| `constraints.xdc` | one dedicated FMC pin (LVCMOS12, with fallback) |

`chip_io` is declared `(*prefix=""*)` in `DebugSoc.bsv`, so the BSV method
`pwm{N}_io` surfaces as the bare Verilog port `pwm{N}_io` — hence the port map
above. The `always_ready` attribute suppresses the `RDY_` companion port, so the
`output` connects straight through the IOBUF.

### Pin choice (nexys_video)

The primaries are the free FMC-LA clock-capable pins. They are claimed by no
other peripheral and are not listed as anyone else's `fallback_pin`, so the XDC
conflict resolver never has to move them. They sit in the FMC-LA bank, whose VADJ
rail runs at 1.2 V — hence `LVCMOS12`, matching `i2c1`, `gpio_29..31` and
`spi2`/`spi3`.

| Channel | Pin | Package pin | Fallback |
|---|---|---|---|
| `pwm6` | `fmc_la00_cc_p` | K18 | `fmc_la_p[21]` |
| `pwm7` | `fmc_la00_cc_n` | K19 | `fmc_la_n[21]` |
| `pwm8` | `fmc_la01_cc_p` | J20 | `fmc_la_p[22]` |
| `pwm9` | `fmc_la01_cc_n` | J21 | `fmc_la_n[22]` |

### Config (`soc_build_config.yaml`)

```yaml
- name: pwm_v1
  def_path: ip_bsv/pwm_v1/pwm_v1.yaml
  base_context:
    xdc_file: '{board_dir}/constraints.xdc'
    verilog_buffer_type: IOBUF
    verilog_buffer_tristate: 1'b0     # IOBUF held in permanent output mode
    pwm_total_channels: 10            # 6 existing (pinmuxed) + 4 new (direct)
    pwm_io_standard: LVCMOS12
  instances:
  - instance_id: '6'                  # instance_id == PWM channel index
    pwm_pin: fmc_la00_cc_p
    pwm_fallback: fmc_la_p[21]
  # ... 7, 8, 9
```

To add a fifth direct channel, bump `pwm_total_channels` to 11, append an
`instance_id: '10'` block with its pin, and re-run. Check the address table above
first: at 16 channels the decoded offset reaches `0x100` and would overflow the
`PWM0Base`..`PWM0End` window, so `PWM0End` (and `PWMClusterEnd`) must grow too.

### Idempotency note

The channel-count patch is a `replace` whose anchor requires the literal
`, 32, 6)`. Once applied, that anchor is gone — so the guard must be a
`skip_if_matches` regex that recognises "already widened to anything other than
6", not a `skip_if_contains`:

```yaml
skip_if_matches: 'module mkpwm\(Ifc_pwm_axi4lite#\(`paddr, `buswidth, `USERSPACE, 32, (?!6\))\d+\)\);'
```

The engine checks skip guards **before** searching for the anchor, so a re-run
skips cleanly instead of reporting `Anchor pattern not found` (§7).

---

## 19. Adding a direct-connected GPTimer (GPTimer4)

`gptimer_v1` (`ip_bsv/gptimer_v1/gptimer_v1.yaml`)
adds **one** general-purpose timer beside the four the board already has
(`gptimer0..gptimer3`). Like `uart_v1/`, `sspi_v1/` and
`pwm_v1/`, the subdirectory holds **only** the YAML — GPTimer is
direct-only here, so there is no `modes:` map.

### No new source, no new bsvpath entry

This is the cheapest kind of IP def to write, because everything it needs already
exists in a pristine tree:

- `devices/gptimer/gptimer.bsv` is the IP, and `devices/gptimer/` is already on
  the `bsvpath` (used by `gptimer0..3`), so there is **no** `bsvpath` patch;
- `mixed_cluster.bsv` already carries the `(*synthesize*) module
  mkgptimer#(Clock ext_clk)` wrapper, so there is **no** wrapper patch;
- `import gptimer::*;` is already present in both `mixed_cluster.bsv` and
  `Soc.bsv`, so there is **no** import patch.

The def therefore only instantiates a fifth `mkgptimer`, gives it an address
window and a cluster slave slot, routes its interrupt, surfaces its
`Ifc_gptimer_io` out to the pins, and constrains them.

### "Direct connection" here means no IOBUF either

`gptimer0..3` are declared in `fpga_top.v` as a plain `input`/`output` **pair**,
not as bidirectional pads:

```verilog
input  gptimer4_in,
output gptimer4_out,
```

and are wired straight into the SoC instance:

```verilog
.gptimer4_io_input_signal_signal_in(gptimer4_in),
.gptimer4_io_timer_out(gptimer4_out),
```

So unlike `rtc0`/`rtc1` (§ the RTC def) or `pwm6..9` (§18), which need an IOBUF
held in permanent output mode, `gptimer4` needs **no buffer instantiation at
all** — hence no `verilog_buffer_type` / `verilog_buffer_tristate` in its
`base_context`. `chip_io` is declared `(*prefix=""*)` in `DebugSoc.bsv`, which is
why the BSV sub-interface methods surface as those bare Verilog port names.

### Address map

`gptimer4` takes the first free 256-byte window above `GPTimer3End`:

| Macro | Value |
|---|---|
| `GPTimer4Base` | `'h0004_0800` |
| `GPTimer4End` | `'h0004_08FF` |

`RTC0`/`RTC1` occupy `'h0004_0600`/`'h0004_0700` when `rtc_v1` is enabled, so this
window never collides whether or not `rtc_v1` is present. The windows of
`gptimer0..3` are untouched, so existing timer firmware is unaffected.

### Interrupt: a discrete PLIC source, not a bus bit

`gptimer4.sb_interrupt` is inserted into the `plic_inputs` concatenation by the
width-agnostic `plic_vector` handler, and the `mkplic` wrapper width re-syncs —
exactly the mechanism `rtc_v1` and `i2c_v1` use (Rule 1, §17). It is a **discrete**
signal, so it does **not** consume a bit of the shared `wr_external_interrupts`
bus and never touches the `Bit#(13)` interrupt-method width. That is what frees it
of the ordering coupling that ties `uart_v1` (bus 13→14) to `sspi_v1`
(bus 14→16), and it is why the def carries no `ext_int_bit`.

Consequently `gptimer_v1` may be listed **anywhere** in
`automated_peripherals`. Its slave number is drawn from the err-slot at apply time
via `{allocated_slot}`: standalone it lands on slot 10, after `rtc`+`i2c` on 14.

> As always (§17), `plic_index` only orders signals *within* this peripheral's own
> batch. It is not an absolute bit position — moving the entry renumbers PLIC
> source IDs.

### What it changes

| File | Injection |
|---|---|
| `Soc.defines` | `GPTimer4Base`/`GPTimer4End`; `GPTimer4_slave_num` = `{allocated_slot}`; `MixedCluster_Num_Slaves` and `_err_slave_num` each +1 |
| `mixed_cluster.bsv` | `Ifc_gptimer_io gptimer4_io` decl; decoder arm; `let gptimer4 <- mkgptimer(ext_clk);`; fabric `mkConnection`; `interface gptimer4_io = gptimer4.io;`; `gptimer4.sb_interrupt` into `plic_inputs`; `mkplic` width |
| `Soc.bsv` | `gptimer4_io` chip_io decl + bind to `mixed_cluster` |
| `TbSoc.bsv` | `soc.chip_io.gptimer4_io.input_signal(1'b0);` |
| `fpga_top.v` | `input gptimer4_in,` / `output gptimer4_out,` + the two SoC port maps |
| `constraints.xdc` | pin + `IOSTANDARD` for both ports |

### Why `TbSoc.bsv` is patched

An `(*always_enabled*)` method gets **no enable wire** in the generated Verilog,
so its caller must invoke it on *every* clock cycle — there is no way to say "not
this cycle". `bsc` enforces that at the instantiation site.

`Ifc_gptimer_io.input_signal` is such a method, so once `gptimer4_io` is exposed
on `chip_io`, a testbench instantiating the SoC must drive it or `bsc` reports
`(G0066) … requires the following methods to be always enabled`. `gptimer0..3`
are tied off in `rule connect_gpt_connection`, so the patch appends one line
there. `uart_v1` and `sspi_v1` tie off their inputs the same
way. A peripheral exposing only **outputs** (like `rtc`) needs no TbSoc patch.

Note the attribute does not have to be written in `Soc.bsv`. `gpio_v1` injects
`(*always_enabled,always_ready*)` explicitly above each `method Action gpio_N`,
whereas `i2c_v1` inherits it for free: the attribute sits on the `I2C_out`
**interface type** in `devices/i2c_v2/i2c.bsv`. Confirm the real situation by
grepping the generated Verilog for `EN_` ports — `mkDebugSoc.v` has none on
`chip_io`, so every chip_io input there is always-enabled.

> **Known gap (pre-existing, dormant — not introduced here):** `gpio_v1` and
> `i2c_v1` expose `always_enabled` inputs (`chip_io_gpio_32..47`,
> `chip_io_i2c{2,3}_out_*_in`) without adding TbSoc tie-offs, so `bsc -g mkTbSoc`
> fails to elaborate when they are enabled.
>
> **This breaks no build today.** `TOP_MODULE := mkDebugSoc` and
> `TOP_FILE := DebugSoc.bsv`, so *both* the FPGA flow and `quick_build_sim` reach
> the top through `generate_verilog` → `mkDebugSoc` → Verilator. `TbSoc.bsv` is
> named only in `SYNC_BSV_FILES` (a copy list); no target compiles it, nothing
> imports it, and it is absent from `depends.mk`. The gap would surface only if
> `TOP_MODULE` were repointed at `mkTbSoc`, or a Bluesim testbench flow revived.
> `gptimer_v1` keeps `TbSoc.bsv` consistent regardless, at the cost of
> one injected line.

### Pin choice (nexys_video)

`fmc_la17_cc_n` and `fmc_la18_cc_n` are the only two FMC-LA pins claimed by no
other peripheral as either a primary **or** a fallback, so the XDC conflict
resolver never has to move them. They sit in the FMC-LA bank, whose VADJ rail runs
at 1.2 V — hence `LVCMOS12`, matching `gptimer2`/`gptimer3`, `i2c1` and
`gpio_29..31`.

| Port | Pin | Package pin | Fallback |
|---|---|---|---|
| `gptimer4_in` | `fmc_la17_cc_n` | B18 | `fmc_la_n[06]` |
| `gptimer4_out` | `fmc_la18_cc_n` | C17 | `fmc_la_n[08]` |

Every PMOD pin (JA/JB/JC) is already taken on this board, which is why a
direct-routed peripheral lands on the FMC-LA bank.

### Config (`soc_build_config.yaml`)

```yaml
- name: gptimer_v1
  def_path: ip_bsv/gptimer_v1/gptimer_v1.yaml
  base_context:
    xdc_file: '{board_dir}/constraints.xdc'
    gptimer_io_standard: LVCMOS12     # FMC-LA VADJ bank at 1.2 V
  instances:
  - instance_id: '4'                  # instance_id == timer index
    slave_id_macro: GPTimer4_slave_num
    base_macro: GPTimer4Base
    base_addr: '0004_0800'
    end_macro: GPTimer4End
    end_addr: '0004_08FF'
    connect_to_plic: true
    plic_index: 42                    # next free index after spi3 = 41
    gptimer_in_pin: fmc_la17_cc_n
    gptimer_out_pin: fmc_la18_cc_n
    gptimer_in_fallback: fmc_la_n[06]
    gptimer_out_fallback: fmc_la_n[08]
```

To add a sixth timer, append an `instance_id: '5'` block with the next free
address window, a free `plic_index`, and two free pins. Nothing else changes: the
slave slot and the PLIC width are both allocated dynamically.

### Idempotency

Every patch carries a guard, so re-running any phase is a no-op:

- literal patches guard on the injected text (`skip_if_contains`);
- the two counter patches guard on `` `define GPTimer4_slave_num ``, **not** on
  `{calculated_val}` — the latter is only known at apply time, so a guard built
  from it could never match and the counters would increment on every re-run (§7);
- the `plic_vector` patch needs no guard: the handler re-parses the concatenation
  and skips a signal already present. It then leaves `shared_state['plic_width']`
  unset, which makes the dependent `mkplic` wrapper patch skip too;
- the XDC patch is a no-op when the target pin already holds that very port.

Verified: applied alone and in the full BOM, three times each from a pristine
tree, producing a byte-identical tree after the first pass, with
`pre_validate_automation` / `post_validate_automation` passing and
`make restore_autointeg_patches` returning the tree to the baseline byte-for-byte.
`mkmixed_cluster`, `mkSoc` and `mkDebugSoc` all elaborate to Verilog.

---

## 20. Adding a direct-connected watchdog (wd0)

`watchdog_v1` (`ip_bsv/watchdog_v1/watchdog_v1.yaml`)
adds **one** watchdog timer to the mixed cluster. Like the other `_nopinmux`
directories it holds **only** the YAML.

### Reusing RTL that was wired into nothing

`devices/watchdog/watchdog.bsv` has existed in the tree all along, but nothing
instantiated it. Unlike GPTimer (§19), which needed no new plumbing at all, the
watchdog needs three pieces of scaffolding that the def injects:

1. **`bsvpath`** — `devices/watchdog/` is appended so `bsc` can find the package.
2. **`import watchdog :: *;`** in `mixed_cluster.bsv`.
3. **A `(*synthesize*)` wrapper**, because the raw module cannot be a synthesis
   boundary (see below).

No `.bsv` is added — the RTL is reused as-is.

> **Why patching `bsvpath` is enough.** `bsc` is invoked with `-p $(BSVINCDIR)`
> from `makefile.inc`, which does **not** list `devices/watchdog/`. But
> `makefile.inc` is *generated*: `soc_config/configure.py` builds
> `BSVINCDIR = ".:%/Libraries:" + top_dir` and then appends **every line of the
> `bsvpath` file**. `run_setup_build` / `run_setup_fpga` regenerate it, and
> `quick_build_automated` runs `update_bsvpath` *before* `run_setup_build`. So
> `makefile.inc` is never patched directly — patch `bsvpath` and it follows.

### The synthesis wrapper, and the reset-domain trap

The IP's AXI4-Lite module is:

```bsv
module mkwatchdog_axi4lite(Reset ext_rst, Integer wd_control,
                           Integer reset_cycles, Ifc_watchdog_axi4lite#(...) ifc)
```

Its `Integer` parameters are compile-time only, so it cannot itself carry
`(*synthesize*)`. The def injects a wrapper that pins them down:

```bsv
(*synthesize*)
module mkwdt(Ifc_watchdog_axi4lite#(`paddr, `buswidth, `USERSPACE));
  let wd_rst <- exposeCurrentReset;
  let ifc();
  mkwatchdog_axi4lite#(wd_rst, 0, 100) _temp(ifc);
  return ifc;
endmodule
```

**`exposeCurrentReset` inside the wrapper is load-bearing.** The obvious
alternative — `module mkwdt#(Reset ext_rst)(...)`, taking the reset as a module
argument like `mkrtc#(Clock ext_clk)` does — compiles, but makes `bsc` emit two
`(G0043)` multiple-reset warnings on `rl_capture_write_req`. Internally
`mkwatchdog_axi4lite` instantiates its core with `reset_by ext_rst` while the AXI
transactor uses the default reset; when `ext_rst` arrives as an opaque port, those
look like two distinct reset domains and the rule straddles both. Sourcing the
reset from inside the wrapper makes them provably the same reset, and the
warnings disappear. Both variants were compiled to confirm this.

### Parameters

| Parameter | Value | Meaning |
|---|---|---|
| `wd_control` | `0` | bit[0] enable, bit[1] reset-vs-interrupt mode, bit[2] lock. `0` = **disabled at power-on**, so it cannot fire before firmware configures it |
| `reset_cycles` | `100` | length of the reset pulse |

Both are baked into `mkwdt` and remain runtime-writable at offsets `0x8` and
`0x10` of the watchdog's window.

### `reset_out` goes to a pin, not the reset tree

`watchdog.bsv` documents `reset_out` as *"active low reset out which would reset
the SoC"*. This def deliberately routes it **only to an observation pin**:

```verilog
output wd0_reset_out,
...
.wd0_reset_out(wd0_reset_out),
```

It is a plain top-level `output` wired straight to the SoC instance — no pinmux,
and no IOBUF (it is a pure output, not a bidirectional pad), exactly like
`gptimer0..3`. `chip_io` is `(*prefix=""*)` in `DebugSoc.bsv`, so the BSV method
surfaces as the bare port `wd0_reset_out`; `always_ready` suppresses the `RDY_`
companion.

Feeding `reset_out` back into the SoC reset tree would be a **separate, invasive
change**: it touches reset generation in `Soc.bsv` / `DebugSoc.bsv` /
`fpga_top.v`, needs a *raw external* reset for the watchdog's own `ext_rst` so the
watchdog survives the reset it triggers, and risks an unrecoverable reset loop if
`wd_control` ever defaults to enabled. Keeping `reset_out` on a pin is what makes
this peripheral independent of the reset infrastructure. Software uses the PLIC
interrupt instead — that is exactly what `wd_control[1] == 0` ("interrupt mode")
is for.

### Address map

| Macro | Value |
|---|---|
| `WatchDog0Base` | `'h0004_0900` |
| `WatchDog0End` | `'h0004_09FF` |

The first free 256-byte window above `GPTimer4End` (`'h0004_08FF`). Register
offsets inside it: `0x0` = watchdog cycles (writing arms it), `0x8` = control,
`0x10` = reset cycles, `0x18` = kick.

### Interrupt: a discrete PLIC source

`wd0.interrupt` is inserted into `plic_inputs` by the width-agnostic
`plic_vector` handler and the `mkplic` wrapper width re-syncs — the same mechanism
`rtc_v1`, `i2c_v1` and `gptimer_v1` use (Rule 1, §17). It is a
**discrete** signal, so it never consumes a bit of the shared
`wr_external_interrupts` bus and never touches the `Bit#(13)` interrupt-method
width. Hence no `ext_int_bit`, and no ordering coupling to `uart_v1` / `sspi_v1`.

`watchdog_v1` may therefore be listed **anywhere** in
`automated_peripherals`. Its slave number comes from `{allocated_slot}` at apply
time: standalone it lands on slot 10; in the full BOM on 15.

### What it changes

| File | Injection |
|---|---|
| `bsvpath` | `devices/watchdog/` |
| `Soc.defines` | `WatchDog0Base`/`WatchDog0End`; `WD0_slave_num` = `{allocated_slot}`; `MixedCluster_Num_Slaves` and `_err_slave_num` each +1 |
| `mixed_cluster.bsv` | `import watchdog :: *;`; `mkwdt` wrapper; `method Bit#(1) wd0_reset_out` decl; decoder arm; `let wd0 <- mkwdt();`; fabric `mkConnection`; `method wd0_reset_out = wd0.reset_out;`; `wd0.interrupt` into `plic_inputs`; `mkplic` width |
| `Soc.bsv` | `wd0_reset_out` chip_io decl + bind to `mixed_cluster` |
| `fpga_top.v` | `output wd0_reset_out,` + the SoC port map |
| `constraints.xdc` | pin + `IOSTANDARD` |

**No `TbSoc.bsv` patch.** `reset_out` is an *output*, and `interrupt` never
reaches `chip_io`, so the watchdog adds no `always_enabled` **input** and the
testbench needs no tie-off (contrast §19).

### Pin choice (nexys_video)

Every FMC-LA pin is now claimed by some peripheral as a primary **or** a fallback,
so there is no completely unclaimed pin left. `fmc_la_n[19]` is chosen because it
is only ever `gpio_46`'s fallback, and `gpio_46`'s primary (`fmc_la_n[09]`) is
always free — so that fallback can never fire and the pin stays ours in every
ordering.

| Port | Pin | Package pin | Fallback |
|---|---|---|---|
| `wd0_reset_out` | `fmc_la_n[19]` | A19 | `fmc_la_n[20]` |

FMC-LA sits in the VADJ bank at 1.2 V — hence `LVCMOS12`, matching `gptimer2/3`,
`i2c1` and `gpio_29..31`.

### Config (`soc_build_config.yaml`)

```yaml
- name: watchdog_v1
  def_path: ip_bsv/watchdog_v1/watchdog_v1.yaml
  base_context:
    xdc_file: '{board_dir}/constraints.xdc'
    wd_io_standard: LVCMOS12
  instances:
  - instance_id: '0'
    slave_id_macro: WD0_slave_num
    base_macro: WatchDog0Base
    base_addr: '0004_0900'
    end_macro: WatchDog0End
    end_addr: '0004_09FF'
    connect_to_plic: true
    plic_index: 43                    # next free index after gptimer4 = 42
    wd_reset_pin: fmc_la_n[19]
    wd_reset_fallback: fmc_la_n[20]
```

A second watchdog would need a second `instance_id`, a free address window, a free
`plic_index` and a free pin — but also a second `mkwdt`-style wrapper, since
`mkwdt` is `apply_once` and takes no instance parameters.

### Idempotency

Every patch carries a guard:

- literal patches guard on the injected text (`skip_if_contains`);
- the `bsvpath` and `mkwdt` wrapper patches are `apply_once`, guarded on
  `devices/watchdog/` and `module mkwdt(` respectively;
- the two counter patches guard on `` `define WD0_slave_num ``, **not** on
  `{calculated_val}` — the latter is only known at apply time, so a guard built
  from it could never match and the counters would grow on every re-run (§7);
- the `plic_vector` handler is delta-aware and, once its signal is present, leaves
  `shared_state['plic_width']` unset so the dependent `mkplic` patch skips too.

Verified: applied alone and in the full BOM, three times each from a pristine
tree, byte-identical after the first pass, with `pre_validate_automation` /
`post_validate_automation` passing, `make restore_autointeg_patches` rewinding the
tree — **including `bsvpath`** — to the baseline byte-for-byte, and
`mkmixed_cluster` + `mkDebugSoc` elaborating to Verilog with no new warnings.

---

## 21. The derived interrupt bus (and the two bugs that forced it)

This section explains why `mixed_cluster.bsv` contains no interrupt-bus widths any
more, and why `Soc.defines` grew five sizing macros. Read it before adding any
peripheral that raises an interrupt.

### The bus

`Soc.bsv`'s `connect_interrupt_lines` packs every cluster's sideband interrupts
into one vector, MSB → LSB:

```
{ spi.., spi1, spi0, wr_ext_interrupts[1:0], uart_interrupts, pwm..pwm0 }
```

`mixed_cluster.bsv` receives it as `wr_external_interrupts` and feeds it to the
PLIC, splitting it into a **non-PWM** part and a **PWM** part.

Two structural facts make hardcoded indices unsafe:

1. **PWM is at the LSB end.** Adding PWM channels shifts *everything* above.
2. **`uart_interrupts` is below `spi`.** Growing it 3→4 for UART3 pushes
   `spi0`/`spi1` up by one.

### Bug 1 — `sspi_v1` did not compile standalone

`sspi_v1` widened the bus with an anchor matching the literal `Bit#(14)` — a width
that only exists *after* `uart_v1` has run. Applied alone, the widening silently
never fired: `wr_external_interrupts` stayed `Bit#(13)` while the concat fed it 15
signals, and `plic_inputs` still read `[14]`/`[15]`. `bsc` rejected it outright:

```
Error: "mixed_cluster.bsv", line 170: (S0015)
  index 14 out-of-range (bit extraction - high index)
```

So `sspi_v1` had a silent, undocumented dependency on `uart_v1`. This went
unnoticed because the independence matrix in §17 was built from
`pre/post_validate_automation` runs — **and the audits do not compile.**

### Bug 2 — `plic_inputs[13]` was not UART3

`uart_v1` injected `wr_external_interrupts[13]` into `plic_inputs`, commented as
"UART3's bit". But once `uart_interrupts` grew to 4 bits, `spi1` moved *to* bit 13.
The bit labelled UART3 was really SPI1. No interrupt was *lost* (all 16 bits still
reached the PLIC), but the source IDs did not mean what the comments said.

Both bugs are the same root cause: **absolute bit indices in a bus whose layout
shifts.**

### The fix: derive everything

`Soc.defines` carries five macros. Their baseline values reproduce the original
hardcoded widths exactly, so a pristine tree is **bit-for-bit unchanged** (the
generated `mkmixed_cluster.v` was diffed before/after the refactor: identical apart
from the timestamp comment).

```
`define PWMCluster_Channels 6    `define PWMChannelsMsb 5
`define ExtIntWidth        13    `define ExtIntMsb      12
`define PLICWidth          35
```

`mixed_cluster.bsv` uses them and names no literal:

```bsv
method Action interrupts(Bit#(`ExtIntWidth) inp);
Wire#(Bit#(`ExtIntWidth)) wr_external_interrupts <- mkDWire('d0);

Bit#(`PLICWidth) plic_inputs = {
  wr_external_interrupts[`ExtIntMsb : `PWMCluster_Channels],  // ALL non-PWM bits
  <discretes>, i2c1.isint, i2c0.isint, gptimer3..0, lv_gpio_intr,
  wr_external_interrupts[`PWMChannelsMsb : 0]                 // ALL PWM channels
};

module mkplic(Ifc_plic_axi4lite#(`paddr, `buswidth, `USERSPACE, `PLICWidth, 2, 7));
```

Each IP grows the macros with an ordinary `cluster_config` counter
(`increment_by_instances`, which conveniently equals the number of bits it adds):

| IP | instances | grows |
|---|---|---|
| `pwm_v1` | 4 | `PWMCluster_Channels` +4, `PWMChannelsMsb` +4, `ExtIntWidth` +4, `ExtIntMsb` +4, `PLICWidth` +4 |
| `uart_v1` (both modes) | 1 | `ExtIntWidth` +1, `ExtIntMsb` +1, `PLICWidth` +1 |
| `sspi_v1` | 2 | `ExtIntWidth` +2, `ExtIntMsb` +2, `PLICWidth` +2 |
| `rtc` / `i2c` / `gptimer` / `watchdog` | 2/2/1/1 | `PLICWidth` += instances *(discrete PLIC sources)* |

### Why five macros and not one expression

BSV accepts **neither** arithmetic inside `Bit#(...)` **nor** arithmetic in a
static bit-slice bound. Both must be literals:

```bsv
Bit#(`A + `B)  x;              // P0005 — "Unexpected `+'"
w[`ExtIntWidth-1 : `PWMC]      // T0035 — "Bit vector of unknown size"
```

Nor can the macro itself hold the expression: `` `define EIW (`A + `B) `` makes bsc
read `(` as a **macro parameter list** (P0146). Hence separate `…Msb` macros
holding the pre-computed bounds, each maintained by its own counter.

### Consequences for IP authors

- **Never** write a bus width or a bit index into `mixed_cluster.bsv`. Grow a macro.
- **Insert** into `connect_interrupt_lines` at a stable anchor. Never `replace` the
  rule — `uart_v1` and `sspi_v1` both did, and whichever ran last clobbered the
  other (§17 Rule 2b).
- A discrete PLIC source (not on the bus) only needs `PLICWidth` +N and a
  `plic_vector` patch. The `plic_vector` handler now **detects the macro form** and
  leaves the width alone; it only inserts the signal.
- **Compile every subset.** A green `post_validate_automation` proves the text
  landed, not that the RTL elaborates.

### Verification

All 8 peripherals now elaborate `mkSoc` standalone (previously `sspi_v1` did not).
`uart`+`sspi` and `pwm`+`sspi` produce identical output in **either** order.
Full BOM: `ExtIntWidth` 20, `PLICWidth` 48, `mkDebugSoc` elaborates with no new
warning classes, and `plic_inputs` contains **zero** absolute bus indices.

---

## 22. Output ordering and layout

Generated code has to be *readable*, not just correct. Two things drive that: which
anchor a patch owns (§6), and — for `Soc.defines` — being able to emit a define far
away from the counter that computed it.

### `Soc.defines`: slave numbers sit with their family

Slave slots are allocated dynamically (`{allocated_slot}`, §17 Rule 1), and that
value is computed inside `ClusterConfigHandler` while it rewrites the
`MixedCluster_err_slave_num` counter. Every peripheral therefore *had* to emit its
`X_slave_num` define right there, which piled them all into one block above the err
slot, ordered by apply order:

```
`define Pinmux_slave_num 9
`define RTC0_slave_num 10        <- everything lumped here
`define RTC1_slave_num 11
`define I2C2_slave_num 12
`define I2C3_slave_num 13
`define GPTimer4_slave_num 14
`define WD0_slave_num 15
`define MixedCluster_err_slave_num 16
```

`ClusterConfigHandler` now **publishes `allocated_slot` back onto the instance
dict**, so any *later* patch of the same peripheral can template on it. A plain
regex patch can then place the define next to its family:

```
`define I2C0_slave_num 0
`define I2C1_slave_num 1
`define I2C2_slave_num 12       <- with the I2Cs
`define I2C3_slave_num 13
`define GPTimer0_slave_num  2
...
`define GPTimer3_slave_num  5
`define GPTimer4_slave_num 14   <- with the GPTimers
`define PLIC_slave_num 6
...
`define RTC0_slave_num 10       <- rtc/watchdog have no family, so they
`define RTC1_slave_num 11          group before the err slot
`define WD0_slave_num 15
`define MixedCluster_err_slave_num 16
```

**The values are deliberately non-monotonic.** Slots 2–9 are taken by
`gptimer0..3`/`PLIC`/`GPIO`/`XADC`/`Pinmux` in the baseline, so `I2C2` *cannot* be 2
— it takes the next free err-slot. Grouping by family and sorting by value are
mutually exclusive here; the family grouping is what makes the map legible, and the
numbers remain correct in every ordering.

> The relocating patch **must be listed after** the `soc_defines_err_slave`
> `cluster_config` patch, which is what publishes `{allocated_slot}`. On a re-run
> the counter skips (guarded), so `allocated_slot` is never set — but the
> relocating patch's own guard (`` `define {slave_id_macro} ``) fires first, so the
> template is never resolved and nothing breaks.

Address windows follow the same one-anchor-one-owner rule: `gptimer4` after
`GPTimer3End`, `i2c2/i2c3` after `I2C1End`, `rtc` after `PinmuxEnd`, `watchdog`
before `PLICBase`. Previously three IPs all anchored on `GPTimer3End`.

### Baseline indentation defects that were fixed

These were **upstream, pre-existing** — not caused by any patch — but injected code
landed beside them and inherited their style, so they are now fixed in the board
source:

| File | Defect |
|---|---|
| `mixed_cluster.bsv` | `module mkgptimer#(...)` sat at **column 0** while its `(*synthesize*)` was at 2; its body was at 2 and `endmodule` at 0 |
| `mixed_cluster.bsv` | `mki2c`'s first two body lines were tab-indented |
| `mixed_cluster.bsv` | `method Action interrupts(...)` (decl **and** impl) was tab-indented |
| `mixed_cluster.bsv` | a 2-line blank run (one line whitespace-only) after `interface gptimer3_io = …` |
| `fpga_top.v` | a 4-line blank run (one tab-only) after `output gptimer3_out,` |
| `fpga_top.v` | `.spi0_io_miso_*` port-map lines were **tab**-indented while every sibling used 8 spaces — this is why the injected `spi2`/`spi3` maps looked misaligned |
| `fpga_top.v` | `.gptimer[0-3]_io_timer_out(...)` lines were tab-indented with trailing tabs |

`scripts/indent_tools.py audit --baseline … --current …` reports **0 violations**
across `Soc.bsv`, `Soc.defines`, `mixed_cluster.bsv`, `spi_cluster.bsv`,
`uart_cluster.bsv`, `pwm_cluster.bsv`, `pinmux.bsv` and `TbSoc.bsv` after a full run.

### Verification

Full BOM applies cleanly, is byte-identical after three applies, elaborates
`mkDebugSoc` with zero errors, and `make restore_autointeg_patches` rewinds
byte-for-byte. All 8 peripherals still pass pre/post audit and are idempotent
standalone. Reversing the **entire** peripheral list leaves the layout unchanged
(families still grouped, `gptimer4` still adjacent to `gptimer3`); only the
allocated slot *numbers* shift, which is the documented `{allocated_slot}`
behaviour, not a regression.

---

## 23. The PLIC source vector, and why `spi1_io` is commented out

### The PLIC vector is grouped, not prepended

`plic_inputs` in `mixed_cluster.bsv` used to be a single long line, and every IP
**prepended** its signal at the MSB via the `plic_vector` handler. Order was just
reverse-apply-order, so the vector read:

```bsv
{wd0.interrupt, gptimer4.sb_interrupt, i2c2.isint, i2c3.isint, rtc1…, rtc0…,
 wr_external_interrupts[`ExtIntMsb : `PWMCluster_Channels], i2c1.isint, i2c0.isint,
 gptimer3…, gptimer2…, gptimer1…, gptimer0…, lv_gpio_intr,
 wr_external_interrupts[`PWMChannelsMsb : 0]}
```

Families were split (`i2c2`/`i2c3` nowhere near `i2c0`/`i2c1`; `gptimer4` nowhere
near `gptimer0..3`), `i2c2` came *before* `i2c3`, and the external-interrupt bus was
**cut in half** — its PWM bits at the far LSB end, everything else stranded in the
middle. It is now written one-signal-per-line with explicit group anchors:

```bsv
Bit#(`PLICWidth) plic_inputs= {
  // ---- discrete PLIC sources (added by automation) ----
  wd0.interrupt,                            // family-less discretes at the MSB
  rtc1.rtc_sb_interrupt,
  rtc0.rtc_sb_interrupt,
  gptimer4.sb_interrupt,                    // gptimer4..0, contiguous, descending
  gptimer3.sb_interrupt,
  … gptimer0.sb_interrupt,
  i2c3.isint,                               // i2c3..0, contiguous, descending
  i2c2.isint,
  i2c1.isint,
  i2c0.isint,
  lv_gpio_intr,
  wr_external_interrupts[`ExtIntMsb : 0]};  // the WHOLE bus, one undivided slice
```

Three rules:

1. **Every family is contiguous and descending.** `gptimer_v1` inserts
   *before* `gptimer3.sb_interrupt,`; `i2c_v1` inserts *before* `i2c1.isint,`. Both
   use `reverse_instances`, so a two-instance IP emits `3, 2` — descending.
2. **Family-less discretes (rtc, watchdog) sit at the MSB**, anchored on the
   `// ---- discrete PLIC sources ----` marker line.
3. **The external-interrupt bus is one undivided slice** at the LSB end. Splitting
   it was an artefact of the old hardcoded `[12:6]` / `[5:0]` slices (§21); with
   `[`ExtIntMsb : 0]` the whole bus stays together and *nothing* needs to know
   where PWM sits inside it.

The `plic_vector` **handler is no longer used**. It existed to prepend-and-resize;
now the width is derived (`` `PLICWidth ``, §21) and placement is a plain regex
insert, so ordinary patches do the job. Width still checks out independently:
`1 (wd) + 2 (rtc) + 5 (gptimer) + 4 (i2c) + 16 (gpio) + 20 (bus) = 48 = `PLICWidth`.

> Two family-less discretes share the MSB marker, so `wd0` vs `rtc` ordering follows
> apply order. In the shipped config you get `wd0, rtc1, rtc0`. Reordering the
> peripheral list swaps them — but reordering already renumbers PLIC source IDs
> anyway (§17), so this changes nothing that was not already order-sensitive.
> `PWMCluster_Channels` / `PWMChannelsMsb` survive only as documentation of the
> channel count; the PLIC vector no longer reads them.

### `spi1_io` is commented out — and spi1 works fine

This *looks* like a disabled peripheral. It is not. **spi1 is fully functional**:

| Stage | Evidence (`spi_cluster.bsv` / `Soc.bsv`) |
|---|---|
| instantiated | `let spi1 <- mkspi();` |
| on the AXI fabric | `mkConnection(fabric.v_to_slaves[`SPI1_slave_num], spi1.slave);` |
| address-decoded | `else if(addr>= `SPI1Base && addr<= `SPI1End) slave_num = `SPI1_slave_num;` |
| interrupt to PLIC | `spi_cluster.spi1_sb_interrupt,` in `connect_interrupt_lines` |
| reaches pins | 8 `put()` output lines into `pinmuxtop_peripheral_side.mspi`, 4 input `get()`s back out |

**spi1 is the pinmuxed SPI.** It reaches its pins through the pinmux `mspi` cell
(iocells 17–20), which is why `fpga_top.v` contains **zero** `spi1` references
(versus 21 for `spi0`). The commented `chip_io` lines would add a *second,
dedicated* pin path that spi1 does not use.

Uncommenting them is **not compilable**. `rule connect_pinmux_peripheral_input_lines`
already calls `spi_cluster.spi1_io.miso_in(...)`, `.mosi_in(...)`, `.sclk_in(...)`,
`.ncs_in0(...)`. Exporting `spi1_io` on `chip_io` re-exports those same `Action`
methods to the chip boundary, giving them a second caller. `bsc` cannot schedule
that, so the rule's condition collapses to False and every `always_enabled` method
it feeds goes undriven:

```
Error: "Soc.bsv", line 448: (G0066)
  Instance `mixed_cluster' requires the following methods to be always enabled,
  but the conditions for executing the methods are always False:
    gpio_io_gpio_in, pinmuxtop_peripheral_side_mspi_clk_in_get,
    pinmuxtop_peripheral_side_mspi_miso_in_get, … uart1_rx_get, uart2_rx_get …
```

So the choice is *pinmux route* **or** *dedicated chip_io port* — never both. spi1
keeps the pinmux route.

What **was** wrong is that `sspi_v1` anchored its `spi2`/`spi3` injections on
`spi0_io`, so they landed **above** the commented `spi1` line and the block read
`spi0, spi2, spi3, spi1`. The anchors now sit on the commented `spi1` lines, so the
reading order is `spi0, spi1, spi2, spi3` in both the declaration and the binding
block.

> **To direct-connect spi1 later** (if you ever want the `mspi` pinmux cells back):
> delete the 8 `put()` output lines and the 4 input-driving lines in `Soc.bsv`,
> uncomment the two `chip_io` lines, and add 4 dedicated pins + IOBUFs in
> `fpga_top.v`/XDC — the same shape as `spi0`. It is a deliberate design change, not
> a comment removal.

### Baseline indentation fixed (round 2)

More pre-existing tab contamination, now normalised in the board source:

| File | Defect |
|---|---|
| `mixed_cluster.bsv` | `interface Ifc_gptimer_io gptimer0..3_io;` were **tab + 2 spaces** — this is the "line 68" mismatch, made obvious once `gptimer4_io` was injected beside them at a correct 4 spaces |
| `mixed_cluster.bsv` | `method I2C_out i2c0_out;`, the `GPTimer0..3` decoder arms, and two `import AXI4_Lite_*` lines were tab-indented |
| `mixed_cluster.bsv` | the whole `rl_connect_plic_connections` rule body was tab-indented |
| `Soc.bsv` | `interface Ifc_gptimer_io gptimer0_io;` was tab-indented |

`indent_tools.py audit` reports **0 violations** across all 9 patched files.

---

## 24. The cluster address windows (the bug that hid UART3 and I2C2/I2C3)

### Two decoders, not one

An address on the slow fabric is decoded **twice**. `fn_slave_map` in `Soc.bsv`
picks a *cluster* from the outer window macros:

```bsv
if      (addr >= `PWMClusterBase   && addr <= `PWMClusterEnd)   PWMCluster
else if (addr >= `UARTClusterBase  && addr <= `UARTClusterEnd)  UARTCluster
else if (addr >= `SPIClusterBase   && addr <= `SPIClusterEnd)   SPICluster
else if (addr >= `MixedClusterBase && addr <= `MixedClusterEnd) MixedCluster
…
else                                                            Err_slave
```

and only then does the cluster's own decoder pick a slave from `<IP>Base`/`<IP>End`.

An IP that adds a slave therefore has to grow **both**. Adding `UART3Base` alone
is not enough: if the address does not also fall inside `UARTClusterBase ..
UARTClusterEnd`, the *outer* decoder never routes it to the UART cluster and the
inner decoder is never consulted.

### What was broken

`sspi_v1` got this right — it widened `SPIClusterEnd` from `0002_01FF` to
`0002_03FF` when it added SPI2/SPI3. `uart_v1` and `i2c_v1` did not:

| IP | Slave added | Old cluster window | Result |
|---|---|---|---|
| `uart_v1` | UART3 @ `0001_1600..1640` | `UARTClusterEnd = 0001_1540` | **outside** → error slave |
| `i2c_v1` | I2C2/I2C3 @ `0004_1600..17FF` | `MixedClusterEnd = 0004_15FF` | **outside** → error slave |

Both peripherals synthesised, wired up, took a cluster slot and a PLIC line — and
were simply unreachable from software. Every access decoded to the top-level error
slave. This is invisible in simulation unless a test actually pokes the new
address, which is exactly why it survived until FPGA bring-up.

`gptimer4` (`0004_0800`), `watchdog0` (`0004_0900`) and `rtc0`/`rtc1`
(`0004_0600`/`0004_0700`) all sit *below* the old `0004_15FF` end, so they were
unaffected and need no window bump. **`i2c_v1` is the only mixed-cluster IP whose
addresses land past the pinmux**, so it owns that bump; `uart_v1` owns the UART one.

### The fix

Each offending IP def now carries a one-time `replace` patch on its cluster's end
macro, in the same shape `sspi_v1` already used:

```yaml
anchors:
  soc_defines_cluster_end:
    file: "{board_dir}/Soc.defines"
    pattern: "^`define\\s+MixedClusterEnd\\s+'h0004_15FF"
    position: "replace"

patches:
  - anchor_ref: "soc_defines_cluster_end"
    code: "`define MixedClusterEnd   'h0004_17FF"
    skip_if_matches: "`define\\s+MixedClusterEnd\\s+'h0004_17FF"
    apply_once: true
```

`skip_if_matches` keys on the *post* value, so a re-apply is a no-op and the
patch stays idempotent. The same pattern in `uart_v1.yaml` **and**
`uart_v1.yaml` takes `UARTClusterEnd` to `0001_1640` (both routing modes
put UART3 at the same address).

Resulting windows, all slaves now inside their cluster:

| Cluster | Window | Highest slave |
|---|---|---|
| UART | `0001_1300 .. 0001_1640` | `UART3End = 0001_1640` |
| SPI | `0002_0000 .. 0002_03FF` | `SPI3End = 0002_03FF` |
| PWM | `0003_0000 .. 0003_05FF` | `PWM0End = 0003_00FF` |
| Mixed | `0004_0000 .. 0004_17FF` | `I2C3End = 0004_17FF` |

`MixedClusterEnd` stops well short of `EthBase = 0004_4000`, so nothing collides.

### Rule for new IP authors

> If your slave's address is higher than every existing slave in its cluster,
> you **must** widen that cluster's `…ClusterEnd` too. Check it explicitly — the
> failure mode is silent, and it costs a full synthesis run to discover on-board.

---

## 25. Software bring-up (GCSDK): `platform.h`, drivers, and testing on the board

The RTL is only half the job. A peripheral that is synthesised, addressable and
wired to the PLIC is still untestable until software knows its base address and
its interrupt id. This section is the software mirror of everything the automation
added.

### The SDK target is `ganga`

`GCSDK/bsp/third_party/` has no `nexys_video` entry. The right existing target is
**`ganga`**: `rv64imac` / `lp64` / `WIDTH=8`, which matches this SoC's
`makefile.inc` (`xlen=64`, `buswidth=64`, `paddr=32`). Everything below lives in
`GCSDK/bsp/third_party/ganga/platform.h` and is built with `TARGET=ganga`.

`ganga/platform.h` was badly stale — it described a 16-GPIO, 2-UART, 1-I2C,
2-GPT SoC — so it was regenerated from `hw/boards/nexys_video/Soc.defines` rather
than patched.

> **`platform.h` is a hand-maintained mirror of `Soc.defines`.** The automation
> does **not** generate it. If you re-run the flow with different addresses in
> `soc_build_config.yaml`, re-derive this header or software will talk to the
> wrong addresses and the failure will look like a hardware bug.

### Address map

| Peripheral | Base | Count | Notes |
|---|---|---|---|
| UART0-3 | `0001_1300` +`0x100` | 4 | evenly strided |
| SPI0-3 (SSPI) | `0002_0000` +`0x100` | 4 | evenly strided |
| PWM0-9 | `0003_0000` +`0x10` | 10 | all inside the one `PWM0Base` window |
| I2C0-3 | `0004_0000`, `0004_1400`, `0004_1600`, `0004_1700` | 4 | **not strided** |
| GPIO0-47 | `0004_0100` | 48 | one instance, 48 lines |
| GPT0-4 | `0004_0200`+`0x100` ×4, then `0004_0800` | 5 | **not strided** |
| RTC0/RTC1 | `0004_0600` / `0004_0700` | 2 | strided by `0x100` |
| WDT0 | `0004_0900` | 1 | |
| XADC | `0004_1000` | 1 | |
| pinmux | `0004_1500` | 1 | |
| PLIC | `0C00_0000` | 1 | |
| CLINT | `0200_0000` | 1 | |

Core clock is **40 MHz** (`sys_clk_i` 100 MHz → MMCM ×10, CLKOUT0 ÷25), so
`CLOCK_FREQUENCY 40000000`. Main memory is `8000_0000..9FFF_FFFF` = 512 MB.

### The two stride breaks

The SDK drivers historically compute instance addresses as `base + i * offset`:

```c
i2c_instance[i] = (i2c_struct*) (I2C0_BASE + (i * I2C_OFFSET));   /* 0x1400 apart */
gpt_instance[i] = GPT_BASE_ADDRESS + i*GPT_OFFSET;                /* 0x100 apart  */
```

Neither survives the new instances:

* **I2C** — the stride says I2C2 is at `0004_2800`. It is actually at `0004_1600`
  (the automation packed it into the first free window above the pinmux).
* **GPT** — the stride says GPT4 is at `0004_0600`. That address is **RTC0**. GPT4
  is at `0004_0800`.

So `platform.h` publishes an explicit per-instance table and the two drivers use
it when present:

```c
#define I2C_BASE_LIST { I2C0_BASE, I2C1_BASE, I2C2_BASE, I2C3_BASE }
#define GPT_BASE_LIST { GPT0_BASE, GPT1_BASE, GPT2_BASE, GPT3_BASE, GPT4_BASE }
```

```c
void i2c_init()
{
#ifdef I2C_BASE_LIST
	static const unsigned long i2c_base_table[MAX_I2C_COUNT] = I2C_BASE_LIST;
	for(int i=0; i< MAX_I2C_COUNT; i++)
		i2c_instance[i] = (i2c_struct*) i2c_base_table[i];
#else
	…original base + i*offset…
#endif
}
```

The `#ifdef` matters: `i2c_driver.c` and `gpt.c` are **shared by every board**.
A target that defines no base list keeps its old behaviour byte-for-byte. Verified:
`TARGET=yamuna` still builds and still emits the strided form.

### GPIO is 48 lines, so its registers are 64-bit

`mkgpio` is instantiated with `ionum = 48`. The `gpio_struct` register fields were
`uint32_t`, which cannot address GPIO32-47 at all. Under `GPIO_COUNT > 32`,
`gpiov2.h` now selects a 64-bit variant:

```c
#if defined(GPIO_COUNT) && (GPIO_COUNT > 32)
	uint64_t  direction;
	uint64_t  data;
	…
#else
	uint32_t  direction;
	uint32_t  reserved0;
	…
#endif
```

The two layouts are **byte-for-byte identical** — each `uint64_t` covers the old
`uint32_t` plus the reserved word that followed it — so `qualification` and
`intr_config` keep their offsets and no other board is disturbed. (Confirmed in the
disassembly: `gpiov2_toggle` still hits offset `32`, now with a 64-bit `ld`.)

The pin masks are `1ULL << n`, not `1 << n`. With 64-bit registers a signed
`(1 << 31)` would sign-extend on the OR and set every bit above 31.

> **GPIO16-47 cannot interrupt.** `mixed_cluster.bsv` truncates the gpio-to-PLIC
> vector to 16 bits (`Bit#(16) lv_gpio_intr = truncate(pack(tmp))`), so only
> GPIO0-15 reach the PLIC. The rest are perfectly good I/O. `GPIO_INTR_COUNT`
> records this.

### The PLIC table was wrong — and had been for a long time

`sb_frm_sources` in `devices/riscv-plic/plic.bsv` maps vector bit `i` to source id
**`i+1`** (source 0 is reserved). The vector is `plic_inputs` in
`mixed_cluster.bsv`, whose bottom slice is the external-interrupt bus concatenated
in `Soc.bsv`. Read out, LSB → MSB, that gives:

| Bits | Source | PLIC ids |
|---|---|---|
| 0-9 | PWM0..PWM9 | 1-10 |
| 10-13 | UART0..UART3 | 11-14 |
| 14-15 | `ext_interrupts` | 15-16 — **tied to `2'b0`** in `fpga_top.v`, reserved |
| 16-19 | SPI0..SPI3 | 17-20 |
| 20-35 | GPIO0..GPIO15 | 21-36 |
| 36-39 | I2C0..I2C3 | 37-40 |
| 40-44 | GPT0..GPT4 | 41-45 |
| 45 | RTC0 | 46 |
| 46 | RTC1 | 47 |
| 47 | WDT0 | 48 |

`PLIC_MAX_INTERRUPT_SRC` is 49.

The old `ganga/platform.h` claimed GPIO was 7-22, GPT 23-26, I2C 27-28, UART 29-31.
That ordering **never matched this SoC** — it belongs to a different C-class
variant. Any interrupt example run on this board was arming the wrong source. The
table is now derived from the hardware.

Two things follow:

1. The `plic_index:` fields in `soc_build_config.yaml` (rtc0=35, i2c2=38, …) are
   **stale metadata**. They are used only for the engine's duplicate-reservation
   check, not to generate the vector — the vector's order comes from the anchors in
   `mixed_cluster.bsv`. They do not match the real ids and should not be trusted
   as documentation. Same for `ext_int_bit` in `sspi_v1` (it says 14/15; the real
   bus bits are 18/19 now that PWM is 10 wide and UART is 4 wide).
2. Prefer the **helper macros** over bare numbers, because they survive a
   renumbering when the next peripheral is added:

```c
configure_interrupt(PLIC_RTC(0));        /* not PLIC_INTERRUPT_46 */
isr_table[PLIC_RTC(0)] = handle_rtc_alarm;
```

`PLIC_PWM(i)`, `PLIC_UART(i)`, `PLIC_SSPI(i)`, `PLIC_GPIO(i)`, `PLIC_I2C(i)`,
`PLIC_GPT(i)`, `PLIC_RTC(i)`, `PLIC_WDT(i)` are all defined.

### New drivers: RTC and watchdog

Neither IP had a driver in the SDK. Both now do.

**`bsp/drivers/rtc/rtc.c` + `bsp/include/rtc.h`** — for `ip_bsv/rtc_v1` (note: *not*
`devices/rtc`, which is a different, unused RTC with a completely different register
map. `mixed_cluster.bsv` instantiates `rtc_v1`). Registers are 32-bit on an 8-byte
pitch: `control`, `time_read`, `time_write`, `epoch_read`, `epoch_write`, `alarm`,
`alarm_read`.

The control register's `cnt_rst` / `load_en` / `alarm_clr` / `prescl_upd` bits are
**mailbox request bits**: software sets one, it crosses into the RTC's oscillator
clock domain, and the hardware clears it on ack (`rl_handshake_clearing`). So the
driver sets the bit and polls it back to zero — that is the completion signal:

```c
static int rtc_request(rtc_struct *instance, uint32_t request_bit)
{
	instance->control |= request_bit;
	for(uint32_t i = 0; i < RTC_HANDSHAKE_TIMEOUT; i++)
		if((instance->control & request_bit) == 0)
			return 0;
	return -1;      /* oscillator not running? */
}
```

The alarm irq is **level-driven** — `rtc_clear_interrupt()` in the isr is mandatory
or the handler re-enters forever.

**`bsp/drivers/watchdog/watchdog.c` + `bsp/include/watchdog.h`** — for
`devices/watchdog`. Registers are 64-bit on an 8-byte pitch: `cycles` (0x00,
writing it *also feeds*), `control` (0x08), `reset_cycles` (0x10), `active` (0x18,
write to feed). Control bit 0 = enable, bit 1 = mode (0 = interrupt, 1 = reset),
bit 2 = soft reset.

> **The watchdog cannot reboot this SoC.** `reset_out` is routed to an observation
> pin only; it is not fed back into the reset tree (see §17). `WDT_MODE_RESET` will
> toggle a pin and nothing else. Use `WDT_MODE_INTERRUPT` for anything software
> must react to. The bite irq is level-driven from `counter == 0`, so the isr must
> `wdt_feed()` — disarming alone does not drop the line.

### Makefile wiring

`GCSDK/software/examples/Makefile`, `TARGET=ganga` branch:

* `compile:` builds `$(bspdri)/rtc/rtc.c → gen_lib/rtc.o` and
  `$(bspdri)/watchdog/watchdog.c → gen_lib/watchdog.o`
* `build:` adds `./gen_lib/rtc.o ./gen_lib/watchdog.o` to the `ar rcs` line for
  `libshakti$(XLEN).a`
* `do:` recurses into `rtc_applns` and `wd_applns` on `PROGRAM=all`
* the `PROGRAM` dispatch cascade gains `rtc_time`, `rtc_alarm`, `wd_feed`,
  `wd_interrupt`

`GCSDK/Makefile` gains `RTC_APPS` / `WD_APPS` (so `make list_applns` shows them)
and cleans both new directories.

### Building and loading

```bash
cd GCSDK
make software PROGRAM=rtc_alarm TARGET=ganga     # -> rtc_applns/rtc_alarm/output/rtc_alarm.shakti
make list_applns                                  # every app, including the new ones
```

Load over JTAG without touching flash — this is the fast bring-up loop:

```bash
sudo openocd -f bsp/third_party/ganga/ftdi.cfg \
  -c "reset init" \
  -c "load_image <path>/output/<prog>.shakti; resume 0x80000000; shutdown"
```

`make upload PROGRAM=… TARGET=ganga` writes to flash instead. Use it only when you
want the program to survive a power cycle — flashing is far slower than
`load_image` and you will iterate many times.

### An efficient way to test each peripheral on the Nexys Video

**Build the bitstream once.** All of it is in one SoC image; nothing below needs a
re-synthesis. Then iterate in software over JTAG. Work down this list in order —
each step depends only on the ones above it, so a failure localises immediately.

```bash
cd hw
make restore_autointeg_patches     # optional: prove the baseline is clean
make run_autointeg_complete        # apply all peripherals
make sync_bsv_patches
make generate_verilog
make BOARD=nexys_video quick_build # ~1 synthesis run, then program the board
```

| # | Peripheral | Test | External hardware | Proves |
|---|---|---|---|---|
| 0 | **UART0** | any app's `printf` | USB-UART (built in) | The console. Nothing else is debuggable until this prints. |
| 1 | **PLIC** | `PROGRAM=test` (`plic_applns`) | none | The interrupt path and the renumbered table. |
| 2 | **WDT0** | `PROGRAM=wd_feed` | none | WDT0 decodes at `0004_0900` and reads back. Cheapest new-peripheral check — run it first. |
| 3 | **WDT0 irq** | `PROGRAM=wd_interrupt` | none | Discrete PLIC source 48. Feeds 5×, then starves it and catches the bite. |
| 4 | **RTC0** | `PROGRAM=rtc_time` | none | RTC0 decodes at `0004_0600`, counter advances in its own clock domain, load handshake works. |
| 5 | **RTC0 irq** | `PROGRAM=rtc_alarm` | none | Discrete PLIC source 46. |
| 6 | **GPT4** | `gpt_applns` app, `gpt_get_instance(4)`, arm `PLIC_GPT(4)` | none | The `GPT_BASE_LIST` table (GPT4 at `0004_0800`, off-stride) and PLIC id 45. |
| 7 | **PWM6-9** | `pwmv2_applns` app on channels 6-9 | scope / LED on the FMC pin | The widened 10-channel `mkpwm` and the direct (non-pinmux) pin path. |
| 8 | **UART3** | `uart_testcases` loopback on `uart_instance[3]` | jumper TX↔RX | **The `UARTClusterEnd` fix.** Before it, every UART3 access hit the error slave. |
| 9 | **SPI2/SPI3** | `sspi_testcases/sspi_full_duplex` on instance 2 or 3 | jumper MOSI↔MISO | The widened SPI cluster + PLIC ids 19/20. |
| 10 | **I2C2/I2C3** | `i2c_applns/lm75` etc. on `i2c_instance[2]` | an I²C device on the FMC pins (+ pull-ups) | **The `MixedClusterEnd` fix** and the `I2C_BASE_LIST` table. |
| 11 | **GPIO32-47** | `gpiov2_applns` set/read a pair | jumper between two FMC pins | The 64-bit register width. Remember: these pins **cannot** interrupt. |

Notes that save time:

* **Steps 0-6 need no external hardware at all.** Do them first. They cover both
  brand-new IPs (RTC, WDT), the PLIC renumbering, and both off-stride base tables —
  i.e. most of what could be wrong.
* **Steps 7-11 are all on the FMC-LA bank**, so they need an FMC breakout board.
  The bank runs at **1.2 V (VADJ)** — do not wire 3.3 V logic to it directly.
  `boards/nexys_video/pin_map.yaml` has the exact pin for every signal.
* A peripheral that reads back **all-zeros or all-ones and never changes** is
  almost always an address decoding to the error slave — check the cluster window
  (§24) before suspecting the IP.
* An interrupt that never fires, on a peripheral whose registers read back fine,
  is almost always a **PLIC id mismatch** — check the id against the table above,
  not against `plic_index` in `soc_build_config.yaml`.

### Verification performed

* `make restore_autointeg_patches` → `run_autointeg_complete` → `run_autointeg_complete`
  again: the second apply changes **no file** (byte-identical `diff -r`), so the
  cluster-window patches are idempotent, and restore fully reverts them.
* `post_validate_automation`: 0 failures.
* `indent_tools.py audit`: **0 violations** across all patched BSV files.
* `TARGET=ganga`: BSP library and all four new apps compile and link. The
  disassembly confirms `rtc_init` materialises `0x40600`, `wdt_init` materialises
  `0x40900`, `rtc_alarm` calls `configure_interrupt(46)`, and the `.rodata` base
  tables hold exactly `{40000, 41400, 41600, 41700}` and
  `{40200, 40300, 40400, 40500, 40800}`.
* `TARGET=yamuna` still builds — the shared `i2c_driver.c` / `gpt.c` / `gpiov2.h`
  changes are backward compatible. (`sos` and `parashu` fail to build, but they
  did so **before** these changes too — pre-existing, unrelated.)

---

## 26. I2C address-define ordering (family grouping in `Soc.defines`)

### The ask

Group the I2C `Base`/`End` address macros so `I2C0`, `I2C1`, `I2C2`, `I2C3`
appear **contiguously** in `Soc.defines`, the same way their `_slave_num` macros
already do (§22). Before this, `I2C1Base`/`End` sat far below `I2C0` — after the
GPIO, GPTimer0-3 and XADC blocks — and the automation-injected `I2C2`/`I2C3` were
appended beneath `I2C1`, so the family was split across the file.

### This is declaration order, not address space

The addresses are **not** touched. `I2C0` stays at `0004_0000`; `I2C1`/`I2C2`/`I2C3`
stay at `0004_1400`/`0004_1600`/`0004_1700`. They *cannot* be made numerically
contiguous — the words right after `I2C0` (`0004_0100`, `0004_0200`…) are GPIO and
GPTimer0-3, and relocating I2C would force a re-synthesis and break the
`I2C_BASE_LIST` table in `platform.h` (§25). Only the **order of the `#define`
lines** changed. `#define` order is irrelevant to the Bluespec preprocessor —
these macros are consumed only in the decoder files compiled afterwards — so the
reordering is behaviour-neutral. Nothing in the SDK changes.

### Why it needed a baseline edit, not just a YAML tweak

`I2C1` is a **baseline** macro (it predates the automation), and the I2C0↔I2C1
split lived in the pre-automation board source — the backup carried it too. The
automation only *appends* `I2C2`/`I2C3` after `I2C1End`. So the fix was to move
`I2C1Base`/`End` up next to `I2C0End` **in the baseline**, then let the existing
`i2c_v1` injection (anchored on `I2C1End`) extend the now-adjacent group:

```
make restore_autointeg_patches     # board files -> pristine baseline
make reset_autointeg_baseline      # forget the old baseline snapshot
# move I2C1Base/End to directly after I2C0End in boards/nexys_video/Soc.defines
make run_autointeg_complete        # re-captures the grouped baseline, then
                                   # injects I2C2/I2C3 after I2C1End
```

Because `i2c_v1.yaml`'s anchor is `^`define I2C1End\s+'h0004_14FF` and `I2C1End`
now sits right after `I2C0End`, `I2C2`/`I2C3` land in the same place and the whole
family is contiguous. No IP-def change was required.

Result:

```
`define I2C0Base    'h0004_0000
`define I2C0End     'h0004_00FF
`define I2C1Base    'h0004_1400
`define I2C1End     'h0004_14FF
`define I2C2Base    'h0004_1600
`define I2C2End     'h0004_16FF
`define I2C3Base    'h0004_1700
`define I2C3End     'h0004_17FF
```

### Every other family was already grouped

Checked all of them — I2C was the only split family. `GPTimer0`-`GPTimer4`,
`RTC0`/`RTC1`, `SPI0`-`SPI3`, `UART0`-`UART3` and the singletons (`GPIO`, `XADC`,
`Pinmux`, `WatchDog0`) each already appear as one contiguous block, in both the
`_slave_num` section and the address section. Nothing else needed moving.

### Verification

* Restore now returns to the **grouped** baseline (`I2C0`,`I2C1` together, no
  `I2C2`/`I2C3`, cluster windows back to `1540`/`15FF`) — confirming the
  re-captured baseline is self-consistent.
* Re-apply is idempotent: a second `run_autointeg_complete` changes no file
  (`diff -r` clean).
* `indent_tools.py audit`: 0 violations. `post_validate_automation`: 0 failures.
* No duplicate macros introduced (`grep | sort | uniq -d` clean apart from the
  pre-existing `ifdef`-guarded `Num_Fast_Masters`).
* `platform.h` unchanged — addresses never moved, so `I2C_BASE_LIST` still reads
  `{0x40000, 0x41400, 0x41600, 0x41700}`.
