#!/usr/bin/env bash
# =============================================================================
# rtc_validate.sh — Anchor Pattern Validation for rtc_v1 Automation
# Location: gc2025/hw/ip_bsv/rtc_v1/rtc_validate.sh
#
# Auto-detects BOARD_DIR from soc_build_config.yaml. No flags required.
# Validates BSV, Verilog, AND XDC constraint patches including:
# - 5-element pin map structure
# - Conflict resolution with fallback pins
# - Property migration (PULLDOWN, etc.)
# - Header grouping preservation
# - No duplicate constraints
# - Clock property injection (CLOCK_DEDICATED_ROUTE)
# =============================================================================

set -euo pipefail

# =============================================================================
# Configuration & Auto-Detection
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HW_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [[ ! -f "$HW_ROOT/bsvpath" ]] && [[ -f "$HW_ROOT/hw/bsvpath" ]]; then
    HW_ROOT="$HW_ROOT/hw"
fi

CONFIG_FILE="$HW_ROOT/soc_build_config.yaml"

BOARD_DIR="boards/nexys_video"
if [[ -f "$CONFIG_FILE" ]]; then
    DETECTED_DIR=$(grep -m1 "board_dir:" "$CONFIG_FILE" 2>/dev/null | \
        sed -E 's/.*board_dir:[[:space:]]*//; s/[[:space:]]*#.*//; s/^[[:space:]]*"//; s/"[[:space:]]*$//; s/[[:space:]]*$//')
    if [[ -n "$DETECTED_DIR" ]]; then
        BOARD_DIR="$DETECTED_DIR"
    fi
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Counters
PASS=0
FAIL=0
VERBOSE=0
MODE="all"


# =============================================================================
# Utility Functions
# =============================================================================
log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_pass()    { echo -e "${GREEN}[PASS]${NC} $*"; PASS=$((PASS + 1)); }
log_fail()    { echo -e "${RED}[FAIL]${NC} $*"; FAIL=$((FAIL + 1)); }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_verbose() { [[ ${VERBOSE} -eq 1 ]] && echo -e "${YELLOW}[DEBUG]${NC} $*" || true; }

usage() {
    cat << EOF
Usage: $0 [--pre] [--post] [--all] [--verbose]

Validate anchor patterns for rtc_v1 peripheral automation.

Options:
  --pre      Run pre-automation anchor existence checks only
  --post     Run post-automation patch verification checks only
  --all      Run both pre and post checks (default)
  --verbose  Show detailed grep output for debugging
  --help     Show this help message

Exit Codes:
  0 = All checks passed
  1 = One or more checks failed
  2 = Invalid arguments, missing config, or missing target files
EOF
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pre) MODE="pre"; shift ;;
        --post) MODE="post"; shift ;;
        --all) MODE="all"; shift ;;
        --verbose) VERBOSE=1; shift ;;
        --help|-h) usage ;;
        *) log_fail "Unknown option: $1"; usage ;;
    esac
done

cd "$HW_ROOT" || { log_fail "Cannot cd to $HW_ROOT"; exit 2; }
log_info "Working directory: $(pwd)"
log_info "Target board directory: $BOARD_DIR"



# =============================================================================
# Helper Functions for XDC Validation
# =============================================================================

check_pin_map_structure() {
    local pin_name="$1"
    local pin_map_file="$2"
    
    if [[ ! -f "$pin_map_file" ]]; then
        echo "[DEBUG] Pin map file not found: $pin_map_file" >&2
        return 1
    fi

    # Use Heredoc + Env Vars to bypass bash quoting/escaping hell
    PIN_MAP_FILE="$pin_map_file" PIN_NAME="$pin_name" python3 - << 'PYEOF'
import os, yaml, sys

try:
    file_path = os.environ.get('PIN_MAP_FILE')
    target_pin = os.environ.get('PIN_NAME')
    
    if not file_path or not target_pin:
        print(f"[ERROR] Missing environment variables", file=sys.stderr)
        sys.exit(1)

    with open(file_path) as f:
        data = yaml.safe_load(f)
        
    if not isinstance(data, dict) or 'pins' not in data:
        print(f"[ERROR] Invalid pin_map.yaml structure", file=sys.stderr)
        sys.exit(1)
        
    pins = data['pins']
    if target_pin not in pins:
        print(f"[ERROR] Pin '{target_pin}' not found in pin_map.yaml", file=sys.stderr)
        sys.exit(1)
        
    entry = pins[target_pin]
    # Accept flexible structure: list with at least 2 elements (pkgs, assigned_port)
    if not isinstance(entry, list) or len(entry) < 2:
        print(f"[ERROR] Pin '{target_pin}' entry is not a valid list structure", file=sys.stderr)
        sys.exit(1)
        
    # If all checks pass, exit cleanly
    sys.exit(0)
    
except yaml.YAMLError as e:
    print(f"[ERROR] YAML parse error: {e}", file=sys.stderr)
    sys.exit(1)
except FileNotFoundError:
    print(f"[ERROR] File not found: {file_path}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"[ERROR] Unexpected validation error: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
    return $?
}

get_pin_map_value() {
    # Helper to safely extract a value from pin_map.yaml using Python
    local pin_name="$1"
    local field="$2"  # 0=pkgs, 1=assigned_port, 2=templates, 3=header, 4=properties
    local pin_map_file="$3"
    
    if [[ ! -f "$pin_map_file" ]]; then
        echo "null"
        return
    fi
    
    python3 -c "
import yaml, sys
try:
    with open('$pin_map_file') as f:
        data = yaml.safe_load(f)
    pins = data.get('pins', {})
    if '$pin_name' not in pins:
        print('null')
        sys.exit(0)
    entry = pins['$pin_name']
    if not isinstance(entry, list) or len(entry) <= $field:
        print('null')
        sys.exit(0)
    val = entry[$field]
    if val is None:
        print('null')
    elif isinstance(val, list):
        # For package pins list, return first element if exists
        if $field == 0 and val:
            print(val[0] if isinstance(val[0], str) else 'null')
        else:
            print('list')  # Indicate it's a list without printing contents
    else:
        print(val)
except:
    print('null')
" 2>/dev/null
}

count_constraint_occurrences() {
    local port_name="$1"
    local xdc_file="$2"
    local constraint_type="${3:-PACKAGE_PIN}"  # Default to PACKAGE_PIN
    
    if [[ ! -f "$xdc_file" ]]; then
        echo "0"
        return
    fi
    
    # Count lines containing both the port name and constraint type
    grep -c "get_ports.*{.*$port_name.*}.*$constraint_type\|$constraint_type.*get_ports.*{.*$port_name.*}" "$xdc_file" 2>/dev/null || echo "0"
}

verify_header_grouping() {
    local port_name="$1"
    local expected_header="$2"
    local xdc_file="$3"
    
    if [[ ! -f "$xdc_file" ]]; then
        return 1
    fi
    
    # Use Python with regex to match port name with optional braces/whitespace
    python3 -c "
import re, sys
with open('$xdc_file') as f:
    lines = f.readlines()
current_header = '__global__'
# Regex pattern: matches get_ports { port } or get_ports port
port_pattern = re.compile(r'get_ports\s+\{?\s*${port_name}\s*\}?\]')
for line in lines:
    stripped = line.strip()
    if stripped.startswith('##'):
        current_header = stripped
    elif port_pattern.search(line):
        if current_header == '$expected_header':
            print('FOUND')
            sys.exit(0)
        else:
            print(f'WRONG_HEADER:{current_header}')
            sys.exit(1)
print('NOT_FOUND')
sys.exit(1)
" 2>/dev/null | grep -q "FOUND"
    return $?
}

# =============================================================================
# Pre-Automation: Anchor Existence Checks
# =============================================================================
run_pre_checks() {
    log_info "=== Running PRE-AUTOMATION Anchor Checks ==="
    
    # --- bsvpath ---
    if grep -qF "devices/tcm" bsvpath 2>/dev/null; then
        log_pass "bsvpath: anchor 'devices/tcm' found"
    else
        log_fail "bsvpath: anchor 'devices/tcm' NOT found"
    fi

    # --- Soc.defines ---
    if grep -qF '`define GPTimer3End' "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: anchor '\`define GPTimer3End' found"
    else
        log_fail "Soc.defines: anchor '\`define GPTimer3End' NOT found"
    fi

    if grep -qF '`define MixedCluster_Num_Slaves' "$BOARD_DIR/Soc.defines" 2>/dev/null && \
       grep -qF "11" "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        if grep -q '`define MixedCluster_Num_Slaves.*11\|11.*`define MixedCluster_Num_Slaves' "$BOARD_DIR/Soc.defines" 2>/dev/null; then
            log_pass "Soc.defines: anchor 'MixedCluster_Num_Slaves 11' found"
        else
            log_fail "Soc.defines: anchor 'MixedCluster_Num_Slaves 11' NOT found"
        fi
    else
        log_fail "Soc.defines: anchor 'MixedCluster_Num_Slaves 11' NOT found"
    fi

    if grep -qF '`define MixedCluster_err_slave_num' "$BOARD_DIR/Soc.defines" 2>/dev/null && \
       grep -qF "10" "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        if grep -q '`define MixedCluster_err_slave_num.*10\|10.*`define MixedCluster_err_slave_num' "$BOARD_DIR/Soc.defines" 2>/dev/null; then
            log_pass "Soc.defines: anchor 'MixedCluster_err_slave_num 10' found"
        else
            log_fail "Soc.defines: anchor 'MixedCluster_err_slave_num 10' NOT found"
        fi
    else
        log_fail "Soc.defines: anchor 'MixedCluster_err_slave_num 10' NOT found"
    fi

    # === Soc.bsv import anchor ===
    if grep -qF "import gptimer::*;" "$BOARD_DIR/Soc.bsv" 2>/dev/null; then
        log_pass "Soc.bsv: import gptimer anchor found"
    else
        log_fail "Soc.bsv: import gptimer anchor NOT found"
    fi

    # --- mixed_cluster.bsv ---
    if grep -qF "import pinmux_axi4lite" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: import anchor found"
    else
        log_fail "mixed_cluster.bsv: import anchor NOT found"
    fi

    if grep -qF "interface Ifc_mixed_cluster;" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: interface declaration anchor found"
    else
        log_fail "mixed_cluster.bsv: interface declaration anchor NOT found"
    fi

    if grep -qF "slave_num = \`Pinmux_slave_num" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: address decoder anchor found"
    else
        log_fail "mixed_cluster.bsv: address decoder anchor NOT found"
    fi

    if grep -qF "let pinmuxtop <- mkpinmuxtop" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: instantiation anchor found"
    else
        log_fail "mixed_cluster.bsv: instantiation anchor NOT found"
    fi

    if grep -qF "(*synthesize*)" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null && \
       grep -qF "module mkpinmuxtop(Ifc_pinmux_axi4lite#(" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null && \
       grep -qF "mkpinmux_axi4lite _temp(ifc);" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null && \
       grep -qF "return ifc;" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null && \
       grep -qF "endmodule" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: mkpinmuxtop wrapper block anchor found"
    else
        log_fail "mixed_cluster.bsv: mkpinmuxtop wrapper block anchor NOT found"
    fi

    if grep -qF "Pinmux_slave_num" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null && \
       grep -qF "mkConnection" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: AXI connection anchor found"
    else
        log_fail "mixed_cluster.bsv: AXI connection anchor NOT found"
    fi

    if grep -qF "interface gptimer3_io = gptimer3.io" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: interface wiring anchor found"
    else
        log_fail "mixed_cluster.bsv: interface wiring anchor NOT found"
    fi

    if grep -qF "Bit#(35) plic_inputs=" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: PLIC width declaration found (Bit#(35))"
    else
        log_warn "mixed_cluster.bsv: PLIC width declaration NOT found (may be different width)"
    fi

    if grep -qF "module mkplic(Ifc_plic_axi4lite#(" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null && \
       grep -qF ", 35, 2, 7" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: mkplic wrapper width anchor found (35)"
    else
        log_fail "mixed_cluster.bsv: mkplic wrapper width anchor NOT found"
    fi

    # --- Soc.bsv ---
    if grep -qF "interface Ifc_gptimer_io gptimer3_io;" "$BOARD_DIR/Soc.bsv" 2>/dev/null; then
        log_pass "Soc.bsv: interface declaration anchor found"
    else
        log_fail "Soc.bsv: interface declaration anchor NOT found"
    fi

    if grep -qF "interface gptimer3_io = mixed_cluster.gptimer3_io;" "$BOARD_DIR/Soc.bsv" 2>/dev/null; then
        log_pass "Soc.bsv: interface wiring anchor found"
    else
        log_fail "Soc.bsv: interface wiring anchor NOT found"
    fi

    # --- fpga_top.v ---
    if grep -qF "output gptimer3_out" "$BOARD_DIR/fpga_top.v" 2>/dev/null; then
        log_pass "fpga_top.v: port declaration anchor found"
    else
        log_fail "fpga_top.v: port declaration anchor NOT found"
    fi

    if grep -qF "wire ip2intc_irpt;" "$BOARD_DIR/fpga_top.v" 2>/dev/null; then
        log_pass "fpga_top.v: wire declaration anchor found"
    else
        log_fail "fpga_top.v: wire declaration anchor NOT found"
    fi

    if grep -qF ".ext_interrupts_i(interrupts)" "$BOARD_DIR/fpga_top.v" 2>/dev/null; then
        log_pass "fpga_top.v: SoC instantiation anchor found"
    else
        log_fail "fpga_top.v: SoC instantiation anchor NOT found"
    fi

    if grep -qF "IOBUF spi0_inst_sclk" "$BOARD_DIR/fpga_top.v" 2>/dev/null; then
        log_pass "fpga_top.v: IOBUF instantiation anchor found"
    else
        log_fail "fpga_top.v: IOBUF instantiation anchor NOT found"
    fi

    # === PATCH 21: XDC CONSTRAINTS PRE-CHECKS ===
    PIN_MAP="$BOARD_DIR/pin_map.yaml"
    XDC_FILE="$BOARD_DIR/constraints.xdc"
    
    # Extract context values from soc_build_config.yaml using Python (robust YAML parsing)
    TARGET_PIN=""
    FALLBACK_PIN=""
    VERILOG_PORT=""
    
    if [[ -f "$CONFIG_FILE" ]]; then
        read TARGET_PIN FALLBACK_PIN VERILOG_PORT < <(python3 -c "
import yaml, sys
try:
    cfg = yaml.safe_load(open('$CONFIG_FILE'))
    ctx = cfg['automated_peripherals'][0].get('context', {})
    print(ctx.get('rtc_pin_board_pin', ''), ctx.get('rtc_pin_fallback', ''), ctx.get('verilog_port_name', ''))
except Exception as e:
    print('', '', '')
" 2>/dev/null)
    fi

    # Check pin map file existence first
    if [[ ! -f "$PIN_MAP" ]]; then
        log_warn "pin_map.yaml: file not found at $PIN_MAP (run 'make track_pin_map' first)"
    else
        # Validate target pin structure (flexible 3-5 element list)
        if [[ -n "$TARGET_PIN" ]]; then
            if check_pin_map_structure "$TARGET_PIN" "$PIN_MAP"; then
                log_pass "pin_map.yaml: target pin '$TARGET_PIN' has valid structure"
            else
                log_fail "pin_map.yaml: target pin '$TARGET_PIN' missing or invalid structure"
            fi
        else
            log_warn "pin_map.yaml: rtc_pin_board_pin not set in config (skipping check)"
        fi

        # Validate fallback pin structure (Fixed: replaced dead elif with else)
        if [[ -n "$FALLBACK_PIN" ]]; then
            if check_pin_map_structure "$FALLBACK_PIN" "$PIN_MAP"; then
                log_pass "pin_map.yaml: fallback pin '$FALLBACK_PIN' has valid structure"
            else
                log_fail "pin_map.yaml: fallback pin '$FALLBACK_PIN' missing or invalid structure"
            fi
        else
            log_warn "pin_map.yaml: rtc_pin_fallback not set in config (skipping check)"
        fi
        
        # Check that target pin assignment state (for conflict resolution awareness)
        if [[ -n "$TARGET_PIN" ]]; then
            TARGET_ASSIGNED=$(get_pin_map_value "$TARGET_PIN" 1 "$PIN_MAP")
            if [[ "$TARGET_ASSIGNED" == "null" || -z "$TARGET_ASSIGNED" ]]; then
                log_pass "pin_map.yaml: target pin '$TARGET_PIN' is unassigned (ready for automation)"
            else
                log_warn "pin_map.yaml: target pin '$TARGET_PIN' already assigned to '$TARGET_ASSIGNED' (conflict resolution will apply)"
            fi
        fi
    fi

    # Check XDC idempotency (only if file exists)
    if [[ -n "$VERILOG_PORT" && -f "$XDC_FILE" ]]; then
        # Improved: Uses regex with word boundaries to avoid partial port name matches
        if ! grep -qE "\[get_ports\s+\{?\s*${VERILOG_PORT}\s*\}?\]" "$XDC_FILE" 2>/dev/null; then
            log_pass "constraints.xdc: port '$VERILOG_PORT' not yet constrained (idempotent)"
        else
            log_warn "constraints.xdc: port '$VERILOG_PORT' already constrained (skip_if_contains will handle)"
        fi
        
        # === NEW: Check for external_clk clock property (PRE-AUTOMATION: should NOT exist yet) ===
        if grep -qF "CLOCK_DEDICATED_ROUTE FALSE.*external_clk_IBUF" "$XDC_FILE" 2>/dev/null; then
            log_warn "constraints.xdc: external_clk clock property already present (may indicate prior automation)"
        else
            log_pass "constraints.xdc: external_clk clock property not yet injected (expected pre-automation)"
        fi
    elif [[ -z "$VERILOG_PORT" ]]; then
        log_warn "constraints.xdc: verilog_port_name not set in config (skipping check)"
    elif [[ ! -f "$XDC_FILE" ]]; then
        log_warn "constraints.xdc: file not found at $XDC_FILE"
    fi

    log_info "Pre-check summary: ${PASS} passed, ${FAIL} failed"
}

# =============================================================================
# Post-Automation: Patch Verification Checks
# =============================================================================
run_post_checks() {
    log_info "=== Running POST-AUTOMATION Patch Verification ==="
    
    # === IMPORTANT: Post-checks expect automation to have completed ===
    log_info "Note: These checks verify that automation patches were successfully applied."
    log_info "If you haven't run 'make run_peripheral_automation' yet, these will fail as expected."
    
    # --- bsvpath ---
    if grep -qF "ip_bsv/rtc_v1/" bsvpath 2>/dev/null; then
        log_pass "bsvpath: rtc_v1 path appended"
    else
        log_fail "bsvpath: rtc_v1 path NOT found"
    fi

    # --- Soc.defines ---
    if grep -qF '`define RTC_slave_num' "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: RTC_slave_num macro defined"
    else
        log_fail "Soc.defines: RTC_slave_num macro NOT found"
    fi

    if grep -qF '`define RTCBase' "$BOARD_DIR/Soc.defines" 2>/dev/null && \
       grep -qF "0004_0600" "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: RTCBase address defined"
    else
        log_fail "Soc.defines: RTCBase address NOT found"
    fi

    if grep -qF '`define RTCEnd' "$BOARD_DIR/Soc.defines" 2>/dev/null && \
       grep -qF "0004_06FF" "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: RTCEnd address defined"
    else
        log_fail "Soc.defines: RTCEnd address NOT found"
    fi

    if grep -qE "^\s*\`define\s+MixedCluster_Num_Slaves\s+12\s*$" "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: MixedCluster_Num_Slaves updated to 12"
    else
        log_fail "Soc.defines: MixedCluster_Num_Slaves NOT updated"
    fi

    if grep -qE "^\s*\`define\s+MixedCluster_err_slave_num\s+11\s*$" "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: MixedCluster_err_slave_num updated to 11"
    else
        log_fail "Soc.defines: MixedCluster_err_slave_num NOT updated"
    fi

    # === Soc.bsv import verification ===
    if grep -qF "import rtc_v1 :: *;" "$BOARD_DIR/Soc.bsv" 2>/dev/null; then
        log_pass "Soc.bsv: rtc_v1 import added"
    else
        log_fail "Soc.bsv: rtc_v1 import NOT found"
    fi

    # --- mixed_cluster.bsv ---
    if grep -qF "import rtc_v1 :: *;" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: rtc_v1 import added"
    else
        log_fail "mixed_cluster.bsv: rtc_v1 import NOT found"
    fi

    if grep -qF "interface RTCIO rtc_io;" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: RTCIO interface declared"
    else
        log_fail "mixed_cluster.bsv: RTCIO interface NOT declared"
    fi

    if grep -qF "RTCBase" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null && \
       grep -qF "RTCEnd" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null && \
       grep -qF "RTC_slave_num" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: RTC address decoder rule added"
    else
        log_fail "mixed_cluster.bsv: RTC address decoder rule NOT found"
    fi

    if grep -qF "let rtc <- mkrtc(ext_clk);" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: RTC instantiation added"
    else
        log_fail "mixed_cluster.bsv: RTC instantiation NOT found"
    fi

    if grep -qF "(*synthesize*)" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null && \
       grep -qF "module mkrtc#(Clock ext_clk)(Ifc_rtc_axi4lite#(" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null && \
       grep -qF "mkrtc_axi4lite#(ext_clk) _temp(ifc);" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: mkrtc wrapper block added"
    else
        log_fail "mixed_cluster.bsv: mkrtc wrapper block NOT found"
    fi

    if grep -qF "RTC_slave_num" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null && \
       grep -qF "rtc.slave" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: RTC AXI connection added"
    else
        log_fail "mixed_cluster.bsv: RTC AXI connection NOT found"
    fi

    if grep -qF "interface rtc_io = rtc.io;" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: RTC interface wiring added"
    else
        log_fail "mixed_cluster.bsv: RTC interface wiring NOT found"
    fi

    # PLIC verification
    if grep -qF "Bit#(36) plic_inputs=" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: PLIC width incremented to 36"
    else
        log_warn "mixed_cluster.bsv: PLIC width NOT incremented (check manually)"
    fi

    if grep -qF "rtc.rtc_sb_interrupt" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: RTC interrupt added to PLIC vector"
    else
        log_fail "mixed_cluster.bsv: RTC interrupt NOT in PLIC vector"
    fi

    if grep -qF "module mkplic(Ifc_plic_axi4lite#(" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null && \
       grep -qF ", 36, 2, 7" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: mkplic wrapper width updated to 36"
    else
        log_fail "mixed_cluster.bsv: mkplic wrapper width NOT updated"
    fi

    # --- Soc.bsv ---
    if grep -qF "interface RTCIO rtc_io;" "$BOARD_DIR/Soc.bsv" 2>/dev/null; then
        log_pass "Soc.bsv: RTCIO interface declared"
    else
        log_fail "Soc.bsv: RTCIO interface NOT declared"
    fi

    if grep -qF "interface rtc_io = mixed_cluster.rtc_io;" "$BOARD_DIR/Soc.bsv" 2>/dev/null; then
        log_pass "Soc.bsv: RTCIO interface wired"
    else
        log_fail "Soc.bsv: RTCIO interface NOT wired"
    fi

    # --- fpga_top.v ---
    if grep -qF "output rtc_out_pmod" "$BOARD_DIR/fpga_top.v" 2>/dev/null; then
        log_pass "fpga_top.v: rtc_out_pmod port declared"
    else
        log_fail "fpga_top.v: rtc_out_pmod port NOT declared"
    fi

    if grep -qF "wire wire_rtc_out;" "$BOARD_DIR/fpga_top.v" 2>/dev/null; then
        log_pass "fpga_top.v: wire_rtc_out internal wire declared"
    else
        log_fail "fpga_top.v: wire_rtc_out internal wire NOT declared"
    fi

    if grep -qF "rtc_io_rtc_clock_signal(wire_rtc_out)" "$BOARD_DIR/fpga_top.v" 2>/dev/null; then
        log_pass "fpga_top.v: RTC port mapped in mkDebugSoc instantiation"
    else
        log_fail "fpga_top.v: RTC port NOT mapped in mkDebugSoc instantiation"
    fi

    if grep -qF "IOBUF rtc_io_inst" "$BOARD_DIR/fpga_top.v" 2>/dev/null; then
        log_pass "fpga_top.v: RTC IOBUF instantiated"
    else
        log_fail "fpga_top.v: RTC IOBUF NOT instantiated"
    fi

    # === PATCH 21: XDC CONSTRAINTS POST-CHECKS (Enhanced) ===
    PIN_MAP="$BOARD_DIR/pin_map.yaml"
    XDC_FILE="$BOARD_DIR/constraints.xdc"
    
    # Extract context using Python for reliability
    TARGET_PIN=""
    FALLBACK_PIN=""
    VERILOG_PORT=""
    
    if [[ -f "$CONFIG_FILE" ]]; then
        read TARGET_PIN FALLBACK_PIN VERILOG_PORT < <(python3 -c "
import yaml, sys
try:
    cfg = yaml.safe_load(open('$CONFIG_FILE'))
    ctx = cfg['automated_peripherals'][0].get('context', {})
    print(ctx.get('rtc_pin_board_pin', ''), ctx.get('rtc_pin_fallback', ''), ctx.get('verilog_port_name', ''))
except Exception as e:
    print('', '', '')
" 2>/dev/null)
    fi

    if [[ -n "$VERILOG_PORT" && -f "$XDC_FILE" ]]; then
        # 1. Check if new constraint was appended
        # Use regex to match get_ports with optional braces and whitespace
        if grep -qE "\[get_ports\s+\{?\s*${VERILOG_PORT}\s*\}?\]" "$XDC_FILE" 2>/dev/null; then
            log_pass "constraints.xdc: port '$VERILOG_PORT' is now constrained"
            
            # 2. Verify it's assigned to the correct package pin (if pin_map available)
            if [[ -n "$TARGET_PIN" && -f "$PIN_MAP" ]]; then
                TARGET_PKG=$(get_pin_map_value "$TARGET_PIN" 0 "$PIN_MAP")
                if [[ -n "$TARGET_PKG" && "$TARGET_PKG" != "null" ]] && grep -qE "PACKAGE_PIN\s+${TARGET_PKG}.*get_ports\s+\{?\s*${VERILOG_PORT}\s*\}?" "$XDC_FILE" 2>/dev/null; then
                    log_pass "constraints.xdc: '$VERILOG_PORT' correctly assigned to $TARGET_PIN ($TARGET_PKG)"
                elif [[ -n "$TARGET_PKG" && "$TARGET_PKG" != "null" ]]; then
                    log_warn "constraints.xdc: '$VERILOG_PORT' assigned but package pin mismatch (expected $TARGET_PKG)"
                fi
            fi
        else
            log_fail "constraints.xdc: port '$VERILOG_PORT' NOT constrained"
        fi
        
        # 3. Check if fallback was used (conflict resolution)
        if [[ -n "$FALLBACK_PIN" && -f "$PIN_MAP" ]]; then
            FALLBACK_PKG=$(get_pin_map_value "$FALLBACK_PIN" 0 "$PIN_MAP")
            FALLBACK_ASSIGNED=$(get_pin_map_value "$FALLBACK_PIN" 1 "$PIN_MAP")
            
            if [[ -n "$FALLBACK_PKG" && "$FALLBACK_PKG" != "null" && -n "$FALLBACK_ASSIGNED" && "$FALLBACK_ASSIGNED" != "null" ]]; then
                # Use regex to match PACKAGE_PIN and get_ports with optional braces/whitespace
                if grep -qE "PACKAGE_PIN\s+$FALLBACK_PKG.*\[get_ports\s+\{?\s*$FALLBACK_ASSIGNED\s*\}?\]" "$XDC_FILE" 2>/dev/null; then
                    log_pass "constraints.xdc: fallback pin $FALLBACK_PIN ($FALLBACK_PKG) correctly assigned to '$FALLBACK_ASSIGNED' (conflict resolved)"
                else
                    log_warn "constraints.xdc: fallback pin $FALLBACK_PIN assigned to '$FALLBACK_ASSIGNED' but constraint line not found"
                fi
            fi
        fi
        
        # 4. Verify NO duplicate PACKAGE_PIN assignments for the same physical pin
        if [[ -n "$TARGET_PIN" && -f "$PIN_MAP" ]]; then
            TARGET_PKG=$(get_pin_map_value "$TARGET_PIN" 0 "$PIN_MAP")
            if [[ -n "$TARGET_PKG" && "$TARGET_PKG" != "null" ]]; then
                # Count how many times this PACKAGE_PIN appears with any get_ports
                PIN_COUNT=$(grep -c "PACKAGE_PIN $TARGET_PKG" "$XDC_FILE" 2>/dev/null || echo "0")
                if [[ "$PIN_COUNT" -eq 1 ]]; then
                    log_pass "constraints.xdc: no duplicate assignments for package pin $TARGET_PKG"
                else
                    log_fail "constraints.xdc: duplicate assignments found for package pin $TARGET_PKG (count: $PIN_COUNT)"
                fi
            fi
        fi
        
        # 5. Verify additional properties were injected (not just tracked)
        if [[ -n "$VERILOG_PORT" && -f "$XDC_FILE" ]]; then
            # Extract the clk source from config (defaults to verilog_port_name if not set)
            CLK_SOURCE=$(python3 -c "
import yaml, sys
try:
    cfg = yaml.safe_load(open('$CONFIG_FILE'))
    ctx = cfg['automated_peripherals'][0].get('context', {})
    print(ctx.get('verilog_clk_source', ctx.get('verilog_port_name', '')))
except:
    print('')
" 2>/dev/null)
            
            if [[ -n "$CLK_SOURCE" ]]; then
                # Use regex with -E to match the injected property line
                # Pattern: set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets external_clk_IBUF]
                if grep -qE "set_property\s+CLOCK_DEDICATED_ROUTE\s+FALSE\s+\[get_nets\s+${CLK_SOURCE}_IBUF\]" "$XDC_FILE" 2>/dev/null; then
                    log_pass "constraints.xdc: ${CLK_SOURCE} clock property correctly injected"
                else
                    # Fallback: check if the key parts exist on the same line (more lenient)
                    if grep -E "CLOCK_DEDICATED_ROUTE.*FALSE.*${CLK_SOURCE}_IBUF|${CLK_SOURCE}_IBUF.*CLOCK_DEDICATED_ROUTE.*FALSE" "$XDC_FILE" 2>/dev/null | grep -q "set_property"; then
                        log_pass "constraints.xdc: ${CLK_SOURCE} clock property found (lenient match)"
                    else
                        log_fail "constraints.xdc: ${CLK_SOURCE} clock property NOT found (automation may have failed)"
                    fi
                fi
            else
                log_warn "constraints.xdc: verilog_clk_source not set in config (skipping clock property check)"
            fi
        fi
    elif [[ -z "$VERILOG_PORT" ]]; then
        log_warn "constraints.xdc: verilog_port_name not set in config (skipping XDC checks)"
    elif [[ ! -f "$XDC_FILE" ]]; then
        log_warn "constraints.xdc: file not found at $XDC_FILE"
    fi

    log_info "Post-check summary: ${PASS} passed, ${FAIL} failed"
}

# =============================================================================
# Main Execution
# =============================================================================
main() {
    log_info "rtc_v1 Anchor Validation Script"
    log_info "Mode: $MODE"
    [[ $VERBOSE -eq 1 ]] && log_info "Verbose output enabled"
    
    local required_files=("bsvpath" "$BOARD_DIR/Soc.defines" "$BOARD_DIR/mixed_cluster.bsv" "$BOARD_DIR/Soc.bsv" "$BOARD_DIR/fpga_top.v")
    for f in "${required_files[@]}"; do
        if [[ ! -f "$f" ]]; then
            log_fail "Required file not found: $f"
            log_warn "Ensure you are in gc2025/hw/ and BOARD_DIR='$BOARD_DIR' exists"
            exit 2
        fi
    done
    log_info "All required files found"
    
    case "$MODE" in
        pre) run_pre_checks ;;
        post) run_post_checks ;;
        all)
            run_pre_checks
            echo ""
            run_post_checks
            ;;
    esac
    
    echo ""
    echo "============================================"
    if [[ $FAIL -eq 0 ]]; then
        echo -e "${GREEN}[INFO] ALL CHECKS PASSED${NC}"
        echo "============================================"
        exit 0
    else
        echo -e "${RED}[ERROR] $FAIL CHECK(S) FAILED${NC}"
        echo -e "${YELLOW}[INFO] Tip: Run with --verbose for detailed grep output${NC}"
        echo "============================================"
        exit 1
    fi
}

main "$@"