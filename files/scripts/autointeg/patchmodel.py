#!/usr/bin/env python3
"""Patch primitives: instance context, phase classification, comment stripping, gating."""

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

from .registry import CONTEXT_GENERATORS
from .common import (
    log_pass, log_fail, log_warn, log_info, log_debug, set_verbose,
    load_yaml, resolve_template, find_unresolved_placeholders,
    ContextGenerator, IndentationAnalyzer, INDENT_PROFILE,
)


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


# =============================================================================
# CONDITIONAL COMPILATION (`ifdef) SUPPORT
# =============================================================================

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

