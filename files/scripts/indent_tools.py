#!/usr/bin/env python3
"""
indent_tools.py - unified indentation toolkit for the peripheral automation.

Replaces the former normalize_indent.py, one_time_indent_cleanup.py and
indent_audit.py with one entry point and three subcommands:

  normalize [--check]
      Whole-file indentation normalizer. The legacy board sources mix TAB and
      SPACE indentation, while the peripheral auto-integrator always injects
      SPACE-indented code (2 spaces per structural level). This converts the
      legacy files to the same space-only, 2-space grid so everything aligns.
      Transform (leading whitespace only):
        * A leading run that CONTAINS a tab is re-emitted as spaces: each tab
          counts as 2 columns, each space as 1, and the total is floored to
          the 2-space grid (\\t\\t + 3sp = 7 -> 6). This matches
          IndentationAnalyzer's own tab-sanitising rule.
        * A PURE-SPACE leading run is left byte-for-byte unchanged. This
          protects space-literal anchors (notably fpga_top.v's
          '   wire ip2intc_irpt;').
        * Trailing whitespace is stripped; whitespace-only lines become empty.
        * Inner alignment tabs (e.g. 'code\\t\\t// comment') collapse to a
          single space.
        * Runs of more than 2 consecutive blank lines collapse to 2 (the
          file's own block-separation style). Earlier automation splices had
          stacked up to 4 blanks around the SoC instantiation. Safe for every
          anchor: multi-line anchor patterns match with '\\s*\\n\\s*'.
        * Verilog only: the SoC instantiation port list (the
          'mkDebugSoc core(...)' block, delimited by paren balance) is
          re-indented so every '.port(...)' and '// comment' line sits at the
          block's modal indent.
      BSV/Verilog are whitespace-insensitive, so this never changes behaviour.
      Idempotent: a second run reports 0 changes.

  cleanup [--check]
      One-time surgical repair of indentation defects left by EARLIER
      automation runs (before the centralized INDENT_PROFILE system) plus a
      few legacy per-line defects that `normalize` deliberately cannot touch
      (pure-space leads are protected there). The integrator's
      skip_if_contains idempotency means re-running never rewrites those
      lines, so they are fixed once here. Genuine legacy lines (tab-indented
      imports, method I2C_out declarations, AXI4_Lite_*_Xactor declarations)
      are deliberately NOT touched. fpga_top.v gets only line-targeted fixes
      that can never collide with an automation anchor (the 3-space
      '   wire ip2intc_irpt;' anchor line is not in the fix list).
      Idempotent: a second run reports 0 changes.

  audit --baseline <dir> --current <dir> [files...]
      Verify that lines ADDED by the automation follow the project
      indentation policy (spaces only, 2 per level, no whitespace-only lines,
      else-if bodies indented deeper than their branch). Compares a
      pre-automation baseline directory against the current directory and
      audits only the added/changed lines, so legacy tab-indented lines the
      automation never touched are not flagged. If no files are given, all
      *.bsv files present in both directories are audited. Verilog (*.v)
      files are exempt from the space/even-width rules.
      Exit code 0 = no violations.

Usage (run from hw/):
  python3 scripts/indent_tools.py normalize [--check]
  python3 scripts/indent_tools.py cleanup   [--check]
  python3 scripts/indent_tools.py audit --baseline <dir> --current <dir> [files...]
"""

import argparse
import difflib
import re
import sys
from pathlib import Path

HW = Path(__file__).resolve().parent.parent
TAB_WIDTH = 2
MAX_BLANK_RUN = 2


# =============================================================================
# normalize - whole-file tab->space normalizer
# =============================================================================

NORMALIZE_BSV_FILES = [
    "boards/nexys_video/Soc.bsv",
    "boards/nexys_video/mixed_cluster.bsv",
    "boards/nexys_video/uart_cluster.bsv",
    "boards/nexys_video/pinmux.bsv",
]
NORMALIZE_VERILOG_FILES = [
    "boards/nexys_video/fpga_top.v",
]


def _leading_width(ws: str) -> int:
    return sum(TAB_WIDTH if c == "\t" else 1 for c in ws)


def _normalize_line(line: str) -> str:
    stripped = line.lstrip(" \t")
    lead = line[: len(line) - len(stripped)]
    body = stripped.rstrip()
    if body == "":
        return ""                       # whitespace-only -> empty
    if "\t" in lead:
        w = _leading_width(lead)
        w -= w % 2                       # floor to 2-space grid
        lead = " " * w
    if "\t" in body:
        body = re.sub(r"\t+", " ", body)  # inner alignment tabs -> single space
    return lead + body


def _collapse_blank_runs(lines: list, max_run: int = MAX_BLANK_RUN) -> list:
    """Collapse runs of more than max_run consecutive blank lines to max_run.

    Earlier automation splices stacked blank lines (e.g. 4 blanks between the
    SoC instantiation's ');' and 'assign interrupts' in fpga_top.v). Every
    multi-line anchor pattern matches whitespace with '\\s*\\n\\s*', so
    removing surplus blanks can never break an anchor.
    """
    out, run = [], 0
    for line in lines:
        if line == "":
            run += 1
            if run <= max_run:
                out.append(line)
        else:
            run = 0
            out.append(line)
    return out


# Opener of a multi-line module instantiation whose port list we uniformly
# re-indent. Kept specific (the SoC instance) so other instantiations, which
# legitimately use different indents, are left untouched.
_INST_OPENER = re.compile(r"^\s*mk\w+\s+\w+\s*\(\s*$")


def _reindent_instantiation_ports(lines: list) -> list:
    """Re-indent the SoC instantiation's port list to its modal indent.

    The block runs from the opener line (ending in '(') until parentheses
    re-balance to zero (the closing ');'). Every non-blank body line is set to
    the most common leading indent among the block's '.port' lines.
    """
    out = list(lines)
    for start, line in enumerate(out):
        if not _INST_OPENER.match(line):
            continue

        depth = line.count("(") - line.count(")")
        end = None
        for j in range(start + 1, len(out)):
            depth += out[j].count("(") - out[j].count(")")
            if depth <= 0:
                end = j          # the closing ');' line
                break
        if end is None:
            continue

        body = out[start + 1:end]
        widths = [len(l) - len(l.lstrip(" ")) for l in body
                  if l.lstrip().startswith(".")]
        if not widths:
            continue
        target = " " * max(set(widths), key=widths.count)   # modal indent

        for j in range(start + 1, end):
            stripped = out[j].lstrip(" ")
            if stripped:                                    # keep blanks blank
                out[j] = target + stripped
        break                                               # only the SoC inst

    return out


def _normalize_text(text: str, is_verilog: bool = False) -> str:
    lines = [_normalize_line(l) for l in text.split("\n")]
    lines = _collapse_blank_runs(lines)
    if is_verilog:
        lines = _reindent_instantiation_ports(lines)
    return "\n".join(lines)


def cmd_normalize(check_only: bool) -> int:
    total = 0
    for rel in NORMALIZE_BSV_FILES + NORMALIZE_VERILOG_FILES:
        path = HW / rel
        if not path.exists():
            print(f"[WARN] {rel}: not found, skipping")
            continue
        old = path.read_text()
        new = _normalize_text(old, is_verilog=rel in NORMALIZE_VERILOG_FILES)
        # Count real edits via diff alignment: a removed blank line must not
        # make every shifted-down line after it count as "changed".
        matcher = difflib.SequenceMatcher(None, old.split("\n"), new.split("\n"))
        changed = sum(
            j2 - j1 if tag == "replace" else max(i2 - i1, j2 - j1)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes()
            if tag != "equal"
        )
        delta_lines = old.count("\n") - new.count("\n")
        tag = "HIT " if new != old else "----"
        print(f"[{tag}] {rel}: {changed} line(s) changed, "
              f"{delta_lines} line(s) removed")
        if new != old and not check_only:
            path.write_text(new)
        total += changed
    print("-" * 60)
    print(f"[DONE] {total} line(s) {'would be ' if check_only else ''}normalized. "
          f"(0 on a second run = idempotent)")
    return 0


# =============================================================================
# cleanup - one-time surgical fixes
# =============================================================================

# (file, line-regex with ONE capture group for the code, replacement indent,
#  expected hit count on the pristine baseline)
CLEANUP_FIXES = [
    # --- boards/nexys_video/mixed_cluster.bsv (run AFTER restore) ---
    ("boards/nexys_video/mixed_cluster.bsv",
     r"^\t  (interface Ifc_gptimer_io gptimer[0-3]_io;)$", "    ", 4),
    ("boards/nexys_video/mixed_cluster.bsv",
     r"^\t\t\t(slave_num = `GPTimer0_slave_num;)$", "      ", 1),
    ("boards/nexys_video/mixed_cluster.bsv",
     r"^\t\t  (slave_num = `GPTimer[123]_slave_num;)$", "      ", 3),
    ("boards/nexys_video/mixed_cluster.bsv",
     r"^ {5}(mkConnection \(fabric\.v_to_slaves \[`GPTimer[0-3]_slave_num \],gptimer[0-3]\.slave\);)$", "    ", 4),

    # --- boards/nexys_video/Soc.bsv ---
    ("boards/nexys_video/Soc.bsv",
     r"^\t  (interface Ifc_gptimer_io gptimer0_io;)$", "    ", 1),
    ("boards/nexys_video/Soc.bsv",
     r"^\t  (interface gptimer[123]_io = mixed_cluster\.gptimer[123]_io;)$", "    ", 3),

    # --- boards/nexys_video/fpga_top.v ---
    # Legacy line at 2-space indent in a module body that uses 3 spaces
    # everywhere else. `normalize` cannot fix it (pure-space leads are
    # protected there); this targeted rewrite cannot collide with the
    # 3-space '   wire ip2intc_irpt;' automation anchor.
    ("boards/nexys_video/fpga_top.v",
     r"^  (assign interrupts = 2'b0 ;)$", "   ", 1),
    # One clk_converter port at 4-space indent while every sibling .s_axi_* port
    # in the same instantiation is at 7. normalize's port re-indent only targets
    # the `mkDebugSoc` instance (a `mk\w+` opener), not `clk_converter`.
    ("boards/nexys_video/fpga_top.v",
     r"^    (\.s_axi_aresetn\(~soc_reset\),)$", "       ", 1),

    # --- DebugSoc.bsv (hw root only; no board counterpart) ---
    ("DebugSoc.bsv",
     r"^   (interface soc_sb = interface Ifc_soc_sb)$", "    ", 1),
    ("DebugSoc.bsv",
     r"^   (endinterface;)$", "    ", 1),
    ("DebugSoc.bsv",
     r"^    (interface sbread  =soc\.soc_sb\.sbread;)$", "      ", 1),
    ("DebugSoc.bsv",
     r"^    (method commitlog = soc\.soc_sb\.commitlog;)$", "      ", 1),

    # --- TbSoc.bsv (hw root only) ---
    ("TbSoc.bsv",
     r"^        \t(\$fwrite\(dump, \"core   0: \".*idump\.instruction, \"\)\"\);)$", "          ", 1),
    ("TbSoc.bsv",
     r"^\t     (`ifdef RV32)$", "            ", 1),
    ("TbSoc.bsv",
     r"^\t\t(Bit#\(`xlen\) wdata1 = fn_probe_csr\(`MSTATUSH\);)$", "                  ", 1),
    ("TbSoc.bsv",
     r"^\t\t(\$fwrite\(dump, \" \" , fn_csr_to_str\(`MSTATUSH\), \" 0x%8h\", wdata1\);)$", "                  ", 1),
    ("TbSoc.bsv",
     r"^\t        (end)$", "                ", 1),
    ("TbSoc.bsv",
     r"^      \t     (`endif)$", "            ", 1),
    ("TbSoc.bsv",
     r"^\t\t(end)$", "            ", 1),
    # Whitespace-only lines injected by the old splice logic.
    ("TbSoc.bsv",
     r"^  ()$", "", 2),
]


def cmd_cleanup(check_only: bool) -> int:
    total = 0
    per_file = {}

    for rel, pattern, indent, expected in CLEANUP_FIXES:
        path = HW / rel
        if not path.exists():
            print(f"[WARN] {rel}: file not found, skipping")
            continue

        text = path.read_text()
        rx = re.compile(pattern, re.MULTILINE)
        new_text, hits = rx.subn(lambda m: indent + m.group(1), text)

        tag = ""
        if hits != expected:
            tag = f"  (expected {expected} on pristine baseline)"
        print(f"[{'HIT ' if hits else '----'}] {rel}: {hits} x {pattern[:58]}{tag}")

        if hits and not check_only:
            path.write_text(new_text)
        per_file[rel] = per_file.get(rel, 0) + hits
        total += hits

    print("-" * 60)
    for rel, n in per_file.items():
        print(f"  {rel}: {n} line(s) fixed")
    print(f"[DONE] {total} line(s) {'would be ' if check_only else ''}fixed. "
          f"(0 on a second run = idempotent, as intended)")
    return 0


# =============================================================================
# audit - policy audit of automation-added lines
# =============================================================================

def _added_lines(baseline_text: str, current_text: str):
    """
    Yield (line_number_in_current, line, replaced_leads) for lines added vs
    the baseline. replaced_leads is the set of leading-whitespace strings of
    the baseline lines this block replaced -- an added line whose indent
    matches one of them is an in-place edit of a legacy line (e.g. the PLIC
    vector width rewrite) and keeps that line's own indentation by design.
    """
    base = baseline_text.splitlines()
    cur = current_text.splitlines()
    matcher = difflib.SequenceMatcher(None, base, cur)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            replaced_leads = {
                re.match(r"^[ \t]*", base[i]).group(0)
                for i in range(i1, i2)
            } if tag == "replace" else set()
            for j in range(j1, j2):
                yield j + 1, cur[j], replaced_leads


def _audit_file(rel: str, baseline: Path, current: Path) -> list:
    violations = []
    base_file = baseline / rel
    cur_file = current / rel
    if not base_file.exists() or not cur_file.exists():
        return [(rel, 0, f"missing file for comparison ({rel})")]

    cur_text = cur_file.read_text()
    cur_all = cur_text.splitlines()
    added = list(_added_lines(base_file.read_text(), cur_text))

    is_bsv = rel.endswith(".bsv")

    for lineno, line, replaced_leads in added:
        if line and line.strip() == "":
            violations.append((rel, lineno, "whitespace-only line"))
            continue
        if not line:
            continue

        lead = re.match(r"^[ \t]*", line).group(0)
        in_place_edit = lead in replaced_leads

        if is_bsv and "\t" in lead and not in_place_edit:
            violations.append((rel, lineno, f"tab in leading whitespace: {line[:50]!r}"))
        elif is_bsv and len(lead) % 2 != 0 and not in_place_edit:
            violations.append((rel, lineno, f"odd indent width {len(lead)}: {line.strip()[:50]!r}"))

        if is_bsv and re.match(r"^else\s+if\b", line.strip()):
            # The next non-blank line must sit deeper than the else-if.
            for nxt in cur_all[lineno:]:
                if not nxt.strip():
                    continue
                nxt_lead = re.match(r"^[ \t]*", nxt).group(0)
                if len(nxt_lead.expandtabs(2)) <= len(lead.expandtabs(2)) and \
                        not nxt.strip().startswith(("else", "end")):
                    violations.append(
                        (rel, lineno, "else-if body not indented deeper than branch"))
                break

    return violations


def cmd_audit(baseline: Path, current: Path, files: list) -> int:
    if files:
        rels = files
    else:
        rels = sorted(
            p.name for p in baseline.glob("*.bsv")
            if (current / p.name).exists()
        )

    all_violations = []
    for rel in rels:
        v = _audit_file(rel, baseline, current)
        status = "FAIL" if v else "PASS"
        print(f"[{status}] {rel}: {len(v)} violation(s)")
        all_violations.extend(v)

    for rel, lineno, msg in all_violations:
        print(f"  {rel}:{lineno}: {msg}")

    print(f"[{'FAIL' if all_violations else 'PASS'}] total: {len(all_violations)} violation(s)")
    return 1 if all_violations else 0


# =============================================================================
# CLI
# =============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Unified indentation toolkit (normalize / cleanup / audit)")
    sub = ap.add_subparsers(dest="command", required=True)

    p_norm = sub.add_parser("normalize", help="whole-file tab->space normalizer")
    p_norm.add_argument("--check", action="store_true",
                        help="report without writing")

    p_clean = sub.add_parser("cleanup", help="one-time surgical indent fixes")
    p_clean.add_argument("--check", action="store_true",
                         help="report hits without writing")

    p_audit = sub.add_parser("audit", help="audit automation-added lines")
    p_audit.add_argument("--baseline", required=True, type=Path)
    p_audit.add_argument("--current", required=True, type=Path)
    p_audit.add_argument("files", nargs="*",
                         help="relative paths; default: all *.bsv in both dirs")

    args = ap.parse_args()

    if args.command == "normalize":
        return cmd_normalize(args.check)
    if args.command == "cleanup":
        return cmd_cleanup(args.check)
    return cmd_audit(args.baseline, args.current, args.files)


if __name__ == "__main__":
    sys.exit(main())
