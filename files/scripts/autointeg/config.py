#!/usr/bin/env python3
"""soc_build_config.yaml handling: def_path/mode resolution and YAML migration."""

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
from .registry import CONTEXT_GENERATORS


def resolve_def_path(peripheral: Dict, config: Dict) -> str:
    """
    Resolve a peripheral's IP-definition path, honoring an optional mode switch.

    A peripheral entry may carry a `modes` map (mode-value -> def_path) plus a
    mode value chosen inside its `instances`. The mode value is discovered
    without a separate declaration: it is simply the instance field whose value
    names one of the `modes` (e.g. `uart3_mode: with_pinmux`, where
    `with_pinmux` is a key of `modes`). All instances that name a mode must
    agree, since a peripheral has one def_path. This lets a single peripheral
    ship several interchangeable integrations (e.g. UART3 with pinmux vs.
    routed to dedicated pins) chosen by data alone -- no Python per variant,
    and no redundant "which field is the selector" declaration.

    (An explicit `mode_selector: <field>` is still honored if present, for a
    project-wide/top-level default; but it is not required.)

    Entries with no `modes` map fall back to their plain `def_path`, so
    single-variant peripherals (rtc_v1, gpio_v1, …) are unaffected.

    Raises:
        KeyError: if the mode is named nowhere, instances name conflicting
            modes, or the named mode is not one of the declared modes.
    """
    modes = peripheral.get('modes')
    selector = peripheral.get('mode_selector')
    name = peripheral.get('name', '?')

    if not modes:
        if selector:
            raise KeyError(
                f"Peripheral '{name}': `mode_selector` set without a `modes` map"
            )
        return peripheral['def_path']

    mode = resolve_active_mode(peripheral, config)
    if mode is None:
        raise KeyError(
            f"Peripheral '{name}': no mode named in any instance "
            f"(expected an instance field set to one of {list(modes)})"
        )
    return modes[mode]


def resolve_active_mode(peripheral: Dict, config: Dict) -> Optional[str]:
    """Discover the selected routing mode for a peripheral, or None if it declares no `modes`.

    Mirrors the mode discovery in `resolve_def_path` so that patch-level `mode:` gates
    (used by unified multi-mode defs such as uart_v1, which now carries BOTH the
    with-pinmux and without-pinmux patches in one file) always agree with which def_path
    was loaded. A unified def declares its modes with both keys pointing at the same file.
    """
    modes = peripheral.get('modes')
    if not modes:
        return None
    selector = peripheral.get('mode_selector')
    instances = peripheral.get('instances', [])
    name = peripheral.get('name', '?')

    if selector:
        # Explicit field name: read it from the instances, else top-level config.
        named = {inst[selector] for inst in instances if selector in inst}
    else:
        # Implicit: any instance field whose VALUE names one of the modes.
        named = {
            v for inst in instances for v in inst.values()
            if isinstance(v, str) and v in modes
        }

    if len(named) > 1:
        raise KeyError(
            f"Peripheral '{name}': instances name conflicting modes "
            f"({sorted(named)}) — a peripheral has a single routing mode"
        )
    mode = named.pop() if named else (config.get(selector) if selector else None)
    if mode is not None and mode not in modes:
        raise KeyError(
            f"Peripheral '{name}': '{mode}' is not a known mode "
            f"(expected one of {list(modes)})"
        )
    return mode


def check_migration_needed(config_path: str, peripheral_name: Optional[str] = None) -> bool:
    """
    Check if soc_build_config.yaml requires migration (V1→V2 cleanup).
    
    Args:
        config_path: Path to the YAML config file.
        peripheral_name: Optional: check only this peripheral.
        
    Returns:
        True if migration is needed, False otherwise.
    """
    log_info(f"[MIGRATION] Checking if '{config_path}' requires migration")
    
    config = load_yaml(config_path)
    if 'automated_peripherals' not in config:
        log_info("[MIGRATION] No 'automated_peripherals' section found. Migration not needed.")
        return False
    
    for p in config['automated_peripherals']:
        p_name = p.get('name')
        if peripheral_name and p_name != peripheral_name:
            continue
            
        generator = CONTEXT_GENERATORS.get(p_name)
        if not generator:
            continue
            
        needs_it = any(
            generator.needs_migration(inst) 
            for inst in p.get('instances', [])
        )
        if needs_it:
            log_info(f"[MIGRATION] Found auto-generated keys in '{p_name}' that can be stripped.")
            return True
    
    log_info("[MIGRATION] All peripherals are already minimal. No migration needed.")
    return False


def migrate_soc_config(
    input_path: str, 
    output_path: Optional[str] = None, 
    peripheral_name: Optional[str] = None, 
    dry_run: bool = False
) -> bool:
    """
    Strip auto-generated naming keys from soc_build_config.yaml (V1→V2 migration).
    
    Args:
        input_path: Path to input YAML config.
        output_path: Optional: path for migrated output (defaults to input_path).
        peripheral_name: Optional: migrate only this peripheral.
        dry_run: If True, preview changes without writing.
        
    Returns:
        True if successful, False otherwise.
    """
    log_info(f"[MIGRATION] Starting migration for '{input_path}' (Dry-Run: {dry_run})")
    
    input_file = Path(input_path)
    if not input_file.exists():
        log_fail(f"[MIGRATION] Config file not found: {input_path}")
        return False
        
    config = load_yaml(input_path)
    if 'automated_peripherals' not in config:
        log_warn("[MIGRATION] No peripherals found in config. Skipping migration.")
        return True
    
    migrated = False
    for p in config['automated_peripherals']:
        p_name = p.get('name')
        log_info(f"[MIGRATION] Processing peripheral: '{p_name}'")
        
        if peripheral_name and p_name != peripheral_name:
            continue
            
        generator = CONTEXT_GENERATORS.get(p_name)
        if not generator:
            log_warn(f"[MIGRATION] No generator found for '{p_name}'. Skipping.")
            continue
            
        for inst in p.get('instances', []):
            inst_id = inst.get('instance_id', '?')
            for key in generator.get_generated_keys():
                if key in inst:
                    if dry_run:
                        log_info(f"[MIGRATION] [DRY-RUN] Would remove '{key}' from {p_name} instance {inst_id}")
                    else:
                        del inst[key]
                        migrated = True
                        log_info(f"[MIGRATION] Removed '{key}' from {p_name} instance {inst_id}")
    
    if dry_run:
        if migrated:
            log_info("[MIGRATION] Dry-run complete. Review changes and run without --dry-run to apply.")
        else:
            log_info("[MIGRATION] No changes needed. Config is already minimal.")
        return True
    
    # Write migrated config
    output_file = Path(output_path) if output_path else input_file
    if output_file == input_file:
        backup = input_file.with_suffix(input_file.suffix + ".bak")
        shutil.copy2(input_file, backup)
        log_info(f"[MIGRATION] Original config backed up to {backup}")
    
    log_info(f"[MIGRATION] Writing migrated config to {output_file}")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Preserve header comment
    header_comment = (
        "# Centralized config file for Peripheral Automation\n"
        "# Add new peripherals to the `automated_peripherals` list without modifying Python code.\n"
        "# Location: gc2025/hw/soc_build_config.yaml\n\n"
    )
    
    with open(output_file, 'w') as f:
        f.write(header_comment)
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    log_pass(f"[MIGRATION] Migration successful. Output saved to {output_file}")
    return True


# =============================================================================
# RESOURCE TRACKER
# =============================================================================

