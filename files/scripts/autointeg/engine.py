#!/usr/bin/env python3
"""The integration executor: VFS build, phased patch application, atomic commit."""

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

from .common import (
    log_pass, log_fail, log_warn, log_info, log_debug, set_verbose,
    load_yaml, resolve_template, find_unresolved_placeholders,
    ContextGenerator, IndentationAnalyzer, INDENT_PROFILE,
)
from .backup import BACKUP_DIR, load_manifest, save_manifest, snapshot_files
from .bootstrap import bootstrap_board_anchors
from .conditional import resolve_ifdef_criteria
from .config import resolve_def_path, resolve_active_mode
from .handlers import HANDLERS
from .patchmodel import build_instance_context, classify_patch_phase, gate_patch
from .tracker import ResourceTracker


def execute_integration(
    config: Dict,
    dry_run: bool = False,
    phase: str = 'all'
) -> bool:
    """
    Apply patches to target files via VFS. Does NOT run audits.
    
    Audits are run separately via --pre-audit / --post-audit CLI flags.
    
    Args:
        config: Loaded soc_build_config.yaml.
        dry_run: If True, simulate without writing to disk.
        phase: Execution phase: 'all', 'bsvpath', 'bsv', 'verilog', 'xdc'.
        
    Returns:
        True if all patches applied successfully, False otherwise.
    """
    board_dir = config.get('board_dir', 'boards/nexys_video')
    peripherals = config.get('automated_peripherals', [])
    
    if not peripherals:
        log_info("[ENGINE] No peripherals defined in BOM. Exiting.")
        return True

    # Seed the anchors a fresh clone lacks BEFORE loading the VFS, so every IP's
    # anchors resolve and the baseline captured below records the seeded (i.e.
    # functional pre-automation) state -- exactly what a --restore should rewind
    # to. Idempotent, so this is a no-op once the tree is already seeded.
    if not bootstrap_board_anchors(config, dry_run):
        log_fail("[ENGINE] Board bootstrap failed. Aborting.")
        return False

    # Initialize resource tracker and VFS
    rt = ResourceTracker()
    pin_map_path = config.get('pin_map_path', f'{board_dir}/pin_map.yaml')
    master_xdc = config.get('master_xdc_path', f'{board_dir}/master_constraints.xdc')
    active_xdc = config.get('active_xdc_path', f'{board_dir}/constraints.xdc')
    
    # Load target files into VFS
    log_info(f"[VFS] Loading target files into memory")
    vfs: Dict[str, str] = {}
    target_files: Set[str] = set()
    
    for p in peripherals:
        ctx = {**p.get('base_context', {}), 'board_dir': board_dir}
        peripheral_yaml = load_yaml(resolve_def_path(p, config))
        for anchor in peripheral_yaml.get('anchors', {}).values():
            filepath = resolve_template(anchor['file'], ctx)
            if Path(filepath).exists():
                target_files.add(filepath)
                vfs[filepath] = Path(filepath).read_text()
    
    log_info(f"[VFS] Loaded {len(vfs)} file(s) into memory")

    # Snapshot of exactly what is on disk. The commit-time baseline guard diffs
    # the patched VFS against this to tell "pristine tree" from "already patched".
    pristine_vfs = dict(vfs)

    # Ingest static state for resource tracking
    rt.ingest_static_state(vfs, master_xdc, active_xdc)
    
    log_info(f"[ENGINE] Phase: {phase.upper()} | Peripherals: {len(peripherals)}")
    if dry_run:
        log_info("[ENGINE] DRY RUN ACTIVE: No files will be modified on disk")
    
    # Apply patches
    success = True
    for p in peripherals:
        base_ctx = {**p.get('base_context', {}), 'board_dir': board_dir}
        ifdef_criteria = resolve_ifdef_criteria(p)
        if ifdef_criteria:
            base_ctx['__ifdef__'] = ifdef_criteria
            log_info(f"[IFDEF] '{p['name']}' gated on `ifdef {ifdef_criteria}")
        peripheral_name = p['name']
        peripheral_yaml = load_yaml(resolve_def_path(p, config))
        anchors = peripheral_yaml.get('anchors', {})
        active_mode = resolve_active_mode(p, config)
        expanded_instances = [
            build_instance_context(base_ctx, inst, peripheral_name)
            for inst in p.get('instances', [{}])
        ]

        for patch in peripheral_yaml.get('patches', []):
            patch_phase = classify_patch_phase(patch, anchors)

            # Phase filtering. 'bsv' also carries the bsvpath patches so that a
            # standalone `make run_autointeg_bsv` still registers the IP's
            # include path; 'bsvpath' carries ONLY them, so `make update_bsvpath`
            # cannot mutate Soc.defines (which silently drifted the cluster
            # slave counters on every build).
            if phase == 'bsvpath':
                if patch_phase != 'bsvpath':
                    continue
            elif phase == 'bsv':
                if patch_phase not in ('bsv', 'bsvpath'):
                    continue
            elif phase != 'all' and patch_phase != phase:
                continue

            anchor = anchors.get(patch.get('anchor_ref'), {})
            if not anchor:
                continue

            # Mode + connect_to_plic gates (see gate_patch). None => skip patch.
            gated_instances = gate_patch(patch, active_mode, expanded_instances, peripheral_yaml.get('default_mode'))
            if gated_instances is None:
                continue

            handler = HANDLERS.get(patch.get('type', 'regex'))
            if not handler:
                log_fail(f"[ENGINE] Unknown handler type: {patch.get('type')}")
                success = False
                break

            if not handler.apply(
                vfs, anchor, patch, base_ctx, gated_instances,
                dry_run, rt.shared_state, rt
            ):
                success = False
                break
        
        if not success:
            break
    
    if not success:
        log_fail("[ENGINE] Patch application failed. Aborting.")
        return False
    
    # Atomic commit (if not dry-run)
    if success and not dry_run:
        log_info("[COMMIT] Creating atomic backup & flushing VFS to disk")

        changed = [f for f, content in vfs.items() if content != pristine_vfs.get(f)]
        fresh_baseline = not (BACKUP_DIR / "manifest.json").exists()

        # A fresh backup defines the "pristine" baseline that every future
        # --restore rewinds to. If this run has nothing to apply, the tree is
        # already patched, and snapshotting it now would record the PATCHED
        # files as "pristine" — permanently, since snapshot_files() never
        # re-snapshots a tracked file. Every later --restore would be a no-op.
        if fresh_baseline and not changed:
            if os.environ.get("AUTOINTEG_FORCE_BASELINE") == "1":
                log_warn("[COMMIT] Tree is already fully patched; baselining anyway "
                         "(AUTOINTEG_FORCE_BASELINE=1)")
            else:
                log_fail("[COMMIT] Refusing to create a backup baseline from an already-patched tree.")
                log_fail("[COMMIT] No patch in this run had anything left to apply, and")
                log_fail("[COMMIT] .automation_backup/ is missing — so this run would record the")
                log_fail("[COMMIT] PATCHED files as the pristine baseline, making every later")
                log_fail("[COMMIT] 'make restore_autointeg_patches' a silent no-op.")
                log_info("[COMMIT] Recover the pre-automation sources first (e.g. 'git checkout -- boards/'),")
                log_info("[COMMIT] or set AUTOINTEG_FORCE_BASELINE=1 if this state is intentional.")
                return False

        # Merge with the existing manifest instead of clobbering it, so multi-phase
        # runs (--bsvpath, --bsv, --verilog, --xdc) remain fully restorable.
        manifest = load_manifest()
        snapshot_files(sorted(target_files), manifest)
        save_manifest(manifest)

        # Flush VFS to disk
        for filepath, content in vfs.items():
            Path(filepath).write_text(content)

        # Export updated pin map with enhanced metadata
        rt.analyzer.export_pin_map(
            pin_map_path,
            master_xdc_path=master_xdc,
            active_xdc_path=active_xdc,
            board_name=config.get('target_board', board_dir.split('/')[-1]),
            fpga_part=config.get('fpga_part', '')
        )
        
        log_pass("[COMMIT] Atomic commit complete. All files updated successfully.")
    elif dry_run:
        log_info("[ENGINE] Dry-run simulation finished. No disk writes performed.")
    
    return success


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

