#!/usr/bin/env bash
# =============================================================================
# uart_validate.sh — Anchor Pattern Validation for uart_v1 Automation (V2)
# Location: gc2025/hw/ip_bsv/uart_v1/uart_validate.sh
#
# Mirrors rtc_validate.sh structure. Validates UART3 integration via the
# existing Shakti UART IP (no new register map / no new BSV module — UART3
# reuses mkuart()). Covers Soc.defines, uart_cluster.bsv, pinmux.bsv, Soc.bsv,
# and the shared mixed_cluster.bsv PLIC vector + interrupt-bus widening.
# =============================================================================

set -euo pipefail

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

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0
VERBOSE=0
MODE="all"

log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_pass()    { echo -e "${GREEN}[PASS]${NC} $*"; PASS=$((PASS + 1)); }
log_fail()    { echo -e "${RED}[FAIL]${NC} $*"; FAIL=$((FAIL + 1)); }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }

usage() {
    cat << EOF
Usage: $0 [--pre] [--post] [--all] [--verbose]

Validate anchor patterns for uart_v1 (UART3) peripheral automation.

Options:
  --pre      Run pre-automation anchor existence checks only
  --post     Run post-automation patch verification checks only
  --all      Run both pre and post checks (default)
  --verbose  Show detailed output for debugging
  --help     Show this help message
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
# Pre-Automation: Anchor Existence Checks
# =============================================================================
run_pre_checks() {
    log_info "=== Running PRE-AUTOMATION Anchor Checks (uart_v1 / UART3) ==="

    if grep -qE '`define UARTCluster_Num_Slaves\s+4' "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: UARTCluster_Num_Slaves is 4 (pre-automation)"
    else
        log_warn "Soc.defines: UARTCluster_Num_Slaves is not 4 (may already be patched)"
    fi

    if grep -qE '`define UARTCluster_err_slave_num\s+3' "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: UARTCluster_err_slave_num is 3 (pre-automation)"
    else
        log_warn "Soc.defines: UARTCluster_err_slave_num is not 3 (may already be patched)"
    fi

    if grep -qF '`define UART2End' "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: UART2End anchor found"
    else
        log_fail "Soc.defines: UART2End anchor NOT found"
    fi

    if grep -qF "interface RS232 uart2_io;" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: uart2_io interface anchor found"
    else
        log_fail "uart_cluster.bsv: uart2_io interface anchor NOT found"
    fi

    if grep -qF "method Bit#(3) uart_interrupts;" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: Bit#(3) uart_interrupts anchor found"
    else
        log_warn "uart_cluster.bsv: Bit#(3) uart_interrupts not found (may already be patched)"
    fi

    if grep -qF "let uart2 <- mkuart();" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: uart2 instantiation anchor found"
    else
        log_fail "uart_cluster.bsv: uart2 instantiation anchor NOT found"
    fi

    if grep -qE "slave_num\s+=\s+\`UART2_slave_num;" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: UART2 address decoder anchor found"
    else
        log_fail "uart_cluster.bsv: UART2 address decoder anchor NOT found"
    fi

    if grep -qF "mkConnection (fabric.v_to_slaves [\`UART2_slave_num ],uart2.slave);" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: UART2 AXI connection anchor found"
    else
        log_fail "uart_cluster.bsv: UART2 AXI connection anchor NOT found"
    fi

    if grep -qF "interface uart2_io=uart2.io;" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: uart2_io interface wiring anchor found"
    else
        log_fail "uart_cluster.bsv: uart2_io interface wiring anchor NOT found"
    fi

    if grep -qF "return {uart2.interrupt, uart1.interrupt, uart0.interrupt};" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: uart2 interrupt return anchor found"
    else
        log_warn "uart_cluster.bsv: uart2 interrupt return anchor not found (may already be patched)"
    fi

    if grep -qF "Wire#(Bit#(1)) wruart2_tx<-mkDWire(0);" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: wruart2_tx wire anchor found"
    else
        log_fail "pinmux.bsv: wruart2_tx wire anchor NOT found"
    fi

    if grep -qF "interface PeripheralSideUART uart2;" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: PeripheralSideUART uart2 interface anchor found"
    else
        log_fail "pinmux.bsv: PeripheralSideUART uart2 interface anchor NOT found"
    fi

    if grep -qF "wrcell12_mux==1?val0: // unused" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: cell12 mux==1 unused slot found (uart3_rx anchor)"
    else
        log_warn "pinmux.bsv: cell12 mux==1 unused slot not found (may already be patched)"
    fi

    if grep -qF "wrcell13_mux==1?val0: // unused" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: cell13 mux==1 unused slot found (uart3_tx anchor)"
    else
        log_warn "pinmux.bsv: cell13 mux==1 unused slot not found (may already be patched)"
    fi

    if grep -qF "rule assign_wr_on_cell12_1(wrcell12_mux==1);" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: cell12 rule anchor found"
    else
        log_warn "pinmux.bsv: cell12 rule anchor not found (may already be patched)"
    fi

    if grep -qF "rule assign_wr_on_cell13_1(wrcell13_mux==1);" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: cell13 rule anchor found"
    else
        log_warn "pinmux.bsv: cell13 rule anchor not found (may already be patched)"
    fi

    if grep -qF "interface uart2 = interface PeripheralSideUART" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: uart2 peripheral side implementation anchor found"
    else
        log_fail "pinmux.bsv: uart2 peripheral side implementation anchor NOT found"
    fi

    if grep -qF "mixed_cluster.pinmuxtop_peripheral_side.uart2.tx.put(uart_cluster.uart2_io.sout);" "$BOARD_DIR/Soc.bsv" 2>/dev/null; then
        log_pass "Soc.bsv: uart2 tx connection anchor found"
    else
        log_fail "Soc.bsv: uart2 tx connection anchor NOT found"
    fi

    if grep -qF "uart_cluster.uart2_io.sin(pinmux_uart2_rx);" "$BOARD_DIR/Soc.bsv" 2>/dev/null; then
        log_pass "Soc.bsv: uart2 rx connection anchor found"
    else
        log_fail "Soc.bsv: uart2 rx connection anchor NOT found"
    fi

    # mixed_cluster.bsv: interrupt bus widening anchors (UART3-owned; independent of RTC's PLIC patches)
    if grep -qF "method Action interrupts(Bit#(13) inp);" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: interrupts(Bit#(13)) anchor found"
    else
        log_warn "mixed_cluster.bsv: interrupts(Bit#(13)) not found (may already be patched)"
    fi

    if grep -qF "Wire#(Bit#(13)) wr_external_interrupts <- mkDWire('d0);" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: wr_external_interrupts Bit#(13) anchor found"
    else
        log_warn "mixed_cluster.bsv: wr_external_interrupts Bit#(13) not found (may already be patched)"
    fi

    log_info "Pre-check summary: ${PASS} passed, ${FAIL} failed"
}

# =============================================================================
# Post-Automation: Patch Verification Checks
# =============================================================================
run_post_checks() {
    log_info "=== Running POST-AUTOMATION Patch Verification (uart_v1 / UART3) ==="
    log_info "Note: run after 'make run_autointeg_bsv' for uart_v1."

    if grep -qE '`define UARTCluster_Num_Slaves\s+5' "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: UARTCluster_Num_Slaves updated to 5"
    else
        log_fail "Soc.defines: UARTCluster_Num_Slaves NOT updated to 5"
    fi

    if grep -qF '`define UART3_slave_num 3' "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: UART3_slave_num defined as 3"
    else
        log_fail "Soc.defines: UART3_slave_num NOT defined"
    fi

    if grep -qE '`define UARTCluster_err_slave_num\s+4' "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: UARTCluster_err_slave_num updated to 4"
    else
        log_fail "Soc.defines: UARTCluster_err_slave_num NOT updated to 4"
    fi

    if grep -qF '`define UART3Base' "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: UART3Base defined"
    else
        log_fail "Soc.defines: UART3Base NOT defined"
    fi

    if grep -qF '`define UART3End' "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_pass "Soc.defines: UART3End defined"
    else
        log_fail "Soc.defines: UART3End NOT defined"
    fi

    if grep -qF "interface RS232 uart3_io;" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: uart3_io interface declared"
    else
        log_fail "uart_cluster.bsv: uart3_io interface NOT declared"
    fi

    if grep -qF "let uart3 <- mkuart();" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: uart3 instantiated"
    else
        log_fail "uart_cluster.bsv: uart3 NOT instantiated"
    fi

    if grep -qF '`UART3Base' "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null && \
       grep -qF '`UART3End' "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null && \
       grep -qF '`UART3_slave_num' "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: UART3 address decoder added"
    else
        log_fail "uart_cluster.bsv: UART3 address decoder NOT found"
    fi

    if grep -qF "uart3.slave" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: uart3 AXI connection added"
    else
        log_fail "uart_cluster.bsv: uart3 AXI connection NOT found"
    fi

    if grep -qF "interface uart3_io=uart3.io;" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: uart3_io interface wired"
    else
        log_fail "uart_cluster.bsv: uart3_io interface NOT wired"
    fi

    if grep -qF "method Bit#(4) uart_interrupts;" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: uart_interrupts width updated to Bit#(4)"
    else
        log_fail "uart_cluster.bsv: uart_interrupts width NOT updated to Bit#(4)"
    fi

    if grep -qF "uart3.interrupt" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_pass "uart_cluster.bsv: uart3.interrupt added to return"
    else
        log_fail "uart_cluster.bsv: uart3.interrupt NOT in return statement"
    fi

    if grep -qF "Wire#(Bit#(1)) wruart3_tx<-mkDWire(0);" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: wruart3_tx wire declared"
    else
        log_fail "pinmux.bsv: wruart3_tx wire NOT declared"
    fi

    if grep -qF "Wire#(Bit#(1)) wruart3_rx<-mkDWire(0);" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: wruart3_rx wire declared"
    else
        log_fail "pinmux.bsv: wruart3_rx wire NOT declared"
    fi

    if grep -qF "interface PeripheralSideUART uart3;" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: PeripheralSideUART uart3 interface declared"
    else
        log_fail "pinmux.bsv: PeripheralSideUART uart3 interface NOT declared"
    fi

    if grep -qF "uart3_rx is an input" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: cell12 wired to uart3_rx"
    else
        log_fail "pinmux.bsv: cell12 NOT wired to uart3_rx"
    fi

    if grep -qF "uart3_tx is an output" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: cell13 wired to uart3_tx"
    else
        log_fail "pinmux.bsv: cell13 NOT wired to uart3_tx"
    fi

    if grep -qF "assign_wruart3_rx_on_cell12" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: uart3_rx cell12 rule added"
    else
        log_fail "pinmux.bsv: uart3_rx cell12 rule NOT found"
    fi

    if grep -qF "assign_wruart3_tx_on_cell13" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: uart3_tx cell13 rule added"
    else
        log_fail "pinmux.bsv: uart3_tx cell13 rule NOT found"
    fi

    if grep -qF "wruart3_tx<=in;" "$BOARD_DIR/pinmux.bsv" 2>/dev/null && \
       grep -qF "return wruart3_rx;" "$BOARD_DIR/pinmux.bsv" 2>/dev/null; then
        log_pass "pinmux.bsv: uart3 peripheral side tx/rx implemented"
    else
        log_fail "pinmux.bsv: uart3 peripheral side tx/rx NOT implemented"
    fi

    if grep -qF "uart3_io.sout" "$BOARD_DIR/Soc.bsv" 2>/dev/null; then
        log_pass "Soc.bsv: uart3 tx connection added"
    else
        log_fail "Soc.bsv: uart3 tx connection NOT found"
    fi

    if grep -qF "pinmux_uart3_rx" "$BOARD_DIR/Soc.bsv" 2>/dev/null; then
        log_pass "Soc.bsv: uart3 rx connection added"
    else
        log_fail "Soc.bsv: uart3 rx connection NOT found"
    fi

    if grep -qF "method Action interrupts(Bit#(14) inp);" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: interrupts width updated to Bit#(14)"
    else
        log_fail "mixed_cluster.bsv: interrupts width NOT updated to Bit#(14)"
    fi

    if grep -qF "Wire#(Bit#(14)) wr_external_interrupts <- mkDWire('d0);" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: wr_external_interrupts widened to Bit#(14)"
    else
        log_fail "mixed_cluster.bsv: wr_external_interrupts NOT widened to Bit#(14)"
    fi

    if grep -qF "wr_external_interrupts[13]" "$BOARD_DIR/mixed_cluster.bsv" 2>/dev/null; then
        log_pass "mixed_cluster.bsv: wr_external_interrupts[13] present in PLIC vector"
    else
        log_fail "mixed_cluster.bsv: wr_external_interrupts[13] NOT found in PLIC vector"
    fi

    # --- Negative checks ---
    log_info "--- Negative checks (these should NOT be present) ---"

    if grep -qF "method Bit#(3) uart_interrupts;" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null; then
        log_fail "uart_cluster.bsv: old Bit#(3) uart_interrupts still present"
    else
        log_pass "uart_cluster.bsv: old Bit#(3) uart_interrupts correctly removed"
    fi

    if grep -qE '`define UARTCluster_Num_Slaves\s+4' "$BOARD_DIR/Soc.defines" 2>/dev/null; then
        log_fail "Soc.defines: old UARTCluster_Num_Slaves 4 still present"
    else
        log_pass "Soc.defines: old UARTCluster_Num_Slaves 4 correctly removed"
    fi

    # --- Duplicate detection ---
    log_info "--- Duplicate detection checks ---"

    UART3_DECODER_COUNT=$(grep -c "UART3Base" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null || echo "0")
    if [[ "$UART3_DECODER_COUNT" -eq 1 ]]; then
        log_pass "uart_cluster.bsv: exactly one UART3 address decoder entry"
    else
        log_fail "uart_cluster.bsv: duplicate UART3 address decoder entries (count: $UART3_DECODER_COUNT)"
    fi

    UART3_CONN_COUNT=$(grep -c "uart3.slave" "$BOARD_DIR/uart_cluster.bsv" 2>/dev/null || echo "0")
    if [[ "$UART3_CONN_COUNT" -eq 1 ]]; then
        log_pass "uart_cluster.bsv: exactly one uart3 AXI connection"
    else
        log_fail "uart_cluster.bsv: duplicate uart3 AXI connections (count: $UART3_CONN_COUNT)"
    fi

    log_info "Post-check summary: ${PASS} passed, ${FAIL} failed"
}

# =============================================================================
# Main Execution
# =============================================================================
main() {
    log_info "uart_v1 (UART3) Anchor Validation Script — V2"
    log_info "Mode: $MODE"

    local required_files=("$BOARD_DIR/Soc.defines" "$BOARD_DIR/uart_cluster.bsv" "$BOARD_DIR/pinmux.bsv" "$BOARD_DIR/Soc.bsv" "$BOARD_DIR/mixed_cluster.bsv")
    for f in "${required_files[@]}"; do
        if [[ ! -f "$f" ]]; then
            log_fail "Required file not found: $f"
            exit 2
        fi
    done
    log_info "All required files found"

    case "$MODE" in
        pre) run_pre_checks ;;
        post) run_post_checks ;;
        all) run_pre_checks; echo ""; run_post_checks ;;
    esac

    echo ""
    echo "============================================"
    if [[ $FAIL -eq 0 ]]; then
        echo -e "${GREEN}[INFO] ALL CHECKS PASSED${NC}"
        exit 0
    else
        echo -e "${RED}[ERROR] $FAIL CHECK(S) FAILED${NC}"
        exit 1
    fi
}

main "$@"
