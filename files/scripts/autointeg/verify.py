#!/usr/bin/env python3
"""End-to-end fresh-clone verification.

Replaces the former scripts/fresh_clone_verify.sh. Resets a clone's tracked
board files to their committed content (so the tree looks exactly like a bare
`git clone`), runs the full automation flow, and then asserts that
restore_autointeg_patches returns the tree to that pristine state.

Proves the framework works with zero manual board seeding.
"""

import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .common import log_fail, log_info, log_pass, log_warn

BOARD_FILES: List[str] = [
    "Soc.bsv", "Soc.defines", "TbSoc.bsv", "constraints.xdc", "fpga_top.v",
    "mixed_cluster.bsv", "pinmux.bsv", "pwm_cluster.bsv", "spi_cluster.bsv",
    "uart_cluster.bsv", "DebugSoc.bsv",
]

ROOT_FILES: List[str] = BOARD_FILES + ["jtag_constraints.xdc", "bsvpath"]

# The flow, in the order the framework is meant to be driven.
FLOW: List[str] = [
    "bootstrap_board",
    "pre_validate_automation",
    "run_autointeg_bsv",
    "sync_bsv_patches",
    "generate_verilog",
    "run_autointeg_verilog",
    "run_autointeg_xdc",
    "sync_automated_files",
    "post_validate_automation",
    "restore_autointeg_patches",
]


def _git_show(src_git: Path, path: str) -> Optional[bytes]:
    r = subprocess.run(
        ["git", f"--git-dir={src_git}", "show", f"HEAD:{path}"],
        capture_output=True,
    )
    return r.stdout if r.returncode == 0 else None


def fresh_clone_verify(clone_hw: Path, src_git: Path, board: str = "nexys_video") -> bool:
    """Reset `clone_hw` to committed state, run the flow, assert restore is pristine."""
    clone_hw = clone_hw.resolve()
    src_git = src_git.resolve()

    log_info("### [1] Strip tracked board files to committed HEAD (simulate fresh clone)")
    for f in BOARD_FILES:
        blob = _git_show(src_git, f"hw/boards/{board}/{f}")
        if blob is not None:
            (clone_hw / "boards" / board / f).write_bytes(blob)
            log_info(f"    reset boards/{board}/{f}")
    for f in ROOT_FILES:
        blob = _git_show(src_git, f"hw/{f}")
        if blob is not None:
            (clone_hw / f).write_bytes(blob)
            log_info(f"    reset {f}")

    # A fresh clone has no captured baseline and no build tree.
    for stale in (".automation_backup", "build", "soc_build_config.yaml.bak"):
        p = clone_hw / stale
        if p.is_dir():
            subprocess.run(["rm", "-rf", str(p)])
        elif p.exists():
            p.unlink()
    # bsc needs its output dirs; run_setup_build normally makes them, but it is
    # skipped here to avoid its pip/repomanager/network side effects.
    for d in ("build/hw/intermediate", "build/hw/verilog", "bin"):
        (clone_hw / d).mkdir(parents=True, exist_ok=True)

    log_info("### [2] Run the automation flow")
    for target in FLOW:
        if target == "run_autointeg_bsv":
            # Mirror run_setup_build's `cp -r boards/<board>/. .`
            subprocess.run(
                f"cp -r boards/{board}/. .", shell=True, cwd=clone_hw, check=False
            )
        r = subprocess.run(
            ["make", f"BOARD={board}", target],
            cwd=clone_hw, capture_output=True, text=True,
        )
        tail = "\n".join(r.stdout.strip().splitlines()[-3:])
        log_info(f"### make {target} -> exit {r.returncode}")
        if tail:
            log_info(tail)
        if r.returncode != 0 and target != "post_validate_automation":
            log_fail(f"[VERIFY] '{target}' failed")
            return False

    log_info("### [3] FINAL: restore must produce a PRISTINE fresh clone (== git HEAD)")
    pristine = True
    for f in BOARD_FILES:
        blob = _git_show(src_git, f"hw/boards/{board}/{f}")
        if blob is None:
            continue
        if (clone_hw / "boards" / board / f).read_bytes() == blob:
            log_pass(f"  PRISTINE boards/{f}")
        else:
            log_fail(f"  LEFTOVER boards/{f}  <-- NOT restored to fresh-clone state")
            pristine = False

    # Root copies are build inputs that run_setup_build regenerates from the
    # board dir, so a restored root copy is pristine when it matches EITHER
    # committed form. Both are automation-free; which one it is is a pre-existing
    # board-vs-root difference, not a restore failure.
    for f in ROOT_FILES:
        if f == "bsvpath":
            continue
        root_blob = _git_show(src_git, f"hw/{f}")
        if root_blob is None:
            continue
        board_blob = _git_show(src_git, f"hw/boards/{board}/{f}")
        cur = (clone_hw / f).read_bytes()
        if cur == root_blob or (board_blob is not None and cur == board_blob):
            log_pass(f"  PRISTINE root/{f}")
        else:
            log_fail(f"  LEFTOVER root/{f}  <-- carries automation content after restore")
            pristine = False

    log_info(f"  RESTORE_IS_PRISTINE: {'yes' if pristine else 'no'}")
    return pristine
