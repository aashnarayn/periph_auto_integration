#!/usr/bin/env python3
"""Conditional compilation: `ifdef criteria, block wrapping, and `define site parsing."""

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


IFDEF_SUFFIX = '_ifdef'


def resolve_ifdef_criteria(peripheral: Dict) -> Optional[str]:
    """
    Read a peripheral's `<criteria>_ifdef: true` flag from soc_build_config.yaml.

    Returns the criteria name (e.g. 'simulate' for `simulate_ifdef: true`), or
    None when the peripheral is unconditional. Every patch the peripheral owns
    is then emitted inside `ifdef <criteria> / `endif, so the IP is compiled in
    only for builds that define that macro.

    Declaring more than one flag is an error: composing several criteria needs a
    defined semantics (AND or nested) that this does not yet pick for you.
    """
    found = sorted(
        k[:-len(IFDEF_SUFFIX)]
        for k, v in peripheral.items()
        if k.endswith(IFDEF_SUFFIX) and v
    )
    if not found:
        return None
    if len(found) > 1:
        log_fail(
            f"[IFDEF] '{peripheral.get('name')}' declares multiple ifdef flags "
            f"({', '.join(found)}). Only one is supported."
        )
        return None
    return found[0]


def wrap_in_ifdef(block: str, criteria: str) -> str:
    """
    Wrap an already-indented, already-formatted block in `ifdef / `endif.

    The directives sit two columns left of the block (floored at 0), matching
    how the baseline board sources guard `sign_dump` and the bootrom/bram pair.
    """
    if not block.strip():
        return block
    indents = [
        len(ln) - len(ln.lstrip())
        for ln in block.splitlines() if ln.strip()
    ]
    base = min(indents) if indents else 0
    if base == 0:
        # Column-0 block (.defines): directives stay at 0 and the body steps in,
        # matching how the baseline writes its `ifdef-guarded defines.
        body = '\n'.join(
            ('  ' + ln) if ln.strip() else ln for ln in block.splitlines()
        )
        return f"`ifdef {criteria}\n{body}\n`endif"
    pad = ' ' * max(0, base - 2)
    return f"{pad}`ifdef {criteria}\n{block}\n{pad}`endif"


def find_define_sites(content: str, macro: str) -> List[Dict]:
    """
    Locate every ``\`define <macro> <value>`` in `content`, with the conditional
    branch each one sits in.

    A macro may legitimately be defined more than once under `ifdef -- the
    baseline writes `Num_Fast_Slaves` as 7 for simulate and 6 otherwise. Reading
    or rewriting only the first occurrence (which is what a bare re.search does)
    updates one branch and silently freezes the other.

    Each site is {'start', 'end', 'value', 'indent', 'hex', 'branch'}; 'branch' is None at
    file scope, or (criteria, in_else) for the innermost enclosing conditional.
    """
    sites: List[Dict] = []
    stack: List[List] = []
    pat = re.compile(
        rf"^(\s*)`define\s+{re.escape(macro)}\s+('h|0x)?([0-9a-fA-F_]+)\s*$"
    )
    offset = 0
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        directive = re.match(r"^`(ifdef|ifndef|else|elsif|endif)\b\s*(\w+)?", stripped)
        if directive:
            kind, name = directive.group(1), directive.group(2)
            if kind == 'ifdef':
                stack.append([name, False])
            elif kind == 'ifndef':
                stack.append([f"!{name}", False])
            elif kind == 'elsif':
                if stack:
                    stack[-1] = [name, False]
            elif kind == 'else':
                if stack:
                    stack[-1][1] = True
            elif kind == 'endif':
                if stack:
                    stack.pop()
        else:
            m = pat.match(line.rstrip('\n'))
            if m:
                raw = m.group(3).replace('_', '')
                try:
                    value = int(raw, 16) if m.group(2) else int(raw, 10)
                except ValueError:
                    try:
                        value = int(raw, 16)
                    except ValueError:
                        value = None
                if value is not None:
                    sites.append({
                        'start': offset,
                        'end': offset + len(line.rstrip('\n')),
                        'value': value,
                        'indent': m.group(1),
                        'hex': bool(m.group(2)),
                        'branch': tuple(stack[-1]) if stack else None,
                    })
        offset += len(line)
    return sites


def select_define_sites(sites: List[Dict], criteria: Optional[str]) -> List[Dict]:
    """
    Pick the `define sites a patch should rewrite.

    An unconditional patch owns every branch -- the count it grows is true for
    every build. A patch gated on `criteria` owns only the positive arm of that
    same criteria, so an ifdef-only peripheral never disturbs the other build.
    """
    if criteria is None:
        return sites
    return [
        s for s in sites
        if s['branch'] is not None
        and s['branch'][0] == criteria
        and not s['branch'][1]
    ]


