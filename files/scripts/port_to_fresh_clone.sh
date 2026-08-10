#!/usr/bin/env bash
# Port the peripheral-automation framework INTO a fresh clone's hw/ directory.
#
# Copies every framework "setup file" (engine, IP defs, config, docs, board
# bootstrap seed) and injects the Makefile automation targets. It does NOT touch
# the board sources: those stay exactly as the fresh clone shipped them -- the
# framework seeds their anchors itself on the first `make bootstrap_board` /
# pre_validate (see bootstrap_board_anchors). Idempotent: re-running only
# refreshes files and never double-appends the Makefile block.
#
# Usage:
#   scripts/port_to_fresh_clone.sh <dest_hw_dir> [--smoke-test]
#     <dest_hw_dir>  the hw/ directory of the fresh clone to install into
#     --smoke-test   after porting, run bootstrap_board + pre_validate there
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # this repo's hw/
DST="${1:?usage: port_to_fresh_clone.sh <dest_hw_dir> [--smoke-test]}"
SMOKE="${2:-}"
DST="$(cd "$DST" && pwd)"

if [ "$SRC" = "$DST" ]; then echo "[PORT] refusing to port onto itself: $SRC"; exit 2; fi
[ -f "$DST/Makefile" ] || { echo "[PORT] '$DST' has no Makefile - not an hw/ dir?"; exit 2; }

echo "[PORT] source: $SRC"
echo "[PORT] dest  : $DST"

# --- 1. Framework files (single files) -------------------------------------
FILES=(
  scripts/peripheral_auto_integrator.py
  scripts/utils.py
  scripts/indent_tools.py
  scripts/xdc_analyzer.py
  scripts/peripheral_registry.py
  scripts/migrate_yaml.py
  scripts/fresh_clone_verify.sh
  scripts/port_to_fresh_clone.sh
  soc_build_config.yaml
  soc_build_config.yaml.bak
  README_AUTOMATION.md
  UserManual.md
)
# --- 2. Framework directories (recursive) ----------------------------------
DIRS=( ip_bsv )
# --- 3. Board inputs the framework reads (per board_dir) -------------------
#   master_constraints.xdc = XDC pin source; pin_map.yaml is regenerated but
#   copied for convenience. Board *sources* are deliberately NOT copied.
BOARD_DIR="$(python3 -c "import yaml,sys; print(yaml.safe_load(open('$SRC/soc_build_config.yaml')).get('board_dir','boards/nexys_video'))")"
BOARD_NAME="$(basename "$BOARD_DIR")"     # the BOARD= the sync targets need
BOARD_FILES=( "$BOARD_DIR/master_constraints.xdc" )

copied=0
for f in "${FILES[@]}"; do
  if [ -f "$SRC/$f" ]; then mkdir -p "$DST/$(dirname "$f")"; cp -f "$SRC/$f" "$DST/$f"; echo "  + $f"; copied=$((copied+1)); fi
done
for d in "${DIRS[@]}"; do
  if [ -d "$SRC/$d" ]; then mkdir -p "$DST/$d"; cp -a "$SRC/$d/." "$DST/$d/"; echo "  + $d/ (recursive)"; copied=$((copied+1)); fi
done
for f in "${BOARD_FILES[@]}"; do
  if [ -f "$SRC/$f" ]; then mkdir -p "$DST/$(dirname "$f")"; cp -f "$SRC/$f" "$DST/$f"; echo "  + $f"; copied=$((copied+1)); fi
done

# --- 4. Makefile automation targets (idempotent append) --------------------
MARKER="Peripheral Automatic Integration Framework"
if grep -qF "$MARKER" "$DST/Makefile"; then
  echo "  = Makefile already has automation targets (left as-is)"
else
  {
    echo ""
    echo "# ============================================================================="
    echo "# --- automation targets ported by scripts/port_to_fresh_clone.sh ---"
    awk "/# ${MARKER}/{p=1} p" "$SRC/Makefile"
  } >> "$DST/Makefile"
  echo "  + Makefile automation targets appended"
fi

echo "[PORT] Done. $copied framework item(s) installed."
echo "[PORT] This board's automation targets '$BOARD_NAME'. The upstream Makefile"
echo "[PORT] defaults BOARD to a different board, so pass it explicitly:"
echo "[PORT]   cd $DST && make BOARD=$BOARD_NAME quick_build_automated"
echo "[PORT]   (or step-by-step: make BOARD=$BOARD_NAME bootstrap_board pre_validate_automation ...)"

# --- 5. Optional smoke test ------------------------------------------------
if [ "$SMOKE" = "--smoke-test" ]; then
  echo "[PORT] Smoke test: bootstrap + pre-validate in the fresh clone (BOARD=$BOARD_NAME)..."
  ( cd "$DST" && make BOARD="$BOARD_NAME" bootstrap_board && make BOARD="$BOARD_NAME" pre_validate_automation ) \
    && echo "[PORT] SMOKE TEST PASSED ✓" || { echo "[PORT] SMOKE TEST FAILED ✗"; exit 1; }
fi
