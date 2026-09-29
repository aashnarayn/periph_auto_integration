#!/usr/bin/env python3
"""Patch handlers: regex, PLIC vector, cluster config, XDC pin assignment."""

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
from .conditional import (extract_define_value, find_define_sites,
                          select_define_sites, wrap_in_ifdef)
from .patchmodel import strip_comments
from .tracker import ResourceTracker


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
                    
        # === UP-14: conditional compilation ===
        # Wrap this patch's whole emitted block in `ifdef <criteria> / `endif so
        # the IP is compiled in only for builds defining that macro. Wrapping
        # per patch (rather than one block per file) keeps every existing skip
        # guard working unchanged -- the injected text is still literally
        # present -- and keeps re-runs idempotent.
        # bsvpath is a plain path list with no preprocessor: an unused entry is
        # harmless, so it is left unwrapped.
        ifdef_criteria = base_ctx.get('__ifdef__')
        if ifdef_criteria and inj.strip() and not filepath.endswith('bsvpath'):
            inj = wrap_in_ifdef(inj, ifdef_criteria)

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

        # === UP-13 / UP-14: conditional-aware counter rewrite ===
        # A counter macro may be defined more than once under `ifdef (the
        # baseline writes `Num_Fast_Slaves` 7 for simulate, 6 otherwise). Read
        # and rewrite the site(s) this patch actually owns, never blindly the
        # first one: an unconditional patch owns every branch, an ifdef-gated
        # patch owns only its own arm.
        criteria = base_ctx.get('__ifdef__')
        sites = find_define_sites(content, macro) if macro else []
        owned = select_define_sites(sites, criteria)

        if macro and criteria and sites and not owned:
            # The macro exists but has no arm for this criteria yet: this patch
            # must CREATE the conditional, preserving the other build's value.
            owned = []

        # Calculate new value: increment ONLY by the new instances being added
        current_val = None
        if patch.get('increment_by_instances'):
            if owned:
                current_val = owned[0]['value']
                if len({s['value'] for s in owned}) > 1 and '{allocated_slot}' in patch.get('code', ''):
                    log_fail(
                        f"[HANDLER] ClusterConfig: `{macro}` holds different values per "
                        f"`ifdef branch {sorted({s['value'] for s in owned})}, so "
                        f"{{allocated_slot}} is ambiguous. List ifdef-gated peripherals "
                        f"LAST in soc_build_config.yaml."
                    )
                    return False
            else:
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

        # === UP-13 / UP-14: write to the site(s) this patch owns ===
        # Default (single, unconditional define) keeps the original splice.
        if macro and len(owned) > 1:
            # Several branches to grow: rewrite each from ITS OWN current value,
            # last-first so earlier offsets stay valid.
            body = patch['code'].splitlines()[-1]
            for site in sorted(owned, key=lambda x: x['start'], reverse=True):
                line = site['indent'] + resolve_template(
                    body, {'calculated_val': str(site['value'] + len(delta))}
                ).lstrip()
                content = content[:site['start']] + line + content[site['end']:]
            vfs[filepath] = content
        elif macro and criteria and not owned and sites:
            # Ifdef-gated patch meeting an unconditional define: split it into
            # `ifdef <criteria> <new> `else <old> `endif so the other build is
            # left exactly as it was.
            if len([l for l in patch['code'].splitlines() if l.strip()]) > 1:
                log_fail(
                    f"[HANDLER] ClusterConfig: ifdef-gated counter for `{macro}` "
                    f"must emit exactly one line (the counter). Move any "
                    f"per-instance defines into their own regex patch, as "
                    f"gptimer_v1 does with soc_defines_family_slot."
                )
                return False
            site = sites[0]
            ind = site['indent']
            body = patch['code'].splitlines()[-1]
            grown = resolve_template(body, {'calculated_val': str(site['value'] + len(delta))}).lstrip()
            kept = content[site['start']:site['end']].strip()
            block = (
                f"{ind}`ifdef {criteria}\n"
                f"{ind}  {grown}\n"
                f"{ind}`else\n"
                f"{ind}  {kept}\n"
                f"{ind}`endif"
            )
            vfs[filepath] = content[:site['start']] + block + content[site['end']:]
        else:
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
                # Leading whitespace is allowed: a `define inside an `ifdef
                # branch is indented (see find_define_sites, which has the same
                # allowance on the apply path). Anchoring at column 0 made the
                # audit blind to every ifdef-gated peripheral.
                pattern = rf"^[ \t]*`define\s+{re.escape(macro)}\s+{re.escape(str(val))}"
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
            # Same leading-whitespace allowance as above.
            pattern = rf"^[ \t]*`define\s+{re.escape(macro)}\s+\S+"
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
        
        if base_ctx.get('__ifdef__'):
            log_fail(
                "[HANDLER] XDCPinAssign: constraints.xdc is TCL and has no "
                "`ifdef, so an ifdef-gated peripheral cannot own pin "
                "constraints. Remove the xdc_pin_assign patches, or drop the "
                "peripheral's <criteria>_ifdef flag."
            )
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

