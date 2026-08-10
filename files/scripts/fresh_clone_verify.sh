#!/usr/bin/env bash
# Faithfully simulate a FRESH CLONE in $1 and run the automation end to end:
#   bootstrap -> pre-validate -> integrate (bsv+verilog+xdc) -> generate_verilog
#   -> post-validate -> restore.  Proves the framework works with zero manual
#   board seeding.  Uses the source repo's .git ($SRC_GIT) to reset the tracked
#   board files to their committed (fresh-clone) content.
set -u
CLONE="${1:?usage: fresh_clone_verify.sh <clone_hw_dir>}"
SRC_GIT="${2:?usage: fresh_clone_verify.sh <clone_hw_dir> <src_git_dir>}"
cd "$CLONE" || exit 2

# The Makefile defaults BOARD to asic_yamuna, but the automation config targets
# nexys_video. The sync targets copy boards/$(BOARD)/ up to the hw/ root, so a
# wrong BOARD silently seeds the root with the wrong SoC's Soc.defines (missing
# PinmuxConfigReg etc.) and the compile fails. Pin it to the config's board.
export BOARD=nexys_video

# Files the framework touches (board sources + their hw/ root copies). Reset each
# to HEAD so the clone starts WITHOUT any automation seeding, exactly like a bare
# `git clone` of the tracked tree.
BOARD_FILES="Soc.bsv Soc.defines TbSoc.bsv constraints.xdc fpga_top.v mixed_cluster.bsv pinmux.bsv pwm_cluster.bsv spi_cluster.bsv uart_cluster.bsv DebugSoc.bsv"
ROOT_FILES="Soc.bsv Soc.defines TbSoc.bsv mixed_cluster.bsv pinmux.bsv pwm_cluster.bsv spi_cluster.bsv uart_cluster.bsv DebugSoc.bsv fpga_top.v constraints.xdc jtag_constraints.xdc bsvpath"

echo "### [1] Strip tracked board files to committed HEAD (simulate fresh clone)"
for f in $BOARD_FILES; do
  git --git-dir="$SRC_GIT" show "HEAD:hw/boards/nexys_video/$f" > "boards/nexys_video/$f" 2>/dev/null \
    && echo "    reset boards/nexys_video/$f" || echo "    (skip boards/nexys_video/$f - not in HEAD)"
done
for f in $ROOT_FILES; do
  git --git-dir="$SRC_GIT" show "HEAD:hw/$f" > "$f" 2>/dev/null \
    && echo "    reset $f" || echo "    (skip $f - not in HEAD)"
done
# A fresh clone has no captured baseline and no build tree.
rm -rf .automation_backup build soc_build_config.yaml.bak
# bsc needs its output dirs to exist (normally created by run_setup_build/soc_config,
# which we skip here to avoid its pip/repomanager/network side effects).
mkdir -p build/hw/intermediate build/hw/verilog bin
echo

run() { echo "### $*"; "$@" 2>&1 | tail -n "${TAIL:-4}"; echo "    -> exit ${PIPESTATUS[0]}"; echo; }

TAIL=6 run make bootstrap_board
TAIL=3 run make pre_validate_automation
echo "### seed hw/ root from seeded boards (mirrors run_setup_build's cp)"; cp -r boards/nexys_video/. . ; echo
TAIL=3 run make run_autointeg_bsv
TAIL=3 run make sync_bsv_patches
echo "### generate_verilog (bsc BSV->Verilog compile: the gold-standard check)"
make generate_verilog -j 2>&1 | tail -n 8; echo "    -> generate_verilog exit ${PIPESTATUS[0]}"; echo
TAIL=3 run make run_autointeg_verilog
TAIL=3 run make run_autointeg_xdc
TAIL=3 run make sync_automated_files
TAIL=3 run make post_validate_automation
TAIL=4 run make restore_autointeg_patches

echo "### FINAL: restore must produce a PRISTINE fresh clone (== git HEAD)"
pristine_ok=yes
for f in $BOARD_FILES; do
  if git --git-dir="$SRC_GIT" show "HEAD:hw/boards/nexys_video/$f" > /tmp/head_b_$f 2>/dev/null; then
    if diff -q /tmp/head_b_$f "boards/nexys_video/$f" >/dev/null 2>&1; then
      echo "  PRISTINE boards/$f"
    else
      echo "  LEFTOVER boards/$f  <-- NOT restored to fresh-clone state"; pristine_ok=no
    fi
  fi
done
# Root copies are build inputs that run_setup_build regenerates via
# `cp -r boards/<board>/. .`, so a restored root copy is pristine when it matches
# EITHER committed form -- the hw/ root version OR the boards/ version -- as long
# as it carries no automation content. Both are automation-free; which one it is
# is a pre-existing board-vs-root committed difference, not a restore failure.
for f in $ROOT_FILES; do
  [ "$f" = bsvpath ] && continue
  git --git-dir="$SRC_GIT" show "HEAD:hw/$f" > /tmp/head_r_$f 2>/dev/null || continue
  git --git-dir="$SRC_GIT" show "HEAD:hw/boards/nexys_video/$f" > /tmp/head_rb_$f 2>/dev/null
  if diff -q /tmp/head_r_$f "$f" >/dev/null 2>&1 || diff -q /tmp/head_rb_$f "$f" >/dev/null 2>&1; then
    echo "  PRISTINE root/$f"
  else
    echo "  LEFTOVER root/$f  <-- carries automation content after restore"; pristine_ok=no
  fi
done
echo "  RESTORE_IS_PRISTINE: $pristine_ok"
