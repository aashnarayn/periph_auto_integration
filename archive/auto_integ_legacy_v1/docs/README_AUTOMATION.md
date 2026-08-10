# Fully Automated Peripheral Integration (PoC)
## Shakti GC2025 — YAML-Driven RTC Integration

## Goal
Demonstrate end-to-end peripheral integration via YAML configuration alone — zero manual code edits, zero Python modifications for new peripherals.

---

## Deliverables
```
gc2025/hw/
├── Makefile                     # Build system (hw/ root) + automation targets appended
├── bsvpath                      # Include paths (hw/ root)
├── soc_build_config.yaml        # Centralized Bill of Materials (BOM)
├── scripts/
│   └── peripheral_auto_integrator.py  # Agnostic automation engine
├── boards/
│   └── nexys_video/             # Board-specific sources (patched by automation)
│       ├── Soc.defines
│       ├── mixed_cluster.bsv
│       ├── Soc.bsv
│       ├── fpga_top.v
│       └── ...
├── ip_bsv/
│   └── rtc_v1/                  # Our peripheral to be integrated
│       ├── rtc_v1.yaml          # Distributed IP definition + patch instructions
│       ├── rtc_v1.bsv           # Peripheral source code
│       ├── rtc.defines          # Register map defines
│       └── rtc_validate.sh      # Anchor validation suite (executable, auto-detects board_dir)
└── README_AUTOMATION.md         # This file
```

---

## How Automation Works

```
1. Patches applied → boards/nexys_video/* (via rtc_v1.yaml paths)
       ↓
2. make run_setup_fpga → copies patched board files to hw/ root
       ↓
3. make generate_verilog → compiles patched BSV → Verilog
       ↓
4. make board_build → Vivado synthesis → bitstream with integrated RTC
```

### Key Design Decisions
| Component | Strategy | Why |
|-----------|----------|-----|
| **File paths in `rtc_v1.yaml`** | Hardcoded `boards/nexys_video/...` | Explicit, debuggable, zero Python changes for PoC |
| **`board_dir` in `soc_build_config.yaml`** | Single source for validation script | Auto-detection; no CLI flags needed |
| **`rtc_validate.sh`** | Reads `board_dir` from YAML config | Works across boards without argument changes |
| **Idempotency** | `skip_if_contains` on every patch | Safe to re-run; no duplicate injections |

---

## Changing Boards

To target a different board (e.g., `arty_a7_ganga`):

1. **Update `BOARD` in Makefile or CLI**:
   ```bash
   make BOARD=arty_a7_ganga quick_build_automated
   ```

2. **Update `board_dir` in `soc_build_config.yaml`**:
   ```yaml
   target_board: "arty_a7_ganga"
   board_dir: "boards/arty_a7_ganga"
   ```

3. **Update all `file:` paths in `rtc_v1.yaml`**:
   ```yaml
   # Change from:
   - file: "boards/nexys_video/Soc.defines"
   # To:
   - file: "boards/arty_a7_ganga/Soc.defines"
   ```

4. **Verify anchor patterns match the target board's file contents**:
   ```bash
   make pre_validate_automation
   ```

> **Pro Tip**: Keep a template `rtc_v1.yaml.template` with `{board_dir}` placeholders, then use `sed` to generate board-specific versions:
> ```bash
> sed "s|{board_dir}|boards/arty_a7_ganga|g" rtc_v1.yaml.template > rtc_v1.yaml
> ```

---

## Quick Start (Fresh Clone)

```bash
# 1. Clone & navigate
git clone https://github.com/shaktiproject/gc2025.git
cd gc2025/hw    

# 2. Create directories & place files
mkdir -p scripts ip_bsv/rtc_v1
# Copy files to exact paths:
# - soc_build_config.yaml → gc2025/hw/
# - rtc_v1.yaml, rtc_validate.sh → gc2025/hw/ip_bsv/rtc_v1/
# - peripheral_auto_integrator.py → gc2025/hw/scripts/
chmod +x scripts/peripheral_auto_integrator.py ip_bsv/rtc_v1/rtc_validate.sh

# 3. Verify prerequisites
python3 -c "import yaml; print('[INFO] PyYAML ready')"
which bsc || echo "[WARN] bsc not in PATH (BSV validation will skip)"

# 4. Pre-automation validation (auto-detects board_dir from soc_build_config.yaml)
make pre_validate_automation

# 5. Dry-run automation (SAFE — no files modified)
make run_peripheral_automation_dry

# 6. Apply automation
make run_peripheral_automation

# 7. Post-automation verification
make post_validate_automation

# 8. Build end-to-end
make quick_build_automated

# 9. If build fails, rollback & debug
make restore_peripheral_patches
# Original files restored from .automation_backup/
```

---

## Validation via Makefile Targets

The validation logic is now integrated into the Makefile for a unified workflow. All targets auto-detect `board_dir` from `soc_build_config.yaml`.

```bash
# Run from gc2025/hw/

# Pre-automation: verify all anchors exist in board directory
make pre_validate_automation

# Post-automation: verify patches applied correctly
make post_validate_automation

# Verbose mode for debugging grep patterns
make pre_validate_automation VERBOSE=1
```

### Exit Codes
| Code | Meaning |
|------|---------|
| `0` | All checks passed |
| `1` | One or more checks failed |
| `2` | Invalid arguments, missing config, or missing target files |

---

## Makefile Targets

| Target | Purpose |
|--------|---------|
| `run_peripheral_automation` | Apply patches from YAML config to board directory |
| `run_peripheral_automation_dry` | Preview patches without modifying files (verbose) |
| `restore_peripheral_patches` | Rollback to pre-automation state and clean backups |
| `pre_validate_automation` | Run pre-automation anchor validation suite |
| `post_validate_automation` | Run post-automation patch verification suite |
| `quick_build_automated` | Full flow: setup → automate → validate → build |

### Optional: Environment Toggle
```bash
# Enable automation via env var
AUTOMATE_PERIPHERALS=1 make quick_build
```

---

## Adding a New Peripheral (Zero Python Changes)

1. Create `ip_bsv/<name>/<name>.yaml` with patch definitions (follow `rtc_v1.yaml` structure)
2. Add entry to `automated_peripherals` in `soc_build_config.yaml`
3. Provide context variables for template resolution
4. Run `make quick_build_automated`

**Example: Adding an SPI v2 peripheral**
```yaml
# soc_build_config.yaml addition
- name: "spi_v2"
  def_path: "ip_bsv/spi_v2/spi_v2.yaml"
  context:
    # BSV integration
    slave_id_macro: "SPI2_slave_num"
    slave_id_val: "11"
    base_macro: "SPI2Base"
    base_addr: "0002_0200"
    end_macro: "SPI2End"
    end_addr: "0002_02FF"
    plic_source: "spi2.spi2_sb_interrupt"
    plic_position: "lsb"
    # Verilog boundary (optional)
    verilog_port_name: "spi2_cs_n"
    verilog_wire_name: "wire_spi2_cs"
    verilog_buffer_type: "IOBUF"
    verilog_buffer_tristate: "spi2_outen"
    rtc_io_port_name: "spi2_io_cs_n"
```

---

## Safety Features

- **Transactional patching**: All files backed up to `.automation_backup/` before modification  
- **Auto-rollback**: On any error, original files are restored automatically  
- **Idempotent execution**: `skip_if_contains` prevents duplicate patches on re-run  
- **Pre/post validation**: Makefile targets confirm state before and after automation  
- **Dry-run mode**: `--dry-run` previews changes without modifying files  
- **Width-agnostic PLIC**: Handles any starting interrupt count (35, 54, etc.) dynamically  
- **Board-agnostic config**: `board_dir` in YAML allows easy porting to other boards  

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| "Anchor not found" | Upstream code changed or wrong `board_dir` | Update `anchor_pattern` in YAML; verify `board_dir` in `soc_build_config.yaml` |
| "BSV syntax error" | Template variable missing or typo in context | Check `context` keys in `soc_build_config.yaml` match `{placeholders}` in YAML |
| "PLIC width != signal count" | Regex pattern mismatch in `width_anchor_pattern` | Verify patterns in `rtc_v1.yaml`; run `make pre_validate_automation VERBOSE=1` |
| Makefile "missing separator" | Spaces instead of TABs in recipe lines | Replace indentation with literal TAB characters |
| `bsc` not found | Bluespec compiler not in `$PATH` | Install or ignore warning (validation skips gracefully) |
| Validation fails with "file not found" | Running from wrong directory or `board_dir` mismatch | Ensure you're in `gc2025/hw/`; check `board_dir` in `soc_build_config.yaml` |

---

## Scaling Beyond Proof of Concept (PoC)

1. **Schema validation**: Add JSON Schema for peripheral YAML files to catch typos early
2. **`soc_config` integration**: Merge automation into Shakti's existing Python config tool
3. **Software co-generation**: Extend to emit Device Tree snippets + C headers alongside BSV patches
4. **CI/CD checks**: Run `make pre_validate_automation` in PR pipelines to catch anchor drift
5. **Multi-board support**: Use `{board_dir}` templating + `sed` to generate board-specific YAML

---

## PoC Success Criteria

- [x] Add peripheral via YAML only — no manual code edits in BSV/Verilog
- [x] Zero Python modifications required to add new peripherals
- [x] PLIC vector automation handles width + signal injection dynamically
- [x] Build succeeds with `make quick_build_automated`
- [x] Re-running automation is safe (idempotent or restorable via `--restore`)
- [x] Validation confirms pre/post state via Makefile targets
- [x] Board switching requires only config edits (`board_dir` + file paths)

---

This framework demonstrates Data-Driven Hardware Design: YAML as Single Source of Truth for hardware wiring, address maps, interrupt routing, and FPGA pin export — with automated validation at every step.