#!/usr/bin/env python3
"""
xdc_analyzer.py - VFS-Aware Smart Linker & Electrical Rules Checker

Production-ready XDC parser with FULL V1 edge-case parity + V2 electrical validation.
Operates entirely on in-memory strings (VFS-subservient).

Output Structure per pin (6-element list):
    [pkgs_list, assigned_port, templates_list, header, properties_list, iostandard]
"""

import re
import yaml
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# Project utilities
from utils import log_pass, log_fail, log_warn, log_info


class BoardSupportAnalyzer:
    """
    Centralized XDC/Board State Analyzer.
    
    Parses master and active XDC files to build a pin map with electrical metadata.
    Supports conflict resolution, header grouping preservation, and IOSTANDARD validation.
    
    Attributes:
        pin_map: Dict[schematic_name, [pkgs, assigned_port, templates, header, properties, iostandard]]
        physical_pin_io: Dict[package_pin, iostandard] - electrical capabilities from master XDC
        nets: Dict[net_name, [sch_name, port_name, lines, properties]]
        misc: List[str] - clock/group directives not tied to specific pins
        xdc_headers: List[str] - ordered list of ## header groups
        xdc_blocks: Dict[header, List[str]] - constraint lines per header
        header_voltages: Dict[header, str] - IOSTANDARD per header group
    """
    
    # Net suffixes to strip for base port name resolution
    _NET_SUFFIXES: Tuple[str, ...] = (
        '_IBUF', '_OBUF', '_IBUFG', '_OBUFT', 
        '_IN', '_OUT', '_CLK', '_CLK_N', '_CLK_P'
    )
    
    # Keywords that indicate metadata comments (not header separators)
    _METADATA_KEYWORDS: Tuple[str, ...] = (
        'this file', 'to use it', 'uncomment', 'rename', 'for the'
    )
    
    def __init__(self) -> None:
        """Initialize empty board state containers."""
        self.pin_map: Dict[str, List] = {}
        self.physical_pin_io: Dict[str, str] = {}
        self.nets: Dict[str, List] = {}
        self.misc: List[str] = []
        self.xdc_headers: List[str] = []
        self.xdc_blocks: Dict[str, List[str]] = {}
        self.header_voltages: Dict[str, str] = {}
    
    def analyze(
        self, 
        vfs: Dict[str, str], 
        master_xdc_path: Optional[str] = None, 
        active_xdc_path: Optional[str] = None
    ) -> None:
        """
        Parse XDC files and populate internal state.
        
        Args:
            vfs: Virtual file system (path → content).
            master_xdc_path: Path to master constraints file (template source).
            active_xdc_path: Path to active constraints file (runtime state).
        """
        master_content = self._resolve_vfs_or_disk(vfs, master_xdc_path)
        if master_content:
            self._parse_master_xdc(master_content, master_xdc_path)
        
        active_content = self._resolve_vfs_or_disk(vfs, active_xdc_path)
        if active_content:
            self._parse_active_constraints(active_content)
            self._parse_xdc_structure(active_content)
    
    def get_pin_map(self) -> Dict[str, List]:
        """Return the current pin map state."""
        return self.pin_map
    
    def get_physical_pin_io(self) -> Dict[str, str]:
        """Return physical pin → IOSTANDARD mapping from master XDC."""
        return self.physical_pin_io
    
    def validate_pin(self, pin: str, requested_standard: Optional[str] = None) -> Tuple[bool, List[str]]:
        """
        Validate electrical compatibility of a pin assignment.
        
        Args:
            pin: Schematic pin name (e.g., 'ja[10]').
            requested_standard: IOSTANDARD requested by peripheral (e.g., 'LVCMOS33').
            
        Returns:
            Tuple[is_valid: bool, issues: List[str]]
        """
        issues: List[str] = []
        
        if pin in self.physical_pin_io:
            allowed_std = self.physical_pin_io[pin]
            if requested_standard and requested_standard != allowed_std:
                issues.append(
                    f"Pin {pin} requires {allowed_std}, but peripheral requested {requested_standard}"
                )
                return False, issues
        
        return True, issues
    
    def apply_patch(
        self, 
        port_name: str, 
        target_pin: str, 
        fallback_pin: str, 
        pin_map: Dict[str, List], 
        dry_run: bool = False,
        io_standard=None,
    ) -> Tuple[bool, str]:
        """
        Apply XDC constraint patch with conflict resolution.
        
        If target pin is already assigned:
            1. Move original port to fallback pin
            2. Assign new port to target pin
            3. Preserve header grouping and IOSTANDARD
        
        Args:
            port_name: New logical port name to assign.
            target_pin: Target schematic pin (e.g., 'ja[10]').
            fallback_pin: Fallback schematic pin if target is occupied.
            pin_map: Current pin map state (modified in-place).
            dry_run: If True, simulate without modifying internal state.
            
        Returns:
            Tuple[success: bool, rendered_xdc: str]
        """
        target_info = pin_map.get(target_pin)
        if not target_info:
            log_fail(f"Target pin '{target_pin}' not in pin map")
            return False, self._render_xdc()
        
        target_header = self._resolve_header(
            target_info[3] if len(target_info) > 3 else "__global__"
        )
        old_port = target_info[1]
        
        # === CONFLICT RESOLUTION: Target pin already assigned ===
        if old_port and old_port != port_name:
            if not fallback_pin:
                log_fail(f"Conflict: {target_pin} assigned to '{old_port}'")
                return False, self._render_xdc()
            
            fallback_info = pin_map.get(fallback_pin)
            if not fallback_info or fallback_info[1]:
                log_fail(f"Fallback {fallback_pin} occupied or missing")
                return False, self._render_xdc()
            
            fallback_header = self._resolve_header(
                fallback_info[3] if len(fallback_info) > 3 else "__global__"
            )
            
            # Ensure both headers exist in blocks
            for h in [target_header, fallback_header]:
                if h not in self.xdc_blocks:
                    self.xdc_headers.append(h)
                    self.xdc_blocks[h] = []
            
            # Process target header: replace old port, move extra properties
            new_lines: List[str] = []
            moved_lines: List[str] = []  # <-- Fixed: was 'moved'
            port_pattern = re.compile(
                r'get_ports\s+\{?\s*' + re.escape(old_port) + r'\s*\}?'
            )
            
            for line in self.xdc_blocks[target_header]:
                if port_pattern.search(line):
                    if 'PACKAGE_PIN' in line:
                        # Main constraint: swap port name in-place
                        new_line = re.sub(
                            r'(get_ports\s+\{?)\s*' + re.escape(old_port) + r'(\}?)',
                            f'\\1{port_name}\\2',
                            line
                        )
                        new_lines.append(new_line)
                    else:
                        # Extra property (PULLDOWN, etc.): move to fallback
                        moved_lines.append(line)
                else:
                    new_lines.append(line)
            
            # Reorder: main constraint first, then other properties
            main_constraints = [l for l in new_lines if 'PACKAGE_PIN' in l]
            other_properties = [l for l in new_lines if 'PACKAGE_PIN' not in l]
            self.xdc_blocks[target_header] = main_constraints + other_properties
            
            # Generate fallback constraints: main constraint first, then moved properties
            templates = fallback_info[2] if len(fallback_info) > 2 else []
            fallback_voltage = self.header_voltages.get(
                fallback_header,
                "LVCMOS33"
            )
            fb_main_constraints = [
                self._render_template(t, old_port, fallback_header, fallback_voltage,) 
                for t in templates
            ]
            self.xdc_blocks[fallback_header].extend(fb_main_constraints + moved_lines)  # <-- Fixed: was 'moved'
            
            # Update pin map state
            pin_map[fallback_pin][1] = old_port
            if len(target_info) > 4 and target_info[4]:
                if len(fallback_info) < 6:
                    fallback_info.append([])
                pin_map[fallback_pin][4] = target_info[4][:]
                target_info[4] = []
            target_info[1] = port_name
            
            if not dry_run:
                log_info(
                    f"  [INFO] XDC Conflict: Moved {old_port} -> {fallback_pin}, "
                    f"Assigned {port_name} -> {target_pin}"
                )
        
        # === IDEMPOTENT OR NEW ASSIGNMENT ===
        else:
            if old_port == port_name:
                return True, self._render_xdc()
            
            if target_header not in self.xdc_blocks:
                self.xdc_headers.append(target_header)
                self.xdc_blocks[target_header] = []
            
            if io_standard:
                voltage = io_standard.upper()
            else:
                voltage = self.header_voltages.get(target_header, "LVCMOS33")
            templates = target_info[2] if len(target_info) > 2 else []
            new_lines = [
                self._render_template(t, port_name, target_header, voltage) 
                for t in templates
            ]
            self.xdc_blocks[target_header].extend(new_lines)
            target_info[1] = port_name
            
            if not dry_run:
                log_info(f"  [INFO] XDC Assigned {port_name} -> {target_pin}")
        
        return True, self._render_xdc()
        
    
    def export_pin_map(
        self, 
        path: str,
        master_xdc_path: Optional[str] = None,
        active_xdc_path: Optional[str] = None,
        board_name: Optional[str] = None,
        fpga_part: Optional[str] = None
    ) -> None:
        """
        Export pin_map.yaml with enhanced metadata.
        
        Args:
            path: Output file path.
            master_xdc_path: Path to master constraints file.
            active_xdc_path: Path to active constraints file.
            board_name: Board identifier (e.g., 'nexys_video').
            fpga_part: FPGA part number (e.g., 'xc7a200tsbg484-1').
        """
        try:
            result = {
                "meta": {
                    "xdc_source": Path(path).name if path else "unknown",
                    "description": "Auto-generated. Master templates + active state tracking.",
                    "master_xdc_path": master_xdc_path,
                    "active_xdc_path": active_xdc_path,
                    "board_name": board_name,
                    "fpga_part": fpga_part,
                },
                "pins": self.pin_map,
                "nets": self.nets,
                "miscellaneous": self.misc
            }
            
            # Preserve existing meta fields if present
            try:
                with open(path, 'r') as f:
                    existing = yaml.safe_load(f)
                if existing and "meta" in existing:
                    result["meta"].update(existing["meta"])
            except Exception:
                pass  # Ignore read errors; proceed with new content
            
            with open(path, 'w') as f:
                yaml.dump(result, f, default_flow_style=False, sort_keys=False)
            
            log_info(f"State saved: {path} updated")
            
        except Exception as e:
            log_fail(f"Failed to save pin_map.yaml: {e}")
    
    def _resolve_vfs_or_disk(self, vfs: Dict, path: Optional[str]) -> str:
        """
        Resolve file content from VFS or disk.
        
        Args:
            vfs: Virtual file system.
            path: File path to resolve.
            
        Returns:
            File content as string, or empty string if not found.
        """
        if path and path in vfs:
            return vfs[path]
        if path and Path(path).exists():
            with open(path, 'r') as f:
                return f.read()
        return ""
    
    def _render_template(self, template: str, port: str, target_header: str, io_standard: str = None,) -> str:
        """
        Render XDC constraint template while handling spacing variations and preserving IOSTANDARD.
        
        Does NOT modify IOSTANDARD — validation happens separately via validate_pin().
        Handles both {PORT} and { PORT } spacing patterns.
        
        Args:
            template: Constraint template with {PORT} placeholder.
            port: Actual port name to inject.
            target_header: XDC header group for IOSTANDARD lookup (unused here).
            
        Returns:
            Rendered constraint line with newline.
        """
        # Detect spacing pattern: {PORT}, { PORT }, or {PORT }
        # Handle {{PORT}} templates first
        if "{{PORT}}" in template:
            line = template.replace("{{PORT}}", "{" + port + "}")

        # Handle {PORT} and { PORT }
        else:
            match = re.search(r'\{\s*PORT\s*\}', template)
            if match:
                inner = template[match.start()+1:match.end()-1]
                line = (
                    template[:match.start()]
                    + "{"
                    + inner.replace("PORT", port)
                    + "}"
                    + template[match.end():]
                )
            else:
                line = template.replace("{PORT}", port)
        
        if io_standard:
            line = re.sub(
                r'IOSTANDARD\s+[A-Za-z0-9_]+',
                f'IOSTANDARD {io_standard}',
                line,
                flags=re.IGNORECASE,
            )
        # DO NOT replace IOSTANDARD here — validation happens separately
        # target_header is reserved for future header-specific logic if needed
        return line + '\n'
    
    @staticmethod
    def _strip_net_suffix(net_name: str) -> str:
        """
        Strip common FPGA buffer suffixes to resolve base port name.
        
        Args:
            net_name: Net name with potential suffix (e.g., 'sys_clk_IBUF').
            
        Returns:
            Base name without suffix (e.g., 'sys_clk').
        """
        for suffix in BoardSupportAnalyzer._NET_SUFFIXES:
            if net_name.endswith(suffix):
                return net_name[:-len(suffix)]
        return net_name
    
    def _parse_master_xdc(self, content: str, source_path: Optional[str] = None) -> None:
        """
        Parse master XDC to extract pin templates and electrical metadata.
        
        Args:
            content: XDC file content.
            source_path: Optional: path for error reporting.
        """
        current_header = "__global__"
        blank_lines = 0
        pins_found = 0
        debug_samples: List[str] = []
        
        for line in content.splitlines():
            stripped = line.strip()
            
            # Track blank lines for header reset logic
            if not stripped:
                blank_lines += 1
                if blank_lines >= 2:
                    current_header = "__global__"
                continue
            blank_lines = 0
            
            # Distinguish comments: headers vs metadata
            is_comment = stripped.startswith('#')
            is_constraint = any(
                kw in stripped.lower() 
                for kw in ['set_property', 'sch=', 'package_pin', 'create_clock']
            )
            
            if is_comment and not is_constraint:
                if any(kw in stripped.lower() for kw in self._METADATA_KEYWORDS):
                    continue
                current_header = stripped
                continue
            
            # Parse constraint lines with Sch= and PACKAGE_PIN
            line_lower = stripped.lower()
            has_sch = bool(re.search(r'sch\s*=', line_lower))
            has_pkg = bool(re.search(r'package_pin\s+', line_lower))
            
            if has_sch and has_pkg:
                sch_match = re.search(r'sch\s*=\s*([^\s;#]+)', stripped, re.IGNORECASE)
                pkg_match = re.search(r'package_pin\s+([A-Za-z0-9_]+)', stripped, re.IGNORECASE)
                io_match = re.search(r'iostandard\s+([A-Za-z0-9_]+)', stripped, re.IGNORECASE)
                
                if sch_match and pkg_match:
                    name = sch_match.group(1).strip()
                    pkg = pkg_match.group(1).strip()
                    iostd = io_match.group(1).upper() if io_match else None
                    
                    # Vendor quirk: QSPI scl/sda belong to global section
                    if current_header == "##QSPI" and name.lower() in ("scl", "sda"):
                        current_header = "__global__"
                    
                    # Create template with {PORT} placeholder
                    template = stripped.lstrip('#').strip()

                    template = re.sub(
                        r'(get_ports\s+\{?)\s*[^}]*\s*(\}?)',
                        r'\1{PORT}\2',
                        template,
                        flags=re.IGNORECASE
                    )
                    
                    # Initialize or update pin map entry
                    if name not in self.pin_map:
                        self.pin_map[name] = [[pkg], None, [template], current_header, [], iostd]
                    else:
                        if pkg not in self.pin_map[name][0]:
                            self.pin_map[name][0].append(pkg)
                        if template not in self.pin_map[name][2]:
                            self.pin_map[name][2].append(template)
                        if iostd and not self.pin_map[name][5]:
                            self.pin_map[name][5] = iostd
                    
                    # Track physical pin → IOSTANDARD for validation
                    if iostd:
                        self.physical_pin_io[pkg] = iostd
                    
                    pins_found += 1
            
            # Debug: warn if no pins found after scanning constraints
            elif 'set_property' in line_lower and pins_found == 0:
                if len(debug_samples) < 5:
                    debug_samples.append(f"  {stripped[:100]}")
        
        if pins_found == 0 and source_path:
            log_warn(f"Zero pins parsed from {source_path}")
            if debug_samples:
                log_warn("Sample constraint lines:")
                for s in debug_samples:
                    log_warn(s)
    
    def _parse_active_constraints(self, content: str) -> None:
        """
        Parse active XDC to populate assigned_port and net associations.
        
        Args:
            content: Active constraints file content.
        """
        # Reverse lookup: package_pin → schematic_name
        pkg_to_sch = {
            pkg: sch 
            for sch, data in self.pin_map.items() 
            for pkg in data[0]
        }
        
        primary_pat = re.compile(
            r'PACKAGE_PIN\s+([A-Za-z0-9_]+)\s+.*?\[get_ports\s+\{?\s*([\w\[\]]+)\s*\}?\]',
            re.IGNORECASE
        )
        net_pat = re.compile(
            r'\[get_nets\s+\{?\s*([\w\[\]]+)\s*\}?\]',
            re.IGNORECASE
        )
        port_pat = re.compile(
            r'\[get_ports\s+\{?\s*([\w\[\]]+)\s*\}?\]',
            re.IGNORECASE
        )
        
        temp_nets: Dict[str, List] = {}
        temp_misc: List[str] = []
        
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            
            # Capture net references
            net_match = net_pat.search(stripped)
            if net_match:
                net_name = net_match.group(1)
                if net_name not in temp_nets:
                    temp_nets[net_name] = [None, None, [], []]  # [sch, port, lines, props]
                temp_nets[net_name][2].append(stripped)
                continue
            
            # Capture clock/group directives
            if stripped.lower().startswith(('create_clock', 'set_clock_groups')):
                temp_misc.append(stripped)
                continue
            
            # Capture port assignments and properties
            port_match = port_pat.search(stripped)
            if port_match:
                port_name = port_match.group(1)
                
                if 'PACKAGE_PIN' in stripped.upper():
                    # Primary assignment: PACKAGE_PIN + get_ports
                    primary_match = primary_pat.search(stripped)
                    if primary_match:
                        pkg = primary_match.group(1)
                        if pkg in pkg_to_sch and self.pin_map[pkg_to_sch[pkg]][1] is None:
                            self.pin_map[pkg_to_sch[pkg]][1] = port_name
                else:
                    # Secondary property: attach to existing port assignment
                    for sch, data in self.pin_map.items():
                        if data[1] == port_name:
                            if stripped not in data[4]:
                                data[4].append(stripped)
                            break
        
        # Associate nets with schematic names via fuzzy matching
        for net_name, data in temp_nets.items():
            base = self._strip_net_suffix(net_name)
            norm_base = base.lower().replace('_', '')
            
            # Exact match with schematic name
            if base in self.pin_map:
                data[0] = base
                data[1] = self.pin_map[base][1]
                continue
            
            # Exact match with assigned port name
            found = False
            for sch, pin_data in self.pin_map.items():
                if pin_data[1] == base:
                    data[0] = sch
                    data[1] = base
                    found = True
                    break
            if found:
                continue
            
            # Fuzzy match: underscore/case normalized
            for sch, pin_data in self.pin_map.items():
                if norm_base in sch.lower().replace('_', '') or sch.lower().replace('_', '') in norm_base:
                    data[0] = sch
                    data[1] = pin_data[1]
                    break
                assigned = pin_data[1]
                if assigned and (
                    norm_base in assigned.lower().replace('_', '') or 
                    assigned.lower().replace('_', '') in norm_base
                ):
                    data[0] = sch
                    data[1] = assigned
                    break
        
        # Attach net-based properties to pins
        for net_name, data in temp_nets.items():
            sch_name = data[0]
            if sch_name and sch_name in self.pin_map:
                for line in data[2]:
                    if 'set_property' in line.lower() and 'get_nets' in line.lower():
                        if line not in self.pin_map[sch_name][4]:
                            self.pin_map[sch_name][4].append(line)
        
        # Commit results
        self.nets.update(temp_nets)
        self.misc.extend(temp_misc)
    
    def _parse_xdc_structure(self, content: str) -> None:
        """
        Parse XDC header structure and IOSTANDARD per header group.
        
        Args:
            content: XDC file content.
        """
        self.xdc_headers.clear()
        self.xdc_blocks.clear()
        self.header_voltages.clear()
        
        current_header = "__global__"
        self.xdc_blocks[current_header] = []
        self.xdc_headers.append(current_header)
        
        for line in content.splitlines(keepends=True):
            stripped = line.strip()
            if not stripped:
                continue
            
            # Header detection: lines starting with ##
            if stripped.startswith('##'):
                if stripped not in self.xdc_blocks:
                    self.xdc_headers.append(stripped)
                    self.xdc_blocks[stripped] = []
                current_header = stripped
            else:
                if current_header not in self.xdc_blocks:
                    self.xdc_blocks[current_header] = []
                self.xdc_blocks[current_header].append(line)
        
        # Extract IOSTANDARD per header from first constraint line
        for header in self.xdc_headers:
            for line in self.xdc_blocks.get(header, []):
                if 'set_property' in line.lower() and 'package_pin' in line.lower():
                    match = re.search(r'iostandard\s+([A-Za-z0-9_]+)', line, re.IGNORECASE)
                    if match:
                        self.header_voltages[header] = match.group(1).upper()
                        break
    
    def _resolve_header(self, target_header: str) -> str:
        """
        Resolve header name with fuzzy matching for whitespace variations.
        
        Args:
            target_header: Header name to resolve.
            
        Returns:
            Actual header key in xdc_blocks, or target_header if new.
        """
        if target_header in self.xdc_blocks:
            return target_header
        
        # Normalize: remove spaces and #, lowercase
        norm = target_header.replace(' ', '').replace('#', '').lower()
        for key in self.xdc_blocks.keys():
            norm_key = key.replace(' ', '').replace('#', '').lower()
            if norm in norm_key or norm_key in norm:
                return key
        
        # Header not found: create new entry
        if target_header not in self.xdc_blocks:
            self.xdc_headers.append(target_header)
            self.xdc_blocks[target_header] = []
        return target_header
    
    def _render_xdc(self) -> str:
        """
        Render XDC content from internal blocks structure 
        while preserving blank lines between headers.
        
        Returns:
            Complete XDC file content as string.
        """
        output: List[str] = []
        
        for i, header in enumerate(self.xdc_headers):
            # Add blank line before header (except first)
            if i > 0 and header != "__global__":
                output.append("\n")
            
            if header != "__global__":
                output.append(header + "\n")
            
            for line in self.xdc_blocks.get(header, []):
                output.append(line if line.endswith('\n') else line + '\n')
        
        return "".join(output)