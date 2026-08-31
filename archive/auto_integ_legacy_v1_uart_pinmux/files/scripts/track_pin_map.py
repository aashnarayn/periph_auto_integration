#!/usr/bin/env python3
"""
track_pin_map.py - Production-Ready XDC Pin Map Generator & State Tracker
Separates master structure (templates/headers) from active runtime state (ports/nets/misc).
Output Structure:
  pins: {sch_name: [[package_pins...], assigned_port, [templates...], header, [additional_properties...]]}
  nets: {net_name: [associated_sch_name, associated_port_name, [templates...], [net_properties...]]}
  miscellaneous: [line1, line2, ...]
"""
import re
import yaml
import argparse
import sys
from pathlib import Path

def strip_net_suffix(net_name):
    """Strips common FPGA buffer suffixes to resolve base port name."""
    for suffix in ['_IBUF', '_OBUF', '_IBUFG', '_OBUFT', '_IN', '_OUT', '_CLK', '_CLK_N', '_CLK_P']:
        if net_name.endswith(suffix):
            return net_name[:-len(suffix)]
    return net_name

def parse_master_xdc(xdc_path: str) -> dict:
    """
    Extracts pin templates, headers, and Sch= mappings from the master XDC.
    Preserves all header/gap/vendor-quirk optimizations.
    """
    pins = {}
    current_header = "__global__"
    blank_lines = 0

    with open(xdc_path, 'r') as f:
        lines = f.readlines()

    for line in lines:
        stripped = line.strip()
        
        # 1. SPACING/GAP LOGIC
        if not stripped:
            blank_lines += 1
            if blank_lines >= 2:
                current_header = "__global__"
            continue
            
        blank_lines = 0

        # 2. HEADER SEPARATION
        is_comment = stripped.startswith('#')
        is_constraint = any(kw in stripped.lower() for kw in ['set_property', 'sch=', 'package_pin', 'create_clock'])
        
        if is_comment and not is_constraint:
            is_metadata = any(kw in stripped.lower() for kw in ['this file', 'to use it', 'uncomment', 'rename', 'for the'])
            if not is_metadata:
                current_header = stripped
            continue

        # 3. CONSTRAINT PARSING
        if 'sch=' in stripped.lower() and 'package_pin' in stripped.lower():
            sch_match = re.search(r'Sch=([^\s;#]+)', stripped)
            pkg_match = re.search(r'PACKAGE_PIN\s+([A-Za-z0-9]+)', stripped)
            
            if sch_match and pkg_match:
                name = sch_match.group(1)
                pkg = pkg_match.group(1)
                
                # Vendor Quirk Exception
                if current_header == "##QSPI" and name in ("scl", "sda"):
                    current_header = "__global__"

                template = stripped.lstrip('#').strip()
                template = re.sub(r'(get_ports\s+\{?)\s*[^}]*\s*(\}?)', r'\1{PORT}\2', template)

                if name not in pins:
                    pins[name] = [[], None, [], current_header, []]
                pins[name][0].append(pkg)
                pins[name][2].append(template)

    return pins

def populate_active_state(pin_map: dict, nets: dict, misc: list, active_xdc_path: str) -> int:
    """
    Three-pass active state parser:
    Pass 1: Extract assignments, net properties, nets, misc.
    Pass 2: Associate nets with ports using robust fuzzy matching.
    Pass 3: Attach net-based properties (e.g., CLOCK_DEDICATED_ROUTE) to pins.
    """
    if not Path(active_xdc_path).exists():
        print(f"  [WARN] Active constraints file not found: {active_xdc_path}")
        return 0

    primary_pat = re.compile(r'PACKAGE_PIN\s+([A-Za-z0-9]+)\s+.*?\[get_ports\s+\{?\s*([\w\[\]]+)\s*\}?\]')
    net_pat = re.compile(r'\[get_nets\s+\{?\s*([\w\[\]]+)\s*\}?\]')
    
    # Reverse lookup: package_pin -> sch_name
    pkg_to_sch = {pkg: sch for sch, data in pin_map.items() for pkg in data[0]}

    assigned_count = 0
    temp_nets = {}
    temp_misc = []

    # === PASS 1: Extract All Active Elements ===
    with open(active_xdc_path, 'r') as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue

            # Capture Nets & Net Properties
            net_m = net_pat.search(stripped)
            if net_m:
                net_name = net_m.group(1)
                if net_name not in temp_nets:
                    temp_nets[net_name] = [None, None, [], []]  # [sch, port, lines, props]
                temp_nets[net_name][2].append(stripped)
                continue

            # Capture Misc
            if stripped.lower().startswith(('create_clock', 'set_clock_groups')):
                temp_misc.append(stripped)
                continue

            # Capture Port Assignments & Port Properties
            port_m = re.search(r'\[get_ports\s+\{?\s*([\w\[\]]+)\s*\}?\]', stripped)
            if port_m:
                port_name = port_m.group(1)
                if 'PACKAGE_PIN' in stripped:
                    primary_m = primary_pat.search(stripped)
                    if primary_m:
                        pkg = primary_m.group(1)
                        if pkg in pkg_to_sch:
                            pin_map[pkg_to_sch[pkg]][1] = port_name
                            assigned_count += 1
                else:
                    # Secondary property attachment (PULLDOWN, etc.)
                    for s, d in pin_map.items():
                        if d[1] == port_name:
                            d[4].append(stripped)
                            break
                    if port_name in pin_map and port_name not in [d[1] for d in pin_map.values()]:
                        pin_map[port_name][4].append(stripped)

    # === PASS 2: Associate Nets with Schematic & Port Names ===
    for net_name, data in temp_nets.items():
        base = strip_net_suffix(net_name)
        norm_base = base.lower().replace('_', '')
        
        # 1. Exact match with Sch= names
        if base in pin_map:
            data[0] = base
            data[1] = pin_map[base][1]
            continue
            
        # 2. Exact match with assigned port names
        found = False
        for sch, pin_data in pin_map.items():
            if pin_data[1] == base:
                data[0] = sch
                data[1] = base
                found = True
                break
        if found: continue

        # 3. Robust fuzzy match (underscore & case normalized)
        for sch, pin_data in pin_map.items():
            if norm_base in sch.lower().replace('_', '') or sch.lower().replace('_', '') in norm_base:
                data[0] = sch
                data[1] = pin_data[1]
                break
            assigned = pin_data[1]
            if assigned and (norm_base in assigned.lower().replace('_', '') or assigned.lower().replace('_', '') in norm_base):
                data[0] = sch
                data[1] = assigned
                break

    # === PASS 3: Attach Net Properties to Pins ===
    # Handles injected lines like: set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets xxx_IBUF]
    for net_name, data in temp_nets.items():
        sch_name = data[0]
        if sch_name and sch_name in pin_map:
            for line in data[2]:
                if 'set_property' in line and 'get_nets' in line:
                    if line not in pin_map[sch_name][4]:
                        pin_map[sch_name][4].append(line)

    # Commit results
    nets.update(temp_nets)
    misc.extend(temp_misc)
    return assigned_count
    
def main():
    parser = argparse.ArgumentParser(description="Generate & track board-aware pin map from XDC")
    parser.add_argument("input_xdc", help="Master constraints file")
    parser.add_argument("output_yaml", help="Output pin map YAML")
    parser.add_argument("--active_xdc", help="Active constraints.xdc file (for live state tracking)")
    parser.add_argument("--board", help="Board name override")
    parser.add_argument("--fpga", help="FPGA part override")
    args = parser.parse_args()

    if not Path(args.input_xdc).exists():
        print(f"[ERROR] Master XDC file not found: {args.input_xdc}"); sys.exit(1)

    print(f"[INFO] Parsing master XDC structure: {args.input_xdc}...")
    pin_map = parse_master_xdc(args.input_xdc)
    print(f"  [INFO] Extracted {len(pin_map)} pin groups")
    
    nets = {}
    misc = []
    active_count = 0

    if args.active_xdc:
        print(f"[INFO] Tracking active state from: {args.active_xdc}...")
        active_count = populate_active_state(pin_map, nets, misc, args.active_xdc)
        print(f"  [INFO] Synced {active_count} port assignments, {len(nets)} nets, {len(misc)} misc directives")
    else:
        print(f"[WARN] --active_xdc not provided. Ports, nets, and misc will remain empty/untracked.")

    result = {
        "meta": {
            "xdc_source": Path(args.input_xdc).name,
            "description": "Auto-generated. Master templates + active state tracking."
        },
        "pins": pin_map,
        "nets": nets,
        "miscellaneous": misc
    }

    if args.board: result["meta"]["board_name"] = args.board
    if args.fpga: result["meta"]["fpga_part"] = args.fpga

    out_path = Path(args.output_yaml)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w') as f:
        yaml.dump(result, f, default_flow_style=False, sort_keys=False)

    print(f"[INFO] Generated {out_path}")

if __name__ == '__main__':
    main()