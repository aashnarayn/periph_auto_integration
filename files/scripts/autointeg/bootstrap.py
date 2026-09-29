#!/usr/bin/env python3
"""Fresh-clone board bootstrap: seed the anchors a bare git clone lacks."""

import sys
import os
import re
import shutil
import json
import subprocess
import yaml
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Set, Any, Tuple
from collections import defaultdict

from .backup import load_manifest, save_manifest, snapshot_files
from .common import (
    log_pass, log_fail, log_warn, log_info, log_debug, set_verbose,
    load_yaml, resolve_template, find_unresolved_placeholders,
    ContextGenerator, IndentationAnalyzer, INDENT_PROFILE,
)


# The seed lives beside the executor at scripts/bootstrap/, not inside this
# package, so it stays where the setup Makefile puts it.
BOOTSTRAP_DIR = Path(__file__).resolve().parent.parent / "bootstrap"


def bootstrap_board_anchors(config: Dict, dry_run: bool = False) -> bool:
    """Inject the anchor comments / derived macros a fresh clone lacks.

    A bare `git clone` ships the board sources WITHOUT the automation anchors:
    the Section-21 derived-width macros in Soc.defines (`ExtIntWidth, `PLICWidth
    ...), the '(added by automation)' comment markers in fpga_top.v /
    mixed_cluster.bsv, and the one-signal-per-line interrupt / PLIC concats in
    Soc.bsv / mixed_cluster.bsv. Every IP def anchors on those, so a fresh
    checkout fails pre-validation with 'Anchor ... NOT found'. This step seeds
    them from a board-specific def so the tree reaches the exact functional
    pre-automation baseline the framework expects -- with zero manual editing.

    Each seed is expressed as literal (find -> replace) hunks lifted verbatim
    from the reference board sources, so seeding can never introduce an
    indentation error. Idempotent: a hunk is applied only while its `find` block
    is still on disk; a hunk whose `replace` block is already present is skipped.
    Safe to run before every pre-audit and integration.

    RESTORE CONTRACT: seeding is the FIRST thing to touch the tree, so before it
    overwrites a file it snapshots that file's PRISTINE (pre-seed) content into
    the backup manifest -- creating the baseline if none exists. That makes the
    baseline the true fresh-clone state, so `make restore_autointeg_patches`
    rewinds the seed as well as the integration patches and the tree returns to
    exactly what a bare `git clone` ships (no macros, no '(added by automation)'
    comments). The next pre-audit / integration simply re-seeds -- fully
    idempotent across restore cycles.

    Returns True on success (including the no-op 'already seeded' case).
    """
    board_dir = config.get('board_dir', 'boards/nexys_video')
    # Board-aware seed path. Each board owns a seed at
    #   scripts/bootstrap/<board>/bootstrap.yaml
    # discovered automatically from `target_board` (falling back to the last path
    # component of board_dir). Adding a new board is therefore just dropping in a
    # new scripts/bootstrap/<board>/bootstrap.yaml -- no code or config change.
    # `board_bootstrap:` in the config still works as an explicit override.
    board = config.get('target_board') or Path(board_dir).name
    seed_path = config.get('board_bootstrap') or str(BOOTSTRAP_DIR / board / "bootstrap.yaml")
    if not Path(seed_path).exists():
        log_warn(f"[BOOTSTRAP] Seed def '{seed_path}' not found for board '{board}'; "
                 f"skipping board bootstrap (add scripts/bootstrap/{board}/bootstrap.yaml).")
        return True

    seed_def = load_yaml(seed_path)
    # Snapshot pristine files into THIS manifest before overwriting them, so a
    # later --restore rewinds the seed too. Merged (not clobbered) so it composes
    # with the snapshots the integration phases add.
    manifest = load_manifest()
    manifest_dirty = False
    total_applied = 0
    for seed in seed_def.get('seeds', []):
        filepath = resolve_template(seed['file'], {'board_dir': board_dir})
        if not Path(filepath).exists():
            log_warn(f"[BOOTSTRAP] Target '{filepath}' not found; skipping.")
            continue
        content = Path(filepath).read_text()
        original = content
        applied = 0
        for hunk in seed.get('hunks', []):
            find = hunk.get('find', '')
            repl = hunk.get('replace', '')
            # Only ever inject pristine -> seeded. If the pristine `find` block is
            # not on disk, the hunk is already applied (or the region has since
            # been mutated by integration, which rewrites the seeded macros/
            # concats) -- either way there is nothing to seed, so skip it. This
            # keeps the pass a safe no-op on an already-seeded OR already-patched
            # tree, so it can run unconditionally before every phase and audit.
            if find and find in content:
                content = content.replace(find, repl, 1)
                applied += 1
        if content != original:
            total_applied += applied
            if dry_run:
                log_info(f"[BOOTSTRAP] Would seed {applied} anchor hunk(s) into {filepath}")
            else:
                # Capture the PRISTINE file (still on disk, not yet overwritten)
                # so --restore can rewind the seed. Idempotent: snapshot_files
                # skips a path already recorded in the manifest.
                if snapshot_files([filepath], manifest):
                    manifest_dirty = True
                # The hw/ root copy of this file (a build input seeded from
                # boards/ by run_setup_build) is still pristine when bootstrap
                # runs first, so snapshot it now too. run_setup_build later
                # overwrites it with the SEEDED board file; without this, its
                # backup would capture the seeded form and --restore would leave
                # the root copy non-pristine.
                root_copy = Path(filepath).name
                if root_copy != filepath and Path(root_copy).exists():
                    if snapshot_files([root_copy], manifest):
                        manifest_dirty = True
                Path(filepath).write_text(content)
                log_pass(f"[BOOTSTRAP] Seeded {applied} anchor hunk(s) into {filepath}")
        else:
            log_debug(f"[BOOTSTRAP] {filepath}: anchors already present; no changes.")

    if manifest_dirty:
        save_manifest(manifest)
    if total_applied == 0:
        log_info("[BOOTSTRAP] Board anchors already present; nothing to seed.")
    return True


