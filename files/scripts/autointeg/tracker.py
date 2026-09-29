#!/usr/bin/env python3
"""ResourceTracker: address/PLIC/macro/pin conflict detection."""

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
from .xdc import BoardSupportAnalyzer
from .patchmodel import build_instance_context


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

