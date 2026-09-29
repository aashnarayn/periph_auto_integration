#!/usr/bin/env python3
"""
utils.py - Core utilities and base classes for Shakti Peripheral Auto-Integrator.

Centralized to prevent circular imports and code duplication.
Contains generic, reusable utilities for the entire framework.
"""

import re
import yaml
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from collections import Counter

# =============================================================================
# LOGGING UTILITIES
# =============================================================================

# ANSI color codes for terminal output
RED, GREEN, YELLOW, BLUE, NC = '\033[0;31m', '\033[0;32m', '\033[1;33m', '\033[0;34m', '\033[0m'

# Global verbose flag for debug logging
_VERBOSE: bool = False


def set_verbose(state: bool) -> None:
    """Enable or disable debug-level logging globally."""
    global _VERBOSE
    _VERBOSE = state


def log_pass(msg: str) -> bool:
    """Log a successful check with green [PASS] prefix."""
    print(f"{GREEN}[PASS]{NC} {msg}")
    return True


def log_fail(msg: str) -> bool:
    """Log a failed check with red [FAIL] prefix."""
    print(f"{RED}[FAIL]{NC} {msg}")
    return False


def log_warn(msg: str) -> bool:
    """Log a warning with yellow [WARN] prefix."""
    print(f"{YELLOW}[WARN]{NC} {msg}")
    return True


def log_info(msg: str) -> None:
    """Log an informational message with blue [INFO] prefix."""
    print(f"{BLUE}[INFO]{NC} {msg}")


def log_debug(msg: str) -> None:
    """Log a debug message (only if verbose mode is enabled)."""
    if _VERBOSE:
        print(f"{BLUE}[DEBUG]{NC} {msg}")


# =============================================================================
# GENERIC YAML & TEMPLATE HELPERS
# =============================================================================

def load_yaml(yaml_path: str) -> Dict:
    """
    Safely load a YAML configuration file.
    
    Args:
        yaml_path: Path to the YAML file.
        
    Returns:
        Parsed YAML content as a dictionary.
        
    Raises:
        SystemExit: If file not found or YAML parse error.
    """
    try:
        with open(yaml_path, 'r') as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        log_fail(f"Config not found: {yaml_path}")
        exit(1)
    except yaml.YAMLError as e:
        log_fail(f"YAML parse error in {yaml_path}: {e}")
        exit(1)


def resolve_template(template: str, context: Dict) -> str:
    """
    Safely resolve {placeholders} in a string using a context dictionary.
    
    Supports up to 2 levels of nested placeholder resolution.
    
    Args:
        template: String containing {placeholder} tokens.
        context: Dictionary mapping placeholder names to values.
        
    Returns:
        String with placeholders resolved.
    """
    if not isinstance(template, str):
        return str(template)
    
    resolved = template
    for _ in range(2):  # Support 2 levels of nesting
        if '{' not in resolved:
            break
        try:
            resolved = resolved.format(**context)
        except (KeyError, ValueError, IndexError):
            # Unresolvable placeholder or literal braces (e.g. a regex
            # containing \{ ) -- return the string as-is.
            break
    return resolved


def find_unresolved_placeholders(template: str, context: Dict) -> List[str]:
    """
    Find {placeholders} in a template that cannot be resolved by the given context.
    
    Args:
        template: String containing {placeholder} tokens.
        context: Dictionary of available placeholder values.
        
    Returns:
        List of unresolved placeholder names.
    """
    return [
        m.group(1) 
        for m in re.finditer(r'\{(\w+)\}', template) 
        if m.group(1) not in context
    ]


# =============================================================================
# ABSTRACT BASE CLASSES
# =============================================================================

class ContextGenerator(ABC):
    """
    Abstract base class for peripheral-specific context generation.
    
    All implementations must be pure functions: no file I/O, no side effects.
    """
    
    @abstractmethod
    def generate(self, base_ctx: Dict, instance: Dict, peripheral_name: str) -> Dict:
        """Generate instance-specific context fields dynamically."""
        pass
    
    @abstractmethod  
    def get_required_keys(self) -> List[str]:
        """List of keys that MUST exist in base_ctx or instance."""
        pass
    
    @abstractmethod
    def get_generated_keys(self) -> List[str]:
        """List of keys that this generator can auto-generate (used for migration)."""
        pass
    
    def needs_migration(self, instance: Dict) -> bool:
        """Check if an instance contains generated keys that can be stripped."""
        return any(k in instance for k in self.get_generated_keys())


# =============================================================================
# CODE STYLE / INDENTATION ANALYZER
# =============================================================================

# Project-wide indentation policy: injected code always uses spaces,
# 2 per structural level. Legacy tab-indented lines in the target files
# are never rewritten, but they must not contaminate new insertions.
INDENT_UNIT = "  "   # 2 spaces per level
TAB_EQUIV = 2        # a tab in an *observed* legacy indent counts as one level

# Central spacing store. Anchors/patches may reference these by name via an
# `indent_context:` key in the IP YAML; when present, the profile value wins
# over any indentation observed at the anchor site.
#
# Verilog (fpga_top.v) contexts are deliberately absent: those files are
# pure-space indented already and auto-detection passes their style through.
INDENT_PROFILE: Dict[str, str] = {
    # BSV contexts (package body starts at one level = 2 spaces)
    "package_body":   INDENT_UNIT * 1,  # imports, (*synthesize*), module/interface headers
    "interface_body": INDENT_UNIT * 2,  # members of an interface declaration
    "module_body":    INDENT_UNIT * 2,  # lets, mkConnection, Wire decls, method headers
    "function_body":  INDENT_UNIT * 2,  # statements in package-level functions (else-if chains)
    "method_body":    INDENT_UNIT * 3,  # statements inside method bodies
    "rule_body":      INDENT_UNIT * 3,  # statements inside rule bodies
    "branch_body":    INDENT_UNIT * 3,  # statement under an if/else-if with no begin/end
    "mux_option":     INDENT_UNIT * 3,  # pinmux ternary-chain continuation lines
    # pinmux.bsv uses a deeper module body than the cluster files:
    "pinmux_module_body":    INDENT_UNIT * 3,
    "pinmux_interface_body": INDENT_UNIT * 6,  # PeripheralSide member declarations
}


class IndentationAnalyzer:
    """
    Generic indentation/style analyzer.

    Responsible for:
      - determining indentation from matched text
      - preserving relative indentation
      - preserving surrounding blank lines
      - formatting replacement blocks

    No regex logic should exist here.
    """

    @staticmethod
    def observe_style(content: str, start: int, end: int) -> Dict[str, Any]:
        """
        Observe formatting around a regex match.

        Important:
          - indentation is derived from the first non-empty matched line
          - boundary blank lines are measured outside the matched block
          - no newline characters are ever included in indentation
        """

        start = max(0, min(start, len(content)))
        end = max(start, min(end, len(content)))

        # Physical line boundaries containing the match.
        start_line_begin = content.rfind("\n", 0, start) + 1

        end_line_end = content.find("\n", end)
        if end_line_end == -1:
            end_line_end = len(content)

        matched_region = content[start_line_begin:end_line_end]
        matched_lines = matched_region.splitlines()

        # Use the first non-empty line belonging to the matched region.
        # This is more robust for multiline anchors than indexing the
        # complete file with start_line/end_line counters.
        base_indent = ""

        for line in matched_lines:
            if line.strip():
                m = re.match(r"^[ \t]*", line)
                base_indent = m.group(0) if m else ""
                break

        # Count blank physical lines immediately BEFORE the matched region.
        newline_before = 0
        prefix = content[:start_line_begin]
        prefix_lines = prefix.splitlines()

        i = len(prefix_lines) - 1
        while i >= 0 and prefix_lines[i].strip() == "":
            newline_before += 1
            i -= 1

        # Count blank physical lines immediately AFTER the matched region.
        newline_after = 0

        suffix_start = end_line_end
        if suffix_start < len(content) and content[suffix_start] == "\n":
            suffix_start += 1

        suffix_lines = content[suffix_start:].splitlines()

        i = 0
        while i < len(suffix_lines) and suffix_lines[i].strip() == "":
            newline_after += 1
            i += 1

        return {
            "base_indent": base_indent,
            "newline_before": newline_before,
            "newline_after": newline_after,
        }

    @staticmethod
    def resolve_injection_indent(
        content: str,
        match_start: int,
        match_end: int,
        code: str,
        position: str,
        observed_indent: str
    ) -> str:
        """
        Resolve indentation for injected code.

        Important case:
        If an inserted block starts with a sibling/control-chain keyword such
        as 'else', 'elif', 'catch', etc., use the indentation of the next
        non-empty source line when that line begins with the same structural
        keyword family.

        Example:
            else if (...)          <- existing parent
              slave_num = ...;     <- matched anchor
            else                   <- next source line

        Injecting:
            else if (...)

        must align with the next 'else', not with the matched slave_num line.
        """

        stripped_code = code.lstrip()
        if not stripped_code:
            return observed_indent

        first_line = stripped_code.splitlines()[0].strip()

        sibling_prefixes = (
            "else",
            "elif",
            "catch",
            "finally",
            "case ",
            "default:",
        )

        is_sibling_block = first_line.startswith(sibling_prefixes)

        if position == "after" and is_sibling_block:
            after_text = content[match_end:]

            for line in after_text.splitlines():
                if not line.strip():
                    continue

                next_stripped = line.strip()

                # Only borrow indentation from a structurally compatible
                # following sibling line.
                if next_stripped.startswith(sibling_prefixes):
                    m = re.match(r"^[ \t]*", line)
                    if m:
                        return m.group(0)

                break

        return observed_indent

    @staticmethod
    def sanitize_indent(indent: str) -> str:
        """
        Convert a tab-contaminated indent observed at an anchor site into the
        project's space-only convention.

        Pure-space indents are returned unchanged (this preserves Verilog
        styles such as 3-space wire declarations in fpga_top.v). Any indent
        containing a tab is converted: each tab counts as TAB_EQUIV spaces,
        and the resulting width is floored to an even number so it lands on
        the 2-space grid.
        """

        if "\t" not in indent:
            return indent

        width = TAB_EQUIV * indent.count("\t") + indent.count(" ")
        width -= width % len(INDENT_UNIT)

        return " " * width

    @staticmethod
    def resolve_target_indent(
        content: str,
        match_start: int,
        match_end: int,
        code: str,
        position: str,
        observed_indent: str,
        context: Optional[str] = None,
    ) -> str:
        """
        Single entry point used by handlers to decide the base indentation
        of an injected block.

        Precedence:
          1. Explicit `indent_context` naming an INDENT_PROFILE entry.
          2. Sibling-borrow (else-if chains etc.) via resolve_injection_indent.
          3. The indent observed at the anchor site.

        Whatever wins from (2)/(3) is sanitized so legacy tab indentation
        can never leak into new code.
        """

        if context:
            if context in INDENT_PROFILE:
                return INDENT_PROFILE[context]
            log_warn(
                f"Unknown indent_context '{context}' "
                f"(known: {', '.join(sorted(INDENT_PROFILE))}); "
                f"falling back to auto-detection"
            )

        resolved = IndentationAnalyzer.resolve_injection_indent(
            content, match_start, match_end, code, position, observed_indent
        )

        return IndentationAnalyzer.sanitize_indent(resolved)

    @staticmethod
    def apply_indentation(code: str, indent: str) -> str:
        """
        Apply target indentation exactly once while preserving
        relative indentation inside the YAML patch.

        The YAML block's own common leading indentation is removed first.
        """

        if code is None:
            return ""

        # Remove only boundary newlines.
        # Do not use strip(), because meaningful internal spaces should survive.
        code = str(code).strip("\n")

        if not code.strip():
            return ""

        # Normalize any leading tabs the YAML author used to the space unit
        # so relative depth is measured on one consistent scale.
        def expand_leading(line: str) -> str:
            m = re.match(r"^[ \t]*", line)
            leading = m.group(0) if m else ""
            expanded = leading.replace("\t", " " * TAB_EQUIV)
            return expanded + line[len(leading):]

        lines = [expand_leading(line) for line in code.splitlines()]

        # Find common leading whitespace across non-empty lines.
        non_empty = [line for line in lines if line.strip()]

        if not non_empty:
            return ""

        leading_widths = []

        for line in non_empty:
            m = re.match(r"^ *", line)
            leading = m.group(0) if m else ""
            leading_widths.append(len(leading))

        common_indent = min(leading_widths)

        unit = len(INDENT_UNIT)
        formatted = []

        for line in lines:
            if not line.strip():
                # Blank lines inside a patch stay truly empty --
                # never emit whitespace-only lines.
                formatted.append("")
                continue

            # Remove YAML patch's common indentation exactly once,
            # quantizing the remaining relative depth to the 2-space grid.
            relative = line[common_indent:]
            m = re.match(r"^ *", relative)
            rel_width = len(m.group(0)) if m else 0
            rel_width -= rel_width % unit

            # Apply target-file indentation exactly once; no trailing
            # whitespace can ever be emitted.
            formatted.append((indent + " " * rel_width + relative.lstrip()).rstrip())

        return "\n".join(formatted)

    @staticmethod
    def get_defines_spacing(content: str, anchor_pos: int) -> str:
        """
        Backward compatibility with existing ClusterConfigHandler.
        """

        lines = content.split('\n')
        anchor_idx = content[:anchor_pos].count('\n')

        for i in range(max(0, anchor_idx - 10),
                       min(len(lines), anchor_idx + 11)):
            if lines[i].strip().startswith('`define'):
                match = re.match(r'^`define(\s+)', lines[i])
                if match:
                    return match.group(1)

        return " "

    @staticmethod
    def apply_defines_indent(code: str, spacing: str) -> str:
        """
        Backward compatibility for .defines generation.
        """

        lines = code.strip().split("\n")

        result = []

        for line in lines:

            clean = line.strip()

            if not clean:
                continue

            if clean.startswith("`define"):
                result.append(clean)
            else:
                result.append("`define" + spacing + clean)

        return "\n".join(result) + "\n"