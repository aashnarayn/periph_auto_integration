#!/usr/bin/env python3
"""
Peripheral Auto-Integrator V2 for Shakti GC2025

Single entry-point for integration, auditing, pin-tracking, and YAML migration.
Production-ready: concise logging by default, verbose with --verbose flag.

Audit Separation:
- --pre-audit / --post-audit: Run audits ONLY (no patch application)
- Default execution: Apply patches ONLY (no auto-audit)
- Audits are run via separate Makefile targets: pre_validate_automation, post_validate_automation
"""

# =============================================================================
# IMPORTS
# =============================================================================

# Standard library
import sys
import os
import re
import shutil
import json
import argparse
import yaml 
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Set, Any, Tuple
from collections import defaultdict

# Project modules
from xdc_analyzer import BoardSupportAnalyzer
from peripheral_registry import CONTEXT_GENERATORS
from utils import (
    # Logging
    log_pass, log_fail, log_warn, log_info, log_debug, set_verbose,
    # YAML & templates
    load_yaml, resolve_template, find_unresolved_placeholders,
    # Base classes
    ContextGenerator, IndentationAnalyzer,
)

# =============================================================================
# MODULE-LEVEL STATE
# =============================================================================

# No global state needed — all state passed explicitly or via utils.set_verbose()

# =============================================================================
# DOMAIN-SPECIFIC HELPERS (Not moved to utils.py — Shakti-specific)
# =============================================================================

def extract_define_value(content: str, macro_name: str) -> Optional[int]:
    """
    Extract integer value from a Verilog/BSV macro definition.
    
    Handles decimal, hex ('h/0x), and underscores (e.g., 1_024 or 0004_06FF).
    
    Args:
        content: File content containing macro definitions.
        macro_name: Name of the macro to extract (without backtick).
        
    Returns:
        Integer value of the macro, or None if not found/invalid.
    """
    match = re.search(
        rf"^`define\s+{re.escape(macro_name)}\s+('h|0x)?([0-9a-fA-F_]+)", 
        content, 
        re.MULTILINE
    )
    if match:
        prefix = match.group(1)
        val_str = match.group(2).replace('_', '')
        if not val_str:
            return None
        if prefix:  # Explicitly marked as hex
            try:
                return int(val_str, 16)
            except ValueError:
                return None
        else:  # Default to decimal (standard for counters like Num_Slaves)
            try:
                return int(val_str, 10)
            except ValueError:
                # Fallback to hex only if decimal fails (handles legacy mixed-syntax)
                try:
                    return int(val_str, 16)
                except ValueError:
                    return None
    return None


def build_instance_context(base_ctx: Dict, instance: Dict, peripheral_name: str) -> Dict:
    """
    Build merged context: base + generated + instance.
    
    Priority: instance > generated > base_ctx
    
    Args:
        base_ctx: Shared context from soc_build_config.yaml base_context.
        instance: Per-instance overrides from instances[] list.
        peripheral_name: Name of the peripheral (for context generator lookup).
        
    Returns:
        Merged context dictionary.
    """
    log_debug(f"[CONTEXT] Building merged context for '{peripheral_name}' instance '{instance.get('instance_id', '0')}'")
    
    generator = CONTEXT_GENERATORS.get(peripheral_name)
    generated_ctx = generator.generate(base_ctx, instance, peripheral_name) if generator else {}
    
    merged = {**base_ctx, **generated_ctx, **instance}
    log_debug(f"[CONTEXT] Merged keys: {list(merged.keys())}")

    return merged


def classify_patch_phase(patch: Dict, anchors: Dict) -> str:
    """
    Classify a patch into the build phase that owns it, from its target file.

    Classification is by TARGET FILE, never by the patch's position in the YAML.
    An earlier revision keyed the bsvpath phase off "is this the first anchor in
    the file", which only held for rtc_v1 (whose first anchor happens to be the
    bsvpath one) and silently let `--bsvpath` re-apply every other IP's first
    Soc.defines patch on each invocation.

    Returns one of: 'bsvpath', 'xdc', 'verilog', 'bsv'.
    """
    if patch.get('type') == 'xdc_pin_assign':
        return 'xdc'

    anchor_file = anchors.get(patch.get('anchor_ref', ''), {}).get('file', '')

    if Path(anchor_file).name == 'bsvpath':
        return 'bsvpath'
    if 'fpga_top' in anchor_file:
        return 'verilog'
    return 'bsv'


def strip_comments(content: str, filepath: str = "") -> str:
    """
    Blank out comments while preserving length and line structure.

    Used ONLY for presence tests (skip guards, audits) -- never for the content
    that gets written back. Commented-out code is not "present": treating it as
    present made sspi_v1 skip SPI2's address-decoder arm entirely (see
    `_already_present`). Offsets are preserved so a caller may still map a match
    position back onto the original text.

    `//` and `/* ... */` are stripped everywhere. `#` is stripped ONLY for .xdc:
    in BSV it is the type-parameter sigil (`Bit#(13)`, `Ifc_pwm_axi4lite#(...)`),
    not a comment, and blanking it would gut the very text the guards match.

    String literals are not special-cased: no guard in these IP defs needs to
    match inside a quoted string, and blanking one only makes a guard MORE
    conservative (it re-applies an idempotent patch) rather than silently
    skipping a real one.
    """
    def blank(m: "re.Match") -> str:
        # Keep newlines so line numbers and offsets are unchanged.
        return "".join("\n" if ch == "\n" else " " for ch in m.group(0))

    content = re.sub(r"/\*[\s\S]*?\*/", blank, content)   # block comments
    content = re.sub(r"//[^\n]*", blank, content)         # line comments
    if filepath.endswith(".xdc"):
        content = re.sub(r"#[^\n]*", blank, content)      # XDC comments only
    return content


BACKUP_DIR = Path(".automation_backup")

# Board-bootstrap seed root. Lives alongside this engine (scripts/bootstrap/) so a
# board's anchor-seed def ships with the framework, not the IP defs. Resolved from
# __file__ so it works regardless of the current working directory. Per-board seed:
#   scripts/bootstrap/<board>/bootstrap.yaml
BOOTSTRAP_DIR = Path(__file__).resolve().parent / "bootstrap"


def load_manifest() -> Dict[str, List[str]]:
    """Read the backup manifest, or an empty one if no backup exists yet."""
    manifest_path = BACKUP_DIR / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    return {"modified": [], "created": []}


def save_manifest(manifest: Dict[str, List[str]]) -> None:
    BACKUP_DIR.mkdir(exist_ok=True, parents=True)
    (BACKUP_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))


def snapshot_files(filepaths: Any, manifest: Dict[str, List[str]]) -> int:
    """
    Copy each not-yet-tracked file into the backup tree and record it.

    Files already named in the manifest are left alone: the manifest must keep
    the ORIGINAL pre-automation content, so a later phase must never overwrite
    an earlier phase's snapshot with its own (already patched) input.

    Returns the number of newly snapshotted files.
    """
    added = 0
    for filepath in filepaths:
        filepath = str(filepath)
        if filepath in manifest["modified"] or filepath in manifest["created"]:
            continue

        backup_path = BACKUP_DIR / filepath
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        if Path(filepath).exists():
            shutil.copy2(filepath, backup_path)
            manifest["modified"].append(filepath)
        else:
            manifest["created"].append(filepath)
        added += 1

    return added


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


def gate_patch(patch: Dict, active_mode: Optional[str],
               instances: List[Dict], default_mode: Optional[str] = None) -> Optional[List[Dict]]:
    """Filter a patch's target instances by the two general gates, or return None to skip
    the patch entirely.

      * ``mode:`` restricts a patch to a single routing mode. Used by unified multi-mode
        defs (e.g. uart_v1) so one yaml holds both the with-pinmux and without-pinmux
        patches; a patch tagged with a mode other than the active one is skipped, while
        untagged patches form the shared core and always apply.

        A config selects the mode with ``modes:`` + a per-instance mode field. A config
        that instead uses a plain ``def_path`` declares NO mode, so ``active_mode`` is
        None; in that case the def's own ``default_mode:`` decides which tagged patches
        run. This is what lets a unified def (pwm_v1's with/without pinmux, rtc_v1's
        with/without io) stay backward-compatible with the many configs that reference it
        by ``def_path`` -- they transparently get the default mode's patches. Without this,
        every ``mode:``-tagged patch would silently skip on a def_path config and the
        peripheral would lose that routing (it may still COMPILE -- an unused output method
        is legal -- but the design is wrong).

      * ``plic_gated: true`` restricts a patch to instances whose ``connect_to_plic`` is
        truthy. This makes ``connect_to_plic: false`` skip every PLIC-routing patch AND its
        matching width increment (PLICWidth / ExtIntWidth / interrupt-bus widening), keeping
        the declared PLIC width equal to the number of routed sources. If no instance
        qualifies, the whole patch is skipped.
    """
    pmode = patch.get('mode')
    # No config-selected mode (def_path config) -> fall back to the def's default_mode.
    effective_mode = active_mode if active_mode is not None else default_mode
    if pmode is not None and pmode != effective_mode:
        return None
    if patch.get('plic_gated'):
        instances = [i for i in instances if i.get('connect_to_plic')]
        if not instances:
            return None
    return instances


# =============================================================================
# YAML MIGRATION UTILITIES
# =============================================================================

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

class ResourceTracker:
    """
    Centralized resource conflict detector for hardware integration.
    
    Tracks: address ranges, PLIC indices, macro names, pin assignments.
    Validates: resource conflicts BEFORE patch application.
    """
    
    def __init__(self) -> None:
        self.used_addresses: List[Tuple[int, int, str]] = []
        self.used_macros: Dict[str, str] = {}
        self.used_plic_indices: Dict[int, str] = {}
        self.shared_state: Dict[str, Any] = {}
        self.analyzer = BoardSupportAnalyzer()
    
    def ingest_static_state(
        self, 
        vfs: Dict[str, str], 
        master_xdc: Optional[str] = None, 
        active_xdc: Optional[str] = None
    ) -> None:
        """
        Ingest static state from VFS and XDC paths.
        
        Args:
            vfs: Virtual file system (path → content).
            master_xdc: Path to master constraints file.
            active_xdc: Path to active constraints file.
        """
        self.analyzer.analyze(vfs, master_xdc, active_xdc)
        
        # Extract macro definitions from Soc.defines files
        macro_count = 0
        for path, content in vfs.items():
            if 'Soc.defines' in path:
                for m in re.finditer(
                    r"^`define\s+(\w+)\s+'?h?([0-9a-fA-F]+)", 
                    content, 
                    re.MULTILINE
                ):
                    self.used_macros[m.group(1)] = "static"
                    macro_count += 1
        
        if macro_count > 0:
            log_info(f"[TRACKER] Ingested {macro_count} static macros from SoC defines.")
    
    def reserve_address_range(self, start: int, end: int, owner: str) -> List[str]:
        """
        Reserve an address range and detect conflicts.
        
        Args:
            start: Start address (integer).
            end: End address (integer).
            owner: Descriptive name of the resource owner.
            
        Returns:
            List of conflict messages (empty if no conflicts).
        """
        conflicts = []
        for s, e, o in self.used_addresses:
            if not (end < s or start > e):
                conflict_msg = f"Address range {hex(start)}-{hex(end)} ({owner}) overlaps with {o}"
                log_warn(f"[TRACKER] Conflict detected: {conflict_msg}")
                conflicts.append(conflict_msg)
        
        if not conflicts:
            self.used_addresses.append((start, end, owner))
        
        return conflicts
    
    def reserve_plic_index(self, index: int, owner: str) -> List[str]:
        """
        Reserve a PLIC interrupt index and detect conflicts.
        
        Args:
            index: PLIC index (integer).
            owner: Descriptive name of the resource owner.
            
        Returns:
            List of conflict messages (empty if no conflicts).
        """
        if index in self.used_plic_indices and self.used_plic_indices[index] != owner:
            conflict_msg = f"PLIC index {index} already claimed by {self.used_plic_indices[index]}"
            log_warn(f"[TRACKER] Conflict detected: {conflict_msg}")
            return [conflict_msg]
        
        self.used_plic_indices[index] = owner
        return []
    
    def audit_instances(self, peripherals: List[Dict], verbose: bool = True) -> bool:
        """
        Audit all peripheral instances for resource conflicts.
        
        Args:
            peripherals: List of peripheral configs from soc_build_config.yaml.
            verbose: If True, log individual pass/fail results.
            
        Returns:
            True if all audits pass, False otherwise.
        """
        all_passed = True
        
        for p in peripherals:
            base_ctx = {**p.get('base_context', {})}
            peripheral_name = p['name']
            
            for inst in p.get('instances', [{}]):
                ctx = build_instance_context(base_ctx, inst, peripheral_name)
                owner = f"{peripheral_name}_{ctx.get('instance_id', '0')}"
                
                # Audit address range
                if 'base_addr' in ctx and 'end_addr' in ctx:
                    try:
                        s = int(str(ctx['base_addr']).replace('0x', ''), 16)
                        e = int(str(ctx['end_addr']).replace('0x', ''), 16)
                        conflicts = self.reserve_address_range(s, e, owner)
                        if conflicts:
                            if verbose:
                                [log_fail(f"[TRACKER] {c}") for c in conflicts]
                            all_passed = False
                        elif verbose:
                            log_pass(f"[TRACKER] Address range {hex(s)}-{hex(e)} unique for {owner}")
                    except ValueError:
                        if verbose:
                            log_fail(f"[TRACKER] Invalid hex address for {owner}")
                        all_passed = False
                
                # Audit pin electrical compatibility
                if 'pin_hint' in ctx:
                    valid, issues = self.analyzer.validate_pin(
                        ctx['pin_hint'], 
                        ctx.get('verilog_buffer_type', 'LVCMOS33')
                    )
                    if not valid:
                        if verbose:
                            [log_fail(f"[TRACKER] {i}") for i in issues]
                        all_passed = False
                    elif verbose:
                        log_pass(f"[TRACKER] Pin {ctx['pin_hint']} validated for {owner}")
                
                # Audit PLIC index
                if ctx.get('connect_to_plic') and 'plic_index' in ctx:
                    try:
                        conflicts = self.reserve_plic_index(int(ctx['plic_index']), owner)
                        if conflicts:
                            if verbose:
                                [log_fail(f"[TRACKER] {c}") for c in conflicts]
                            all_passed = False
                        elif verbose:
                            log_pass(f"[TRACKER] PLIC index {int(ctx['plic_index'])} reserved for {owner}")
                    except (ValueError, TypeError):
                        if verbose:
                            log_fail(f"[TRACKER] Invalid PLIC index for {owner}")
                        all_passed = False
        
        return all_passed


# =============================================================================
# AUDIT FUNCTIONS (Independent from patch application)
# =============================================================================

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


def run_post_audit(config: Dict, vfs: Dict[str, str], rt: ResourceTracker) -> bool:
    """
    Run post-integration validation: verify patches applied correctly.
    
    Args:
        config: Loaded soc_build_config.yaml.
        vfs: Virtual file system (post-patch content).
        rt: ResourceTracker instance.
        
    Returns:
        True if all checks pass, False otherwise.
    """
    board_dir = config.get('board_dir', 'boards/nexys_video')
    peripherals = config.get('automated_peripherals', [])
    all_passed = True
    
    for p in peripherals:
        base_ctx = {**p.get('base_context', {}), 'board_dir': board_dir}
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

class PatchHandler(ABC):
    """Abstract base class for patch application handlers."""
    
    @abstractmethod
    def apply(
        self, 
        vfs: Dict[str, str], 
        anchor: Dict, 
        patch: Dict, 
        base_ctx: Dict, 
        expanded_instances: List[Dict], 
        dry_run: bool, 
        shared_state: Dict, 
        rt: ResourceTracker
    ) -> bool:
        """Apply patch to VFS. Returns True on success."""
        pass
    
    @abstractmethod
    def audit_post(
        self, 
        vfs: Dict[str, str], 
        anchor: Dict, 
        patch: Dict, 
        base_ctx: Dict, 
        expanded_instances: List[Dict], 
        shared_state: Dict, 
        rt: ResourceTracker
    ) -> bool:
        """Verify patch applied correctly. Returns True on success."""
        pass


class RegexHandler(PatchHandler):
    """
    Handler for regex-based injection with style observation.
    Responsibilities:
        - locate anchors using regex
        - preserve idempotency through skip_if_contains
        - delegate indentation/style handling to IndentationAnalyzer
        - inject code before, after, or in place of anchors
    """
    
    
    def apply(
        self, vfs: Dict[str, str], anchor: Dict, patch: Dict, base_ctx: Dict,
        expanded_instances: List[Dict], dry_run: bool, shared_state: Dict, rt: ResourceTracker
    ) -> bool:
        filepath = resolve_template(anchor['file'], base_ctx)
        if filepath not in vfs:
            log_fail(f"[HANDLER] Target file missing from VFS: {filepath}")
            return False
            
        content = vfs[filepath]
        skip_str = patch.get('skip_if_contains')
        skip_rx = patch.get('skip_if_matches')
        apply_once = patch.get('apply_once', False)

        # === IDEMPOTENCY CHECK ===
        # skip_if_contains is a literal substring (whitespace-sensitive);
        # skip_if_matches is a regex for cases a literal cannot express.
        if apply_once and (skip_str or skip_rx):
            test_ctx = expanded_instances[0] if expanded_instances else base_ctx
            if self._already_present(content, skip_str, skip_rx, test_ctx, filepath):
                log_debug(f"[HANDLER] Patch already applied (apply_once). Skipping.")
                return True
            delta = [expanded_instances[0]] if expanded_instances else []
        else:
            delta = []
            for i in expanded_instances:
                if skip_str or skip_rx:
                    if not self._already_present(content, skip_str, skip_rx, i, filepath):
                        delta.append(i)
                else:
                    delta.append(i)
        
        if not delta:
            log_debug(f"[HANDLER] All instances already present (idempotent). Skipping.")
            return True

        # `reverse_instances` emits this patch's instances in reversed config
        # order at the anchor. Use it for an MSB-first concatenation whose new
        # entries must descend while the config lists instances ascending, so
        # every other (order-independent) patch stays in natural order.
        if patch.get("reverse_instances"):
            delta = list(reversed(delta))

        regex_flags = re.MULTILINE

        if anchor.get("multiline", False):
            regex_flags |= re.DOTALL

        try:
            match = re.search(
                anchor["pattern"],
                content,
                regex_flags
            )
        except re.error as e:
            log_fail(
                f"[HANDLER] Invalid anchor regex in {filepath}: {e}"
            )
            return False

        if not match:
            log_fail(f"[HANDLER] Anchor pattern not found in {filepath}")
            return False
            
        # === OBSERVE TARGET FILE STYLE ===
        style = IndentationAnalyzer.observe_style(
            content,
            match.start(),
            match.end()
        )

        pos = anchor.get("position", "after")

        log_debug(
            f"[STYLE] {filepath}: "
            f"indent={repr(style['base_indent'])}, "
            f"blank_before={style['newline_before']}, "
            f"blank_after={style['newline_after']}"
        )

        # === PREPARE INJECTED CODE ===
        code_blocks = []
        
        strip_last = patch.get("strip_last_trailing_comma", False)

        for idx, i in enumerate(delta):
            resolved = resolve_template(patch['code'], i)
            if filepath.endswith(".defines"):
                spacing = IndentationAnalyzer.get_defines_spacing(
                    content,
                    match.start()
                )

                formatted = IndentationAnalyzer.apply_defines_indent(
                    resolved,
                    spacing
                ).rstrip("\n")

            else:
                # Central indent resolution: an explicit indent_context on
                # the patch (or its anchor) selects a value from
                # INDENT_PROFILE; otherwise the sanitized observed/sibling
                # indent is used. Legacy tabs never leak into insertions.
                context_name = patch.get("indent_context") or anchor.get("indent_context")

                base_indent = IndentationAnalyzer.resolve_target_indent(
                    content=content,
                    match_start=match.start(),
                    match_end=match.end(),
                    code=resolved,
                    position=pos,
                    observed_indent=style["base_indent"],
                    context=context_name,
                )

                formatted = IndentationAnalyzer.apply_indentation(
                    resolved,
                    base_indent
                )

                self._lint_flat_block(formatted, filepath)

            if formatted.strip():
                code_blocks.append(formatted)
        
        # Join generated instance blocks deterministically.
        inj = "\n".join(
            block.rstrip("\n")
            for block in code_blocks
            if block.strip()
        )

        # For comma-separated insertions placed AFTER an existing item:
        #   1. ensure the matched anchor ends with a comma
        #   2. remove the comma only from the final generated item
        #
        # Example:
        #   .ext_interrupts_i(interrupts)
        # becomes:
        #   .ext_interrupts_i(interrupts),
        #   .rtc0_io_rtc_clock_signal(...),
        #   .rtc1_io_rtc_clock_signal(...)
        if strip_last and inj:
            inj = re.sub(r',(\s*)$', r'\1', inj)

            if pos == "after":
                matched_text = match.group(0)

                if not matched_text.rstrip().endswith(","):
                    anchor_end = match.end()

                    content = (
                        content[:anchor_end]
                        + ","
                        + content[anchor_end:]
                    )

                    # The insertion point moved by one character because
                    # we inserted a comma before the generated block.
                    match = re.search(
                        anchor["pattern"],
                        content,
                        regex_flags
                    )
                    
        # Position-aware spacing defaults.
        #
        # replace:
        #   preserve blank lines outside both sides of replaced block
        #
        # after:
        #   patch follows anchor directly by default;
        #   preserve blank lines that originally followed anchor
        #
        # before:
        #   preserve blank lines that originally preceded anchor;
        #   patch precedes anchor directly by default
        if pos == "replace":
            default_blank_before = style["newline_before"]
            default_blank_after = style["newline_after"]

        elif pos == "after":
            default_blank_before = 0
            default_blank_after = style["newline_after"]

        elif pos == "before":
            default_blank_before = style["newline_before"]
            default_blank_after = 0

        else:
            log_fail(
                f"[HANDLER] Unsupported patch position "
                f"'{pos}' in {filepath}"
            )
            return False

        # Rich YAML override support.
        blank_before = patch.get(
            "blank_lines_before",
            default_blank_before
        )

        blank_after = patch.get(
            "blank_lines_after",
            default_blank_after
        )

        # Backward compatibility with old boolean YAML fields.
        if patch.get("blank_line_before", False):
            blank_before = max(blank_before, 1)

        if patch.get("blank_line_after", False):
            blank_after = max(blank_after, 1)

        # Defensive normalization.
        blank_before = max(0, int(blank_before))
        blank_after = max(0, int(blank_after))
        
        # === POSITION-AWARE INJECTION (single splice path) ===
        new_content = self._splice(
            content, match, pos, inj, blank_before, blank_after
        )

        if new_content is None:
            log_fail(
                f"[HANDLER] Unsupported patch position "
                f"'{pos}' in {filepath}"
            )
            return False

        vfs[filepath] = new_content
        
        if not dry_run:
            log_pass(f"[HANDLER] RegexHandler: Injected {len(delta)} instance(s) to {filepath}")
        else:
            log_info(f"[HANDLER] RegexHandler: Would inject {len(delta)} instance(s) to {filepath}")

        return True

    @staticmethod
    def _already_present(content: str, skip_str: Optional[str], skip_rx: Optional[str], ctx: Dict, filepath: str = "") -> bool:
        """
        Idempotency test: literal skip_if_contains OR regex skip_if_matches.

        Tested against COMMENT-STRIPPED content. A skip guard asks "has my code
        already been injected?", and commented-out code has not been. Searching
        raw text made a guard match dead code and silently skip a real patch:
        spi_cluster.bsv shipped a commented-out `/*else if(addr>= `SPI2Base ...*/`
        decoder arm, so sspi_v1's guard (`SPI2Base) matched it, SPI2's address
        decoder arm was never injected, and SPI2 became an unreachable slave --
        it compiled, and the post-audit passed, because the audit grepped for the
        same string and also found the comment.
        """
        probe = strip_comments(content, filepath)
        if skip_str:
            resolved = resolve_template(skip_str, ctx)
            if re.search(re.escape(resolved), probe):
                return True
        if skip_rx:
            resolved = resolve_template(skip_rx, ctx)
            try:
                if re.search(resolved, probe, re.MULTILINE):
                    return True
            except re.error as e:
                log_warn(f"[HANDLER] Invalid skip_if_matches regex '{skip_rx}': {e}")
        return False

    @staticmethod
    def _lint_flat_block(formatted: str, filepath: str) -> None:
        """
        Warn-only authoring lint: a multi-line block whose first line opens a
        BSV construct (else-if branch, rule) must have its next line indented
        one level deeper. Catches flat YAML code blocks before they produce
        misaligned output.
        """
        lines = [l for l in formatted.splitlines() if l.strip()]
        if len(lines) < 2:
            return

        first = lines[0].strip()
        if not re.match(r'^(else\s+if\b|rule\b)', first):
            return

        def width(line: str) -> int:
            m = re.match(r'^[ \t]*', line)
            return len(m.group(0).expandtabs(2)) if m else 0

        second = lines[1].strip()
        if width(lines[1]) <= width(lines[0]) and not second.startswith(("end", "`")):
            log_warn(
                f"[LINT] Flat code block for {filepath}: "
                f"'{first[:50]}' opens a block but the next line is not "
                f"indented deeper — check the YAML code: block"
            )

    @staticmethod
    def _splice(
        content: str, match: "re.Match", pos: str, inj: str,
        blank_before: int, blank_after: int
    ) -> Optional[str]:
        """
        Splice the injected block into content at the match position.

        Guarantees no whitespace-only line can be created by the splice:
        a stranded anchor indent on the left side is removed, and a
        whitespace-only remainder of the matched line on the right side is
        consumed.
        """

        if pos == "replace":
            split_left, split_right = match.start(), match.end()
        elif pos == "after":
            split_left = split_right = match.end()
        elif pos == "before":
            # Split at the start of the anchor's physical line when only
            # whitespace precedes the match there, so the anchor keeps its
            # indentation and no whitespace-only line is stranded.
            line_begin = content.rfind("\n", 0, match.start()) + 1
            if content[line_begin:match.start()].strip() == "":
                split_left = split_right = line_begin
            else:
                split_left = split_right = match.start()
        else:
            return None

        left = content[:split_left]
        right = content[split_right:]

        # Remove a stranded indent (the anchor's or the replaced block's
        # leading whitespace) from the end of the left side, then normalize
        # newline boundaries -- blank counts are re-applied below.
        left = re.sub(r"[ \t]+$", "", left).rstrip("\n")

        # If the remainder of the matched line is whitespace-only, consume it.
        nl = right.find("\n")
        head = right if nl == -1 else right[:nl]
        if head and head.strip() == "":
            right = right[len(head):]
        right = right.lstrip("\n")

        left_sep = "\n" * (blank_before + 1) if left else ""
        right_sep = "\n" * (blank_after + 1)

        return left + left_sep + inj + right_sep + right

    def audit_post(
        self, vfs: Dict[str, str], anchor: Dict, patch: Dict, base_ctx: Dict,
        expanded_instances: List[Dict], shared_state: Dict, rt: ResourceTracker
    ) -> bool:
        """Verify that injected patterns (skip_if_contains) exist in the patched VFS."""
        filepath = resolve_template(anchor['file'], base_ctx)
        content = vfs.get(filepath, "")
        if not content:
            return log_fail(f"[AUDIT] VFS content missing for {filepath}")
            
        skip_str = patch.get('skip_if_contains')
        skip_rx = patch.get('skip_if_matches')
        if not skip_str and not skip_rx:
            return True  # No idempotency check defined for this patch

        for ctx in expanded_instances:
            if not self._already_present(content, skip_str, skip_rx, ctx, filepath):
                shown = skip_str or skip_rx
                return log_fail(f"[AUDIT] Expected pattern '{shown[:40]}...' not found in {filepath}")

        shown = skip_str or skip_rx
        return log_pass(f"[AUDIT] Pattern '{shown[:40]}...' present in {filepath}")


class PLICVectorHandler(PatchHandler):
    """Handler for BSV PLIC interrupt vector manipulation with deterministic sorting."""
    
    @staticmethod
    def _parse_items(concat_str: str) -> List[Tuple[str, int]]:
        """Parse BSV concatenation into [(signal_text, bit_width), ...]."""
        items = []
        for raw in concat_str.split(','):
            raw = raw.strip()
            if not raw or raw == '}':
                continue
            clean_raw = raw.lstrip()
            match = re.match(r'([\w$.]+)\s*\[\s*(\d+)\s*(?::\s*(\d+))?\s*\]', clean_raw)
            if match:
                high = int(match.group(2))
                low = int(match.group(3)) if match.group(3) else high
                width = abs(high - low) + 1
                items.append((clean_raw, width))
            else:
                items.append((clean_raw, 1))
        return items
    
    def apply(
        self, 
        vfs: Dict[str, str], 
        anchor: Dict, 
        patch: Dict, 
        base_ctx: Dict, 
        expanded_instances: List[Dict], 
        dry_run: bool, 
        shared_state: Dict, 
        rt: ResourceTracker
    ) -> bool:
        filepath = resolve_template(anchor['file'], base_ctx)
        content = vfs.get(filepath, "")
        
        # The PLIC vector width may be either a literal (`Bit#(35) plic_inputs`)
        # or a macro (`Bit#(`PLICWidth) plic_inputs`). With the macro form the
        # width is DERIVED in Soc.defines -- every contributing IP grows
        # `PLICWidth` through a cluster_config counter -- so this handler must
        # only insert the signal and leave the width alone. A missing
        # width_pattern match is therefore not an error; it means "macro form".
        w_match = re.search(anchor['width_pattern'], content)
        c_match = re.search(anchor['concat_pattern'], content, re.DOTALL)

        if not c_match:
            log_fail(f"[HANDLER] PLIC concat pattern not found in {filepath}")
            return False

        old_width = int(w_match.group(1)) if w_match else None
        items = self._parse_items(c_match.group(1))
        existing_signals = {t for t, _ in items}
        
        # === COLLECT NEW SIGNALS WITH INDICES ===
        new_signals: List[Tuple[int, str]] = []  # (plic_index, signal_name)
        
        for ctx in expanded_instances:
            if not ctx.get('connect_to_plic'):
                continue
            signal = resolve_template(patch['signal'], ctx)
            plic_index = resolve_template(patch.get('plic_index', 'lsb'), ctx)
            
            # Skip if already present
            if signal in existing_signals:
                log_debug(f"[HANDLER] Signal '{signal}' already in vector. Skipping.")
                continue
            
            # Convert position to numeric index
            if plic_index == 'msb':
                # For MSB, use a high index to sort first (we'll reverse sort later)
                idx = 999999
            elif plic_index == 'lsb':
                idx = -1
            else:
                idx = int(plic_index)
            
            new_signals.append((idx, signal))
            existing_signals.add(signal)
        
        if not new_signals:
            log_debug("[HANDLER] No new PLIC signals to insert.")
            return True
        
        # === SORT SIGNALS: Highest index first (MSB order) ===
        new_signals.sort(key=lambda x: x[0], reverse=True)
        
        # === FIX: Insert at MSB while preserving sorted order ===
        # Convert sorted signals to (signal, width) tuples
        new_items = [(s,1) for _,s in reversed(new_signals)] # Reverse to maintain MSB -> LSB order in the final concatenation of rtc
        # Prepend to existing items to maintain MSB -> LSB order
        # e.g., if new_signals = [High, Low], result = [High, Low, ...existing...]
        items = new_items + items
        
        # === REBUILD CONCATENATION STRING ===
        new_concat = ', '.join(t for t, _ in items)
        
        # Replace concatenation content
        # c_match.group(1) is the content inside the braces
        new_content = content[:c_match.start(1)] + new_concat + content[c_match.end(1):]

        if old_width is not None:
            # Literal form: this handler owns the width, and the dependent
            # mkplic-wrapper patch reads it back out of shared_state.
            new_width = old_width + len(new_signals)
            rt.shared_state['plic_width'] = new_width
            new_content = (
                new_content[:w_match.start(1)]
                + str(new_width)
                + new_content[w_match.end(1):]
            )
            msg = f"width {old_width} → {new_width}"
        else:
            # Macro form: `PLICWidth is grown by each IP's cluster_config
            # counter in Soc.defines. Leave shared_state['plic_width'] unset so
            # any legacy mkplic-wrapper patch skips instead of fighting it.
            msg = f"inserted {len(new_signals)} signal(s); width derived from `PLICWidth"

        vfs[filepath] = new_content

        if not dry_run:
            log_pass(f"[HANDLER] PLICVectorHandler: {msg} in {filepath}")
        else:
            log_info(f"[HANDLER] PLICVectorHandler: would update {msg} in {filepath}")

        return True

    def audit_post(
        self, 
        vfs: Dict[str, str], 
        anchor: Dict, 
        patch: Dict, 
        base_ctx: Dict, 
        expanded_instances: List[Dict], 
        shared_state: Dict, 
        rt: ResourceTracker
    ) -> bool:
        """Verify that the PLIC vector in the patched file contains all injected signals."""
        filepath = resolve_template(anchor['file'], base_ctx)
        content = vfs.get(filepath, "")
        if not content:
            return log_fail(f"[AUDIT] VFS content missing for {filepath}")

        # Locate the vector block
        pattern = anchor.get('concat_pattern', '')
        if not pattern:
            return log_fail("[AUDIT] Missing concat_pattern in PLIC anchor definition")

        c_match = re.search(pattern, content, re.DOTALL)
        if not c_match:
            return log_fail(f"[AUDIT] PLIC concatenation block missing in {filepath}")

        concat_content = c_match.group(1)

        # Verify each required signal is present in the vector block
        for ctx in expanded_instances:
            if not ctx.get('connect_to_plic'):
                continue
            
            signal = resolve_template(patch['signal'], ctx)

            if signal not in concat_content:
                return log_fail(f"[AUDIT] PLIC signal '{signal}' MISSING from vector in {filepath}")

        return log_pass(f"[AUDIT] PLIC vector contains all injected signals in {filepath}")


class ClusterConfigHandler(PatchHandler):
    """Handler for cluster-wide macro updates (slave count, PLIC width)."""
    
    def apply(
        self, 
        vfs: Dict[str, str], 
        anchor: Dict, 
        patch: Dict, 
        base_ctx: Dict, 
        expanded_instances: List[Dict], 
        dry_run: bool, 
        shared_state: Dict, 
        rt: ResourceTracker
    ) -> bool:
        filepath = resolve_template(anchor['file'], base_ctx)
        content = vfs.get(filepath, "")
        macro = patch.get('target_macro')
        skip_str = patch.get('skip_if_contains')
        apply_once = patch.get('apply_once', False)

        # === IDEMPOTENCY: only keep instances whose per-instance line isn't there yet ===
        # Guards are tested against COMMENT-STRIPPED content: commented-out code
        # has not been injected, so it must not satisfy a skip guard. See
        # RegexHandler._already_present for the SPI2 bug this prevents.
        probe = strip_comments(content, filepath)
        if skip_str:
            delta = [
                inst for inst in expanded_instances
                if not re.search(re.escape(resolve_template(skip_str, inst)), probe)
            ]
        else:
            delta = list(expanded_instances)

        if expanded_instances and not delta:
            log_debug(f"[HANDLER] ClusterConfig: `{macro}` already present for all instances in {filepath}. Skipping.")
            return True

        # apply_once patches with no per-instance text (e.g. PLIC wrapper width) should
        # only ever fire once, checked against the first still-pending instance.
        if apply_once and skip_str:
            test_ctx = delta[0] if delta else (expanded_instances[0] if expanded_instances else base_ctx)
            if re.search(re.escape(resolve_template(skip_str, test_ctx)), probe):
                log_debug(f"[HANDLER] ClusterConfig: patch already applied (apply_once). Skipping.")
                return True

        # Calculate new value: increment ONLY by the new instances being added
        current_val = None
        if patch.get('increment_by_instances'):
            current_val = extract_define_value(content, macro)
            if current_val is None:
                log_debug(f"[HANDLER] Cannot determine current value for `{macro}`. Skipping patch.")
                return True
            new_val = current_val + len(delta)
        else:
            new_val = shared_state.get(patch.get('source_from_shared_state'))
            if new_val is None:
                log_debug(f"[HANDLER] Shared state '{patch.get('source_from_shared_state')}' not set. Skipping patch.")
                return True

        # Find anchor to replace
        match = re.search(
            anchor['pattern'],
            content,
            re.MULTILINE
        )

        if not match:
            log_fail(f"[HANDLER] ClusterConfig anchor pattern not found in {filepath}")
            return False

        # Build replacement text — only for instances still in `delta`
        if delta:
            blocks = []
            lines = patch['code'].splitlines()

            for slot_offset, inst in enumerate(delta):
                inst_ctx = inst.copy()
                inst_ctx['calculated_val'] = str(new_val)

                # `allocated_slot` lets a peripheral claim the slot the counter
                # is displacing, computed at apply time from the CURRENT value
                # (current_val + position in this batch) instead of a hardcoded
                # number. This makes slave-number allocation independent of
                # which other peripherals are present or their order: standalone
                # or combined, each new slave takes the next free err-slot and
                # the err/count macros move past it.
                if current_val is not None:
                    inst_ctx['allocated_slot'] = str(current_val + slot_offset)
                    # Publish it back onto the shared instance dict so LATER
                    # patches of the same peripheral can template on it. That is
                    # what lets an IP emit its `X_slave_num` define next to its
                    # own family (e.g. I2C2/I2C3 right after I2C1) with a plain
                    # regex patch, instead of being forced to emit it here at the
                    # err-counter anchor, which piled every peripheral's slave
                    # number up in one block ordered by apply order.
                    inst['allocated_slot'] = inst_ctx['allocated_slot']

                inst_code = '\n'.join(lines[:-1])

                if inst_code.strip():
                    blocks.append(
                        resolve_template(inst_code, inst_ctx)
                    )

            global_ctx = expanded_instances[0].copy()
            global_ctx['calculated_val'] = str(new_val)

            blocks.append(
                resolve_template(lines[-1], global_ctx)
            )

            replacement = '\n'.join(blocks)

        else:
            ctx = {'calculated_val': str(new_val)}
            replacement = resolve_template(
                patch['code'],
                ctx
            )
            
        after = content[match.end():]

        if filepath.endswith('.defines'):
            spacing = IndentationAnalyzer.get_defines_spacing(content, match.start())
            replacement = IndentationAnalyzer.apply_defines_indent(
                replacement,
                spacing
            )

            # The `replace` anchors match the define line WITHOUT its trailing
            # newline, so the newline that terminates the last injected line is
            # already the first character of `after`. apply_defines_indent()
            # unconditionally appends one too, which left a stray blank line
            # behind every cluster_config patch -- the gap grew by one line per
            # peripheral. Drop it here (as the regex handler already does) and
            # only terminate the block when `after` cannot (anchor is the last
            # line of the file, with no trailing EOL).
            if after.startswith('\n'):
                replacement = replacement.rstrip('\n')
            elif not replacement.endswith('\n'):
                replacement += '\n'

        vfs[filepath] = (
            content[:match.start()]
            + replacement
            + after
        )
        
        if not dry_run:
            log_pass(f"[HANDLER] ClusterConfig: Updated `{macro}` = {new_val} in {filepath}")
        else:
            log_info(f"[HANDLER] ClusterConfig: Would update `{macro}` = {new_val} in {filepath}")
        
        return True
    
    def audit_post(
        self, 
        vfs: Dict[str, str], 
        anchor: Dict, 
        patch: Dict, 
        base_ctx: Dict, 
        expanded_instances: List[Dict], 
        shared_state: Dict, 
        rt: ResourceTracker
    ) -> bool:
        filepath = resolve_template(anchor['file'], base_ctx)
        content = vfs.get(filepath, "")
        macro = patch.get('target_macro')
        
        if patch.get('source_from_shared_state'):
            # NOTE: shared_state is only populated during a live --bsv apply run.
            # Post-audit runs as a separate process invocation, so shared_state
            # is always empty here. Derive the value directly from file content
            # instead of depending on cross-process runtime state.
            if patch['source_from_shared_state'] == 'plic_width':
                width_match = re.search(
                    r'Bit\s*#\s*\(\s*(\d+)\s*\)\s+plic_inputs\s*=',
                    content
                )
                wrapper_match = re.search(anchor['pattern'], content, re.MULTILINE)
                
                if not width_match or not wrapper_match:
                    log_fail(f"[AUDIT] Could not locate PLIC width or mkplic wrapper in {filepath}")
                    return False
                
                actual_width = int(width_match.group(1))
                wrapper_digits = re.search(r'(\d+)\s*$', wrapper_match.group(0))
                wrapper_val = int(wrapper_digits.group(1)) if wrapper_digits else None
                
                if wrapper_val is None or actual_width != wrapper_val:
                    log_fail(
                        f"[AUDIT] PLIC vector width ({actual_width}) does not match "
                        f"mkplic wrapper width ({wrapper_val}) in {filepath}"
                    )
                    return False
                return log_pass(
                    f"[AUDIT] PLIC vector width ({actual_width}) matches "
                    f"mkplic wrapper width in {filepath}"
                )
            
            # Fallback for any other shared_state-based patch type
            val = shared_state.get(patch['source_from_shared_state'])
            if val is None:
                log_fail(f"[AUDIT] Shared state '{patch['source_from_shared_state']}' is missing")
                return False
            
            code_template = patch.get('code', '')
            
            # Case 1: Patch defines a `define macro → strict regex validation
            if '`define' in code_template:
                pattern = rf"^`define\s+{re.escape(macro)}\s+{re.escape(str(val))}"
                if re.search(pattern, content, re.MULTILINE):
                    return log_pass(f"[AUDIT] Macro `{macro}={val}` defined in {filepath}")
                log_fail(f"[AUDIT] `{macro}={val}` macro definition missing in {filepath}")
                return False

            # Case 2: Inline value injection (e.g., module params) → substring check
            else:
                if str(val) in content:
                    return log_pass(f"[AUDIT] Calculated value {val} for '{macro}' present in {filepath}")
                log_fail(f"[AUDIT] Calculated value {val} for '{macro}' not found in {filepath}")
                return False

        # Fallback: no shared_state → standard macro check
        elif macro:
            pattern = rf"^`define\s+{re.escape(macro)}\s+\S+"
            if re.search(pattern, content, re.MULTILINE):
                return log_pass(f"[AUDIT] Macro `{macro}` defined in {filepath}")
            log_fail(f"[AUDIT] Macro `{macro}` not found in {filepath}")
            return False

        return True


class XDCPinAssignHandler(PatchHandler):
    """Handler for XDC constraint injection with conflict resolution."""
    
    def apply(
        self, 
        vfs: Dict[str, str], 
        anchor: Dict, 
        patch: Dict, 
        base_ctx: Dict, 
        expanded_instances: List[Dict], 
        dry_run: bool, 
        shared_state: Dict, 
        rt: ResourceTracker
    ) -> bool:
        filepath = resolve_template(anchor['file'], base_ctx)
        if filepath not in vfs:
            log_fail(f"[HANDLER] XDC target file missing from VFS: {filepath}")
            return False
        
        pin_map = rt.analyzer.get_pin_map()
        
        for ctx in expanded_instances:
            port = resolve_template(patch['port_name'], ctx)
            target_pin = resolve_template(patch.get('target_board_pin', ''), ctx)
            fallback_pin = resolve_template(patch.get('fallback_pin', ''), ctx)
            # Resolve io_standard too, so it can be driven from config context
            # (e.g. "{uart3_io_standard}") instead of only a literal.
            io_standard = resolve_template(patch.get('io_standard', 'LVCMOS33'), ctx)
            
            # Validate electrical compatibility BEFORE injection
            valid, issues = rt.analyzer.validate_pin(target_pin, io_standard)
            if not valid:
                for issue in issues:
                    log_fail(f"[HANDLER] {issue}")
                return False
            
            log_info(f"[HANDLER] XDCPinAssign: port='{port}', target='{target_pin}', fallback='{fallback_pin}'")
            
            success, updated = rt.analyzer.apply_patch(port, target_pin, fallback_pin, pin_map, dry_run, io_standard)
            if success:
                vfs[filepath] = updated
            else:
                return False
        
        return True
    
    def audit_post(
        self, 
        vfs: Dict[str, str], 
        anchor: Dict, 
        patch: Dict, 
        base_ctx: Dict, 
        expanded_instances: List[Dict], 
        shared_state: Dict, 
        rt: ResourceTracker
    ) -> bool:
        filepath = resolve_template(anchor['file'], base_ctx)
        content = vfs.get(filepath, "")
        
        for ctx in expanded_instances:
            port = resolve_template(patch['port_name'], ctx)
            pattern = rf'\[get_ports\s+\{{?\s*{re.escape(port)}\s*\}}?\]'
            if not re.search(pattern, content):
                log_fail(f"[AUDIT] Constraint for '{port}' MISSING from {filepath}")
                return False
            log_pass(f"[AUDIT] Constraint for '{port}' present in {filepath}")

        return True


# Handler registry: type string → handler instance
HANDLERS: Dict[str, PatchHandler] = {
    "regex": RegexHandler(),
    "plic_vector": PLICVectorHandler(),
    "cluster_config": ClusterConfigHandler(),
    "xdc_pin_assign": XDCPinAssignHandler(),
}


# =============================================================================
# MAIN EXECUTION ENGINE (Patch Application Only — Audits Separate)
# =============================================================================

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

def main() -> None:
    """Unified CLI for integration, auditing, and migration."""
    parser = argparse.ArgumentParser(
        description='Shakti Peripheral Auto-Integrator V2.0',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # Core arguments
    parser.add_argument(
        '--config', 
        default='soc_build_config.yaml', 
        help='Path to SOC configuration YAML'
    )
    parser.add_argument(
        '--dry-run', 
        action='store_true', 
        help='Simulate changes without writing to disk'
    )
    parser.add_argument(
        '--restore',
        action='store_true',
        help='Restore files from .automation_backup manifest'
    )
    parser.add_argument(
        '--backup',
        nargs='+',
        metavar='FILE',
        help='Snapshot FILEs into .automation_backup and record them in the '
             'manifest, so --restore rewinds them too. Used by the sync targets '
             'to protect the hw/ root copies before they are overwritten.'
    )
    parser.add_argument(
        '--reset-baseline',
        action='store_true',
        help='Discard .automation_backup so the next run re-captures the pristine '
             'baseline. Run only from a restored/clean tree.'
    )
    parser.add_argument(
        '--bootstrap-board',
        action='store_true',
        help='Seed the automation anchors a fresh clone lacks (derived-width '
             'macros, comment markers, one-per-line concats) into the board '
             'sources. Idempotent; runs automatically before pre-audit and '
             'integration, but exposed here so it can be invoked on its own.'
    )
    parser.add_argument(
        '--verbose', '-v', 
        action='store_true', 
        help='Enable verbose/debug logging'
    )
    
    # YAML migration group
    migration_group = parser.add_argument_group('YAML Migration')
    migration_group.add_argument(
        '--migrate', 
        action='store_true', 
        help='Strip auto-generated naming keys from soc_build_config.yaml'
    )
    migration_group.add_argument(
        '--check-migration', 
        action='store_true', 
        help='Check if config requires YAML migration'
    )
    
    # Pin tracking
    parser.add_argument(
        '--track-pin-map', 
        action='store_true', 
        help='Parse XDC files and regenerate pin_map.yaml'
    )
    
    # Audit group (INDEPENDENT from patch application)
    audit_group = parser.add_argument_group('Auditing (Independent)')
    audit_group.add_argument(
        '--pre-audit', 
        action='store_true', 
        help='Run pre-integration validation ONLY (no patch application)'
    )
    audit_group.add_argument(
        '--post-audit', 
        action='store_true', 
        help='Run post-integration validation ONLY (no patch application)'
    )
    
    # Phase execution group
    phase_group = parser.add_argument_group('Phased Execution')
    phase_group.add_argument(
        '--bsvpath', 
        action='store_true', 
        help='Apply only bsvpath phase'
    )
    phase_group.add_argument(
        '--bsv', 
        action='store_true', 
        help='Apply BSV/SoC patches only'
    )
    phase_group.add_argument(
        '--verilog', 
        action='store_true', 
        help='Apply fpga_top.v routing patches only'
    )
    phase_group.add_argument(
        '--xdc', 
        action='store_true', 
        help='Apply XDC constraint patches only'
    )
    
    args = parser.parse_args()
    
    # Set verbose mode globally via utils
    set_verbose(args.verbose)
    
    # Load configuration
    config = load_yaml(args.config)
    
    # === YAML MIGRATION MODE ===
    if args.check_migration:
        log_info("[CLI] Migration check mode activated")
        needed = check_migration_needed(args.config)
        if needed:
            log_warn("[CLI] Migration recommended: config contains auto-generated keys.")
            log_info("[CLI] Run with --migrate --dry-run to preview changes safely.")
            sys.exit(1)
        else:
            log_pass("[CLI] Config is already minimal. No migration needed.")
            sys.exit(0)
    
    if args.migrate:
        log_info("[CLI] YAML migration mode activated")
        success = migrate_soc_config(args.config, dry_run=args.dry_run)
        sys.exit(0 if success else 1)
    
    # === PIN TRACKING MODE ===
    if args.track_pin_map:
        log_info("[CLI] Pin tracking mode activated")
        board_dir = config.get('board_dir', 'boards/nexys_video')
        master_xdc = config.get('master_xdc_path', f'{board_dir}/master_constraints.xdc')
        active_xdc = config.get('active_xdc_path', f'{board_dir}/constraints.xdc')
        pin_map_path = config.get('pin_map_path', f'{board_dir}/pin_map.yaml')
        
        analyzer = BoardSupportAnalyzer()
        analyzer.analyze({}, master_xdc, active_xdc)
        analyzer.export_pin_map(
            pin_map_path,
            master_xdc_path=master_xdc,
            active_xdc_path=active_xdc,
            board_name=config.get('target_board', board_dir.split('/')[-1]),
            fpga_part=config.get('fpga_part', '')
        )
        log_pass("[CLI] Pin map updated successfully.")
        return
    
    # === BACKUP MODE ===
    # Snapshot extra files (the hw/ root copies) into the SAME manifest the
    # patch phases use, so one --restore rewinds board sources and root copies
    # together. No-op for files an earlier phase already captured.
    if args.backup:
        log_info("[CLI] Backup mode activated")
        manifest = load_manifest()
        existing = [f for f in args.backup if Path(f).exists()]
        added = snapshot_files(existing, manifest)
        save_manifest(manifest)
        log_pass(f"[CLI] Backed up {added} new file(s); {len(existing) - added} already tracked.")
        return

    # === RESTORE MODE ===
    if args.restore:
        log_info("[CLI] Restore mode activated")
        backup_dir = BACKUP_DIR

        if not backup_dir.exists():
            log_info("[CLI] No backup directory found. Nothing to restore.")
            return

        manifest_path = backup_dir / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            log_info(f"[CLI] Restoring from manifest: {len(manifest['modified'])} modified, {len(manifest.get('created', []))} created")

            for filepath in manifest["modified"]:
                src = backup_dir / filepath
                dst = Path(filepath)
                if src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                else:
                    log_warn(f"[CLI] Manifest lists '{filepath}' but its snapshot is missing; left untouched.")

            for filepath in manifest.get("created", []):
                Path(filepath).unlink(missing_ok=True)
        else:
            log_warn("[CLI] Legacy backup detected. Falling back to directory scan restoration")
            for backup_file in backup_dir.rglob("*"):
                if backup_file.is_file() and backup_file.name != "manifest.json":
                    original_path = backup_file.relative_to(backup_dir)
                    if original_path.exists():
                        shutil.copy2(backup_file, original_path)

        # The baseline is deliberately KEPT. It is the one surviving copy of the
        # pre-automation sources; deleting it here let the next integration run
        # re-cut a baseline from an already-patched tree, which is how a restore
        # silently degrades into a no-op. Use --reset-baseline to drop it.
        log_pass("[CLI] Restore complete. Baseline retained in .automation_backup/")
        return

    # === BOARD BOOTSTRAP MODE ===
    # Seed the anchors a fresh clone lacks, then stop. Integration and pre-audit
    # invoke bootstrap_board_anchors() themselves, so this standalone mode exists
    # mainly for the Makefile `bootstrap_board` target and manual recovery.
    if args.bootstrap_board:
        log_info("[CLI] Board bootstrap mode activated")
        if bootstrap_board_anchors(config, dry_run=args.dry_run):
            log_pass("[CLI] Board bootstrap complete.")
            return
        else:
            log_fail("[CLI] Board bootstrap failed")
            sys.exit(1)

    # === RESET-BASELINE MODE ===
    # Deliberately forget the pristine baseline. Only meaningful from a restored
    # (or otherwise known-good) tree: the NEXT integration run re-captures the
    # baseline from whatever is on disk at that moment.
    if args.reset_baseline:
        if not BACKUP_DIR.exists():
            log_info("[CLI] No baseline to reset.")
            return
        shutil.rmtree(BACKUP_DIR)
        log_pass("[CLI] Baseline discarded. The next integration run will re-capture one.")
        return

    # === AUDIT-ONLY MODES (Independent from patch application) ===
    if args.pre_audit or args.post_audit:
        # A fresh clone lacks the automation anchors, so pre-audit would fail
        # before it began. Seed them first (idempotent) so the anchors every IP
        # def depends on exist on disk. post-audit runs on an already-seeded tree,
        # where this is a no-op.
        if not bootstrap_board_anchors(config, dry_run=args.dry_run):
            log_fail("[CLI] Board bootstrap failed")
            sys.exit(1)

        # Load VFS for audit (no patch application)
        board_dir = config.get('board_dir', 'boards/nexys_video')
        peripherals = config.get('automated_peripherals', [])
        
        rt = ResourceTracker()
        vfs: Dict[str, str] = {}
        
        for p in peripherals:
            ctx = {**p.get('base_context', {}), 'board_dir': board_dir}
            peripheral_yaml = load_yaml(resolve_def_path(p, config))
            for anchor in peripheral_yaml.get('anchors', {}).values():
                filepath = resolve_template(anchor['file'], ctx)
                if Path(filepath).exists():
                    vfs[filepath] = Path(filepath).read_text()
        
        rt.ingest_static_state(
            vfs, 
            config.get('master_xdc_path', f'{board_dir}/master_constraints.xdc'),
            config.get('active_xdc_path', f'{board_dir}/constraints.xdc')
        )
        
        if args.pre_audit:
            log_info("[CLI] Pre-audit mode activated")
            if run_pre_audit(config, vfs, rt):
                log_pass("[CLI] Pre-audit passed")
                sys.exit(0)
            else:
                log_fail("[CLI] Pre-audit failed")
                sys.exit(1)
        
        if args.post_audit:
            log_info("[CLI] Post-audit mode activated")
            if run_post_audit(config, vfs, rt):
                log_pass("[CLI] Post-audit passed")
                sys.exit(0)
            else:
                log_fail("[CLI] Post-audit failed")
                sys.exit(1)
    
    # === PATCH APPLICATION MODE (Default) ===
    # Determine execution phase
    phase = 'all'
    if args.bsvpath:
        phase = 'bsvpath'
    elif args.bsv:
        phase = 'bsv'
    elif args.verilog:
        phase = 'verilog'
    elif args.xdc:
        phase = 'xdc'
    
    log_info(f"[CLI] Executing integration: phase='{phase}', dry_run={args.dry_run}")
    
    if execute_integration(config, args.dry_run, phase):
        log_pass("[CLI] Patch application successful")
    else:
        log_fail("[CLI] Patch application failed")
        sys.exit(1)


if __name__ == "__main__":
    main()