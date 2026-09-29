#!/usr/bin/env python3
"""Independent pre/post integration audits."""

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
from .tracker import ResourceTracker
from .patchmodel import (
    build_instance_context, classify_patch_phase, gate_patch, strip_comments,
)
from .config import resolve_def_path, resolve_active_mode
from .conditional import resolve_ifdef_criteria
from .handlers import HANDLERS


def audit_anchors(
    vfs: Dict[str, str],
    anchors: Dict,
    base_ctx: Dict,
    patch_type: Optional[str] = None,
    skip_check: Optional[str] = None,
    skip_check_rx: Optional[str] = None
) -> bool:
    """
    Verify that all anchor patterns exist in the VFS.

    Args:
        vfs: Virtual file system (path → content).
        anchors: Dictionary of anchor definitions.
        base_ctx: Base context for template resolution.
        patch_type: Optional: type of patch (e.g., 'xdc_pin_assign').
        skip_check: Optional: literal to skip validation if present (idempotency,
            from skip_if_contains).
        skip_check_rx: Optional: regex to skip validation if it matches
            (idempotency, from skip_if_matches). A replace-type patch consumes
            its own anchor, so on an already-patched tree only this guard
            proves the patch landed.

    Returns:
        True if all anchors found, False otherwise.
    """
    if patch_type == 'xdc_pin_assign':
        # XDC anchors are structural; validated by handler
        return True
    
    all_passed = True
    for anchor_name, anchor_def in anchors.items():
        filepath = resolve_template(anchor_def['file'], base_ctx)
        
        if filepath not in vfs:
            log_fail(f"[AUDIT] File missing from VFS: {filepath}")
            all_passed = False
            continue
        
        content = vfs[filepath]
        
        # Idempotency check: skip if already applied
        if skip_check and re.search(re.escape(skip_check), content):
            continue
        if skip_check_rx and re.search(skip_check_rx, content, re.MULTILINE | re.DOTALL):
            continue
        
        pattern = anchor_def.get('pattern')
        if pattern:
            try:
                if re.search(pattern, content, re.MULTILINE | re.DOTALL):
                    log_pass(f"[AUDIT] Anchor '{pattern[:40]}...' found in {filepath}")
                else:
                    log_fail(f"[AUDIT] Anchor '{pattern[:40]}...' NOT found in {filepath}")
                    all_passed = False
            except re.error as e:
                log_fail(f"[AUDIT] Invalid regex pattern in {filepath}: {e}")
                all_passed = False
    
    return all_passed


def run_pre_audit(config: Dict, vfs: Dict[str, str], rt: ResourceTracker) -> bool:
    """
    Run pre-integration validation: anchor existence + resource conflicts.
    
    Args:
        config: Loaded soc_build_config.yaml.
        vfs: Virtual file system.
        rt: ResourceTracker instance.
        
    Returns:
        True if all checks pass, False otherwise.
    """
    board_dir = config.get('board_dir', 'boards/nexys_video')
    peripherals = config.get('automated_peripherals', [])
    all_passed = True
    
    for p in peripherals:
        base_ctx = {**p.get('base_context', {}), 'board_dir': board_dir}
        _crit = resolve_ifdef_criteria(p)
        if _crit:
            base_ctx['__ifdef__'] = _crit
        peripheral_yaml = load_yaml(resolve_def_path(p, config))
        anchors = peripheral_yaml.get('anchors', {})
        patches = peripheral_yaml.get('patches', [])
        instances = p.get('instances', [{}])
        peripheral_name = p['name']
        active_mode = resolve_active_mode(p, config)

        log_info(f"[AUDIT] Starting Pre-Audit: {peripheral_name} ({len(instances)} instances, {len(patches)} patches)")

        for patch in patches:
            anchor_ref = patch.get('anchor_ref')
            anchor = anchors.get(anchor_ref, {})
            patch_type = patch.get('type')
            code = patch.get('code', '')
            skip_template = patch.get('skip_if_contains')
            skip_rx_template = patch.get('skip_if_matches')
            skip_check = None
            skip_check_rx = None

            # Same gates as apply: don't validate anchors / placeholders for
            # patches that will not run (inactive routing mode, or every instance
            # connect_to_plic: false). Anchors of the inactive mode still exist in
            # the pristine tree, so this only avoids spurious 'unresolved
            # placeholder' failures on mode-specific instance fields.
            gated_instances = gate_patch(patch, active_mode, instances, peripheral_yaml.get('default_mode'))
            if gated_instances is None:
                continue

            # Resolve skip_check templates once per patch
            for inst in gated_instances:
                ctx = build_instance_context(base_ctx, inst, peripheral_name)
                # calculated_val and allocated_slot are filled by the
                # cluster_config handler at apply time, not from instance context.
                unresolved = [
                    k for k in find_unresolved_placeholders(code, ctx)
                    if k not in ('calculated_val', 'allocated_slot')
                ]
                if unresolved:
                    log_fail(f"[AUDIT] Instance {ctx.get('instance_id', '?')}: unresolved placeholders {unresolved}")
                    all_passed = False
                if skip_template and not skip_check:
                    skip_check = resolve_template(skip_template, ctx)
                if skip_rx_template and not skip_check_rx:
                    skip_check_rx = resolve_template(skip_rx_template, ctx)

            if not audit_anchors(vfs, {anchor_ref: anchor}, base_ctx, patch_type, skip_check, skip_check_rx):
                all_passed = False
    
    log_info("[AUDIT] Running resource conflict checks")
    if not rt.audit_instances(peripherals):
        all_passed = False
    
    return all_passed


def run_post_audit(
    config: Dict, vfs: Dict[str, str], rt: ResourceTracker, phase: str = 'all'
) -> bool:
    """
    Run post-integration validation: verify patches applied correctly.

    Args:
        config: Loaded soc_build_config.yaml.
        vfs: Virtual file system (post-patch content).
        rt: ResourceTracker instance.
        phase: Which phases were actually applied, so only those are audited.
            'all' (default) audits everything, matching the FPGA flow. The
            simulation flow applies the bsvpath and BSV phases only -- the sim
            top instantiates mkSoc directly and never reaches fpga_top.v or the
            XDC -- so auditing those would fail on patches that were correctly
            never applied. Mirrors the phase filter in execute_integration().

    Returns:
        True if all checks pass, False otherwise.
    """
    board_dir = config.get('board_dir', 'boards/nexys_video')
    peripherals = config.get('automated_peripherals', [])
    all_passed = True
    
    for p in peripherals:
        base_ctx = {**p.get('base_context', {}), 'board_dir': board_dir}
        _crit = resolve_ifdef_criteria(p)
        if _crit:
            base_ctx['__ifdef__'] = _crit
        peripheral_name = p['name']
        active_mode = resolve_active_mode(p, config)
        expanded_instances = [
            build_instance_context(base_ctx, inst, peripheral_name)
            for inst in p.get('instances', [{}])
        ]
        peripheral_yaml = load_yaml(resolve_def_path(p, config))
        anchors = peripheral_yaml.get('anchors', {})
        patches = peripheral_yaml.get('patches', [])

        log_info(f"[AUDIT] Starting Post-Audit: {peripheral_name} ({len(expanded_instances)} instances, {len(patches)} patches)")

        for patch in patches:
            anchor = anchors.get(patch.get('anchor_ref', ''), {})
            if not anchor:
                continue

            # Only audit the phases that were applied (see `phase` in the
            # docstring). Same classification the engine uses.
            if phase != 'all':
                patch_phase = classify_patch_phase(patch, anchors)
                if phase == 'bsvpath':
                    if patch_phase != 'bsvpath':
                        continue
                elif phase == 'bsv':
                    if patch_phase not in ('bsv', 'bsvpath'):
                        continue
                elif patch_phase != phase:
                    continue

            # Same gates as apply: don't audit patches that were never applied
            # (inactive routing mode, or all instances connect_to_plic: false).
            gated_instances = gate_patch(patch, active_mode, expanded_instances, peripheral_yaml.get('default_mode'))
            if gated_instances is None:
                continue

            handler = HANDLERS.get(patch.get('type', 'regex'))
            if not handler or not handler.audit_post(
                vfs, anchor, patch, base_ctx, gated_instances, rt.shared_state, rt
            ):
                all_passed = False

    return all_passed


# =============================================================================
# PATCH HANDLERS (OOP Registry Pattern)
# =============================================================================

