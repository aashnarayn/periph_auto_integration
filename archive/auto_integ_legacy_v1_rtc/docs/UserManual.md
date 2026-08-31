# Legacy V1 — RTC Framework User Manual

This is the **mini** reference for `auto_integ_legacy_v1_rtc`, the original V1
prototype: one peripheral (RTC, dedicated pins on PMOD JA), one static config,
one procedural Python engine. It is not V2's manual — see
`periph_auto_integration/docs/UserManual.md` for the current, multi-peripheral,
dynamically-composed engine. Read this file for how *this* frozen framework
actually works, its patch schema, its idempotency model (and the bug in it that
was fixed here), and how to verify a build.

`README.md` (this framework's root) is the setup-manager quick reference —
clone/inject/status/remove/delete. `docs/README_AUTOMATION.md` is the original
PoC pitch doc. This file is the technical "how it actually works" reference,
the RTC-scoped counterpart to V2's `UserManual.md`.

---

## 1. The big picture

```
soc_build_config.yaml          <- ONE static peripheral: rtc_v1
        │
        ├── ip_bsv/rtc_v1/rtc_v1.yaml   <- 21 patches: anchor + code + skip guard
        │
        ▼
scripts/peripheral_auto_integrator.py   <- ~670-line procedural engine
        │  (writes files directly, backs up to .automation_backup/,
        │   rolls back the whole run on any single patch failure)
        ▼
boards/nexys_video/*   <- PATCHED here: Soc.defines, Soc.bsv, mixed_cluster.bsv,
                           fpga_top.v, constraints.xdc
        │
        │  make sync_bsv_patches / sync_automated_files
        ▼
hw/ root copies   <- what bsc actually compiles
        │
        ▼
make generate_verilog  ->  build/  ->  make board_build  ->  bitstream
```

Unlike V2, there is **no VFS and no atomic multi-file commit** — patches write
straight to disk, phase by phase (`--bsv`, `--verilog`, `--xdc` are three
separate Python invocations, each its own transaction). A failure *within* one
phase rolls that phase's files back cleanly; a failure that only surfaces in a
*later* phase does **not** undo an earlier phase's already-committed files. See
§7 for exactly how that bit this framework in practice.

RTC integrates through the **pinmux**, on **dedicated pins** — it is wired via
`mkpinmuxtop`/`mkrtc`, exposed to the top level over PMOD JA (`ja[10]`, fallback
`ja[9]`), and it is the only one of the three legacy frameworks that ships its
**own new BSV source** (`rtc_v1.bsv`, `rtc.defines`) rather than only reusing
existing Shakti IP.

---

## 2. Repository layout

```
auto_integ_legacy_v1_rtc/
├── Makefile                  # setup manager: setup_full/base, status, remove, delete, update
├── README.md                 # setup-manager quick reference
├── configs/
│   └── soc_build_config.yaml # the ONE static BOM (no CONFIG_SELECT — nothing to choose)
├── docs/
│   ├── README_AUTOMATION.md  # original PoC pitch/quick-start (deployed into the clone)
│   └── UserManual.md         # this file (NOT deployed into the clone — reference only)
└── files/                    # payload injected into a clone's hw/
    ├── Makefile               # hw/ root Makefile + automation targets appended
    ├── master_constraints.xdc
    ├── ip_bsv/
    │   └── rtc_v1/
    │       ├── rtc_v1.yaml       # 21 patches + anchor reference comment block
    │       ├── rtc_v1.bsv        # the RTC peripheral RTL (new source, not reused)
    │       ├── rtc.defines       # register-map defines
    │       └── rtc_validate.sh   # pre/post anchor validation, ~750 lines
    └── scripts/
        ├── peripheral_auto_integrator.py  # the engine (see §3-§4)
        └── track_pin_map.py               # pin_map.yaml generator/sync
```

---

## 3. How a patch is defined

Every entry in `rtc_v1.yaml`'s `patches:` list is one of three shapes:

**Plain regex patch** (the common case):
```yaml
- file: "{board_dir}/mixed_cluster.bsv"
  anchor_pattern: "slave_num = `Pinmux_slave_num;"
  position: "after"                 # after | before | replace
  code: "        else if(addr>= `{base_macro} && addr<= `{end_macro} )\n          slave_num = `{slave_id_macro};"
  skip_if_contains: "`{slave_id_macro}"
```
`anchor_pattern` is a Python regex searched in the target file (`re.MULTILINE`).
`code`/`skip_if_contains` are both plain Python `str.format()` templates,
substituted against the peripheral's `context` block in `soc_build_config.yaml`
merged with `board_dir` — **both fields**, not just `code`. That mattered; see
§7.

**`type: "plic_vector"`** — the one special handler (patch #12 in this def).
Reads the current PLIC input-vector width from `mixed_cluster.bsv`
(`width_anchor_pattern`), inserts `signal` (`{plic_source}`) into the
`plic_inputs = {...}` concatenation at `position` (`msb`/`lsb`/an integer
index), and republishes the new width into `context['new_plic_width']` so a
**later** plain patch can reference `{new_plic_width}` in its own `code` (patch
#13, the `mkplic` module's width parameter). This cross-patch context
dependency is exactly what broke idempotency — see §7.

**`type: "xdc_pin_assign"`** — pin placement with conflict resolution, handled
entirely by `XDCEditor` (not the generic patch path). Tries `target_board_pin`
first; if that pin is already claimed by something else, it displaces the
existing assignment to an FMC-LA fallback and re-homes the peripheral to
`fallback_pin`. Its own `skip_if_contains` field (present on patch #21 for
documentation) is **unused** — `XDCEditor` has its own presence check.

---

## 4. The three patch phases

`rtc_v1.yaml`'s 21 patches split by which file they touch:

| Phase | Patches | Files |
|---|---|---|
| **BSV** (`--bsv`) | #1–16 | `bsvpath`, `Soc.defines`, `Soc.bsv`, `mixed_cluster.bsv` |
| **Verilog** (`--verilog`) | #17–20 | `fpga_top.v` (run *after* `generate_verilog`, since it patches the top-level wrapper, not BSV) |
| **XDC** (`--xdc`) | #21 | `constraints.xdc` (+ the `additional_properties` clock-route line, injected separately — see §9) |

Each phase is its own `python3 scripts/peripheral_auto_integrator.py --config
soc_build_config.yaml --{bsv,verilog,xdc}` call, wrapped by a Makefile target:

| Target | Does |
|---|---|
| `update_bsvpath` | Applies patch #1 only (adds `ip_bsv/rtc_v1/` to `bsvpath`) |
| `run_peripheral_bsv` / `_dry` | Phase 1 |
| `run_peripheral_verilog` / `_dry` | Phase 2 |
| `run_peripheral_xdc` / `_dry` | Phase 3 |
| `run_peripheral_automation_dry` | All three dry-run in sequence, no files touched |
| `run_peripheral_automation` | `update_bsvpath` → BSV → Verilog → XDC → `track_pin_map`, in one call |
| `sync_bsv_patches` | Copies patched `boards/nexys_video/{Soc.bsv,Soc.defines,mixed_cluster.bsv}` (+ `bsvpath`) to `hw/` root |
| `sync_automated_files` | Same, plus `constraints.xdc` and `fpga_top.v` |
| `restore_peripheral_patches` | Restores from `.automation_backup/`, resyncs pin map, deletes the backup |
| `pre_validate_automation` / `post_validate_automation` | Runs `rtc_validate.sh --pre` / `--post` |
| `quick_build_automated` | The real end-to-end order — see below |
| `build_automated` | `= quick_build_automated` (the entry point named in `README.md`) |

**`quick_build_automated`'s actual order** (this is the order that matters —
Verilog patches target `fpga_top.v`, which is a **board source file**, not a
BSV compile artifact, so it does not need to wait for `generate_verilog`; but
`sync_bsv_patches` does need to run before `generate_verilog` so `bsc` sees the
patched BSV):

```
pre_validate_automation → update_bsvpath → run_setup_build → track_pin_map
  → run_peripheral_automation_dry (safety dry-run, skip with SKIP_DRY_RUN=1)
  → run_peripheral_bsv → sync_bsv_patches → generate_verilog
  → run_peripheral_verilog → run_peripheral_xdc → sync_automated_files
  → post_validate_automation → generate_boot_files → ip_build → board_build
```

`run_setup_build` runs `pip install -r requirements.txt`, `repomanager` (fetches
~10 dependency repos from GitLab), and `soc_config` (generates `makefile.inc`,
which is where the board's `BSCCMD` — including any `bsc` flags gc2025 assumes
— gets written). It also does `cp -r boards/$(BOARD)/. .`, which is why it must
run **before** patching: patching happens against `boards/nexys_video/`, and
this step is what first populates the `hw/` root from that board directory.

---

## 5. Idempotency: `skip_if_contains`, and the two bugs fixed here (2026-08-31)

`skip_if_contains` is a plain substring test — if the string is already present
in the target file, the patch is skipped. On the pristine-clone path (the
*only* path this framework had ever been exercised on before this session) that
was invisible. Re-running `run_peripheral_automation` (or `build_automated`) on
an **already-integrated** clone exposed two real defects, both now fixed in
`scripts/peripheral_auto_integrator.py`:

### 5a. `KeyError: 'new_plic_width'` on re-run (crash)

`handle_plic_vector_patch` (patch #12) only populated
`context['new_plic_width']` on its *apply* path. On a re-run its own guard
correctly detects `{plic_source}` already present and returns early — **before**
setting the key. Patch #13 (`mkplic` wrapper width) then needs that key just to
evaluate *its own* `skip_if_contains`, and crashed instead. Because this
crashed mid-transaction, the automatic rollback reverted only the files touched
so far in that phase (`Soc.defines`, `bsvpath`) while leaving others
(`Soc.bsv`, `mixed_cluster.bsv`) at their already-integrated state — an
internally inconsistent, unbuildable tree. (Still fully recoverable: the
*original* `.automation_backup/` from the first successful run survives a
crashed re-run, so `make restore_peripheral_patches` still restores byte-clean
to pristine.)

**Fix:** the skip branch now re-reads the current width from the file and sets
`context['new_plic_width']` to that (not `old_w + 1`, which would double-count)
before returning.

### 5b. `skip_if_contains` was never template-substituted for plain patches

The bigger one. The generic patch path formats `code` through the peripheral's
context (`patch['code'].format(**ctx)`) but was passing `skip_if_contains`
**raw**, curly braces and all, straight to the literal substring check. Any
patch whose skip string contains a `{placeholder}` could therefore never
recognize its own prior output and **reapplied on every re-run** — silently, no
error, no crash, just duplicate injection. Once the crash above was fixed in
isolation this became directly visible: a re-run duplicated the `RTCBase`/
`RTCEnd` memory-map lines in `Soc.defines` and reapplied five other patches
verbatim.

Seven of this def's patches carry a templated `skip_if_contains` and were all
silently affected: #2 (`{base_macro}`), #7 (`{slave_id_macro}`), #9
(`{slave_id_macro}`), #13 (`{new_plic_width}`), #17–19 (`{verilog_port_name}`
/ `{verilog_wire_name}` / `{rtc_io_port_name}`). Patch #21's templated field is
unused (`xdc_pin_assign` has its own idempotency, per §3), so it was never at
risk.

**Fix:** the same line that formats `code` now also formats `skip_if_contains`
before passing it to the file-patch helper — one `if skip: skip =
skip.format(**ctx)`, mirroring the pattern already used correctly by the
`plic_vector` handler and the XDC `additional_properties` injection.

**Both fixes are in the shared engine** (`peripheral_auto_integrator.py` is
byte-identical across all three legacy frameworks — see the root `HANDOFF.md`
§9.7), so they were applied to `_uart` and `_uart_pinmux` too for consistency,
even though neither of those defs has a `plic_vector` patch or a templated
`skip_if_contains` and neither was ever actually affected. Confirmed
unaffected: re-running `run_peripheral_automation` on both UART frameworks was
already exit-0/md5-identical before and after this fix.

**Verified fixed (2026-08-31):** fresh clone → `run_peripheral_bsv` →
`run_peripheral_automation` again → every one of the 21 patches printed
`Skipping`, zero printed `Patched`, all five touched files (`Soc.bsv`,
`Soc.defines`, `mixed_cluster.bsv`, `fpga_top.v`, `constraints.xdc`) came back
md5-identical to before the re-run, and `generate_verilog` compiled clean
afterward (`mkSoc.v`, `mkDebugSoc.v`, exit 0, no `bsc` errors).

### General lesson

**Never let `skip_if_contains` reach the file-content check unformatted if it
contains `{...}`.** Grep any def for `skip_if_contains:.*{` before trusting its
idempotency — that pattern is a wired trap in this engine's design (`code` and
`skip_if_contains` look like siblings on adjacent lines; only one of them was
actually substituted).

---

## 6. Recovery / restore

`make restore_peripheral_patches`:
1. `python3 scripts/peripheral_auto_integrator.py --restore` — restores every
   file recorded in `.automation_backup/` (created on first write of a real,
   non-dry run; **not** recreated on subsequent runs if it already exists).
2. Explicitly restores `bsvpath` from the backup if present (`bsvpath` lives at
   the `hw/` root, outside `boards/`, so it needs its own restore step).
3. `make track_pin_map` — resyncs `pin_map.yaml` to the now-restored
   `constraints.xdc`.
4. `rm -rf .automation_backup`.

Verified byte-clean against a pristine post-clone snapshot (`diff -rq`, exit 0)
on 2026-08-24 and again on 2026-08-31, both before and after the §5 fixes.

---

## 7. Verification recipes

```bash
# 1. Anchors exist in a fresh (or restored) clone — before touching anything
make pre_validate_automation          # expect 24/24 PASS

# 2. Preview every phase, no files touched
make run_peripheral_automation_dry

# 3. Apply, gold-standard compile check
make run_peripheral_automation
make sync_bsv_patches
make generate_verilog -j             # success: build/hw/verilog/{mkSoc,mkDebugSoc}.v

# 4. Confirm the patches actually landed where expected
make post_validate_automation         # expect 28/28 PASS

# 5. Idempotency — re-run must be a clean no-op
make run_peripheral_automation        # expect every line "Skipping", zero "Patched"

# 6. Full flow to bitstream (the real one-shot entry point)
make build_automated                  # = quick_build_automated

# 7. Restore to stock
make restore_peripheral_patches
```

**`rtc_validate.sh --pre --post`** greps for the literal anchor/output strings
listed in the comment block at the bottom of `rtc_v1.yaml` (§ANCHOR REFERENCE).
Like V2's validators, this can confirm *presence* but not *placement* — it
would not, by itself, have caught something like V2's cell12
out/outen-swap-class bug. The strongest check remains what §7's item 3 already
does: compile and inspect the generated Verilog.

**Full bitstream, verified 2026-08-31** (fresh clone, this machine, `bsc
2026.01`): synthesis 0 errors / 0 critical warnings, `write_bitstream`
succeeded, WNS +0.421 / TNS 0.000 / WHS +0.013 / THS 0.000 (timing met), `rtc_out_pmod`
placed at `AA18` (`ja[10]`). The one transient `CRITICAL WARNING` seen mid-build
(`set_property` on `sys_clk_IBUF`, `constraints.xdc:6`) is the pre-existing,
documented, expected quirk described in V2's `UserManual.md` §11 — present in a
completely stock tree, not introduced by this def.

---

## 8. Framework-specific gotchas

- **RTC ships its own new BSV** (`rtc_v1.bsv`). This is the only one of the
  three legacy frameworks that does — both UART variants reuse existing
  `devices/uart_v2/` IP and add zero new BSV source.
- **`update_bsvpath` applies "patch #1"**, whatever that happens to be — here
  it genuinely is the `bsvpath` entry (patch #1 in this def). Don't assume the
  name generalizes; the two UART frameworks' patch #1 is a `Soc.defines` edit,
  since UART3 needs no `bsvpath` entry at all.
- **PLIC width is derived, not hardcoded**, via the `plic_vector` handler — the
  only one of the three legacy frameworks to use it. §5 covers why that made it
  the one framework with a real idempotency bug: dynamic, cross-patch context
  state is inherently fragile in a system with per-phase Python subprocess
  boundaries.
- **`board_build`'s exit code lies.** Its second Vivado call has no `exit 1` on
  failure. Read `artybuild.log`; grep for `write_bitstream completed` /
  `Synthesis finished`, don't trust `$?`.
- **`bsc` needs a build that still has `-cross-info`** (removed in bsc 2026.07,
  present through 2026.01). See root `HANDOFF.md` and
  `ubuntu_setup/RESTORATION_CONTEXT.md` §8 if `generate_verilog` fails with
  `Unrecognized flag: -cross-info` — that is an environment/toolchain issue,
  not a defect in this framework.
- **`/tmp` is wiped at every boot** on machines set up via `ubuntu_setup`.
  Never leave scratch state there across a session boundary.
