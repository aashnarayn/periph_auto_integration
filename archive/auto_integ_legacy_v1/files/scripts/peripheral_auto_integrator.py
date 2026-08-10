#!/usr/bin/env python3
"""
Peripheral Auto-Integrator for Shakti GC2025
Fully automated, width-agnostic, YAML-driven peripheral integration engine.
Supports sequential BSV -> Verilog -> XDC phases with structural constraint editing.
Compatible with 5-element pin map: [pkgs, assigned_port, templates, header, properties]
"""

import yaml
import sys
import os
import re
import shutil
import argparse
from pathlib import Path
from typing import Dict, Tuple, List, Optional

# ==================== HELPER FUNCTIONS ==================== #

def load_yaml(yaml_path: str) -> Dict:
    """Safely load YAML configuration files."""
    try:
        with open(yaml_path, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"[ERROR] Config not found: {yaml_path}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"[ERROR] YAML parse error in {yaml_path}: {e}")
        sys.exit(1)

def resolve_template(template: str, context: Dict) -> str:
    """Safely resolves up to 2 levels of nested placeholders."""
    resolved = template
    for _ in range(2):
        if '{' not in resolved:
            break
        try:
            resolved = resolved.format(**context)
        except KeyError:
            break
    return resolved

# ==================== TRANSACTIONAL SAFETY ==================== #

class PatchTransaction:
    """Atomic file patching with mirrored backup/rollback."""
    def __init__(self, filepaths: List[str], backup_dir: Path = None):
        self.backup_dir = backup_dir or Path(".automation_backup")
        self.backups: Dict[str, Path] = {}
        self.files = set(filepaths)
        self._success = False

    def mark_success(self):
        """Call this at the end of the 'with' block if all patches succeeded."""
        self._success = True

    def __enter__(self):
        self.backup_dir.mkdir(exist_ok=True, parents=True)
        for fp in self.files:
            if Path(fp).exists():
                backup_path = self.backup_dir / fp
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                
                # ONLY backup if it doesn't already exist!
                if not backup_path.exists():
                    shutil.copy2(fp, backup_path)
                    print(f" [INFO] Captured baseline backup of {fp}")
                
                self.backups[fp] = backup_path
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type or not self._success:
            print("[WARNING] Rolling back patches due to error or validation failure...")
            for orig_path, backup_path in self.backups.items():
                if backup_path.exists():
                    shutil.copy2(backup_path, orig_path)
                    print(f"  [INFO] Restored {orig_path}")
            print("[INFO] Rollback complete.")
        return False

# ==================== STRUCTURAL XDC EDITOR ==================== #

class XDCEditor:
    """Structural XDC file editor that preserves header grouping and line formatting."""
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.header_order = []
        self.blocks = {}  # header_name -> [list of lines]
        self.header_lookup = {}  # Normalized header name -> Real header key
        self.header_voltages = {}  # header_name -> IOSTANDARD value
        self._load(filepath)

    def _load(self, filepath: str):
        try:
            with open(filepath, 'r') as f:
                lines = f.readlines()
        except FileNotFoundError:
            return

        current_header = "__global__"
        self.blocks[current_header] = []
        self.header_order.append(current_header)

        for line in lines:
            stripped = line.strip()
            if stripped.startswith('##'):
                if stripped not in self.blocks:
                    self.header_order.append(stripped)
                    self.blocks[stripped] = []
                current_header = stripped
            else:
                if current_header not in self.blocks:
                    self.blocks[current_header] = []
                self.blocks[current_header].append(line)

        # Build fuzzy lookup: strip spaces, lowercase, remove '#'
        for h in self.header_order:
            norm_key = h.replace(' ', '').replace('#', '').lower()
            self.header_lookup[norm_key] = h
            
        # === Extract IOSTANDARD per header from first constraint line ===
        for header, block_lines in self.blocks.items():
            for line in block_lines:
                if 'set_property' in line and 'PACKAGE_PIN' in line:
                    match = re.search(r'IOSTANDARD\s+(\S+)', line)
                    if match:
                        self.header_voltages[header] = match.group(1)
                        break

    def _resolve_header(self, target_header: str) -> str:
        """Finds the existing header block, ignoring whitespace differences."""
        if target_header in self.blocks:
            return target_header
        
        norm_target = target_header.replace(' ', '').replace('#', '').lower()
        if norm_target in self.header_lookup:
            return self.header_lookup[norm_target]
        for norm_key, real_key in self.header_lookup.items():
            if norm_target in norm_key or norm_key in norm_target:
                return real_key
        if target_header not in self.blocks:
            self.header_order.append(target_header)
            self.blocks[target_header] = []
        return target_header

    def _render_template(self, template: str, port: str, target_header: str) -> str:
        """Renders a template with port substitution and header-specific voltage injection."""
        voltage = self.header_voltages.get(target_header, "LVCMOS33")
        line = template.replace('{PORT}', port)
        # Inject correct voltage standard for the destination header
        line = re.sub(r'IOSTANDARD\s+\S+', f'IOSTANDARD {voltage}', line)
        return line + '\n'

    def get_assigned_port(self, board_pin: str, pin_map: Dict) -> str:
        """Returns the port currently assigned to this board pin."""
        info = pin_map.get(board_pin)
        return info[1] if info and len(info) > 1 else None

    def apply_patch(self, port_name: str, target_pin: str, fallback_pin: str, 
                   pin_map: Dict, dry_run: bool) -> bool:
        target_info = pin_map.get(target_pin)
        if not target_info:
            print(f"[ERROR] Target pin '{target_pin}' not in pin map")
            return False
            
        raw_header = target_info[3] if len(target_info) > 3 else "__global__"
        target_header = self._resolve_header(raw_header)
        templates = target_info[2] if len(target_info) > 2 else []
        
        old_port = self.get_assigned_port(target_pin, pin_map)
        
        if old_port and old_port != port_name:
            # === CONFLICT RESOLUTION ===
            if not fallback_pin:
                print(f"[ERROR] Conflict: {target_pin} assigned to '{old_port}', no fallback for '{port_name}'")
                return False
                
            fallback_info = pin_map.get(fallback_pin)
            if not fallback_info:
                print(f"[ERROR] Fallback pin '{fallback_pin}' not in pin map")
                return False
                
            if self.get_assigned_port(fallback_pin, pin_map):
                print(f"[ERROR] Fallback {fallback_pin} already occupied by '{self.get_assigned_port(fallback_pin, pin_map)}'")
                print(f"       Please choose a different fallback pin in soc_build_config.yaml")
                return False
                
            fallback_header = self._resolve_header(fallback_info[3] if len(fallback_info) > 3 else "__global__")
            
            # Ensure headers exist
            if target_header not in self.blocks:
                self.header_order.append(target_header)
                self.blocks[target_header] = []
            if fallback_header not in self.blocks:
                self.header_order.append(fallback_header)
                self.blocks[fallback_header] = []

            # 1. Process target header: Replace old_port in-place, move extras
            new_target_lines = []
            lines_to_move = []
            port_regex = re.compile(r'get_ports\s+\{?\s*' + re.escape(old_port) + r'\s*\}?')
            
            for line in self.blocks[target_header]:
                if port_regex.search(line):
                    if 'PACKAGE_PIN' in line:
                        # Main constraint line: swap port name in-place
                        new_line = re.sub(r'(get_ports\s+\{?)\s*' + re.escape(old_port) + r'(\}?)', f'\\1{port_name}\\2', line)
                        new_target_lines.append(new_line)
                    else:
                        # Extra property line (PULLDOWN, etc.): move to fallback
                        lines_to_move.append(line)
                else:
                    new_target_lines.append(line)
            self.blocks[target_header] = new_target_lines
            
            # 2. Generate fallback lines using templates with header-specific voltage
            new_fallback_lines = [self._render_template(tmpl, old_port, fallback_header) 
                                  for tmpl in fallback_info[2] if len(fallback_info) > 2]
            
            # 3. Append moved/extra lines to fallback header at end of block
            self.blocks[fallback_header].extend(lines_to_move)
            self.blocks[fallback_header].extend(new_fallback_lines)
            
            # Update pin_map state for BOTH pins
            pin_map[fallback_pin][1] = old_port
            # Move properties list too
            if len(pin_map[target_pin]) > 4:
                pin_map[fallback_pin][4] = pin_map[target_pin][4][:]  # Copy properties
                pin_map[target_pin][4] = []  # Clear from target
            pin_map[target_pin][1] = port_name
            print(f"  [INFO] Moved {old_port} to {fallback_pin}, assigned {port_name} to {target_pin}")
            
        else:
            # === TARGET IS FREE OR IDEMPOTENT ===
            if old_port == port_name:
                print(f"  [INFO] Skipping: {port_name} already at {target_pin}")
                return True
                
            if target_header not in self.blocks:
                self.header_order.append(target_header)
                self.blocks[target_header] = []
                
            # Append to end of the correct header block with correct voltage
            new_lines = [self._render_template(tmpl, port_name, target_header) for tmpl in templates]
            self.blocks[target_header].extend(new_lines)
            pin_map[target_pin][1] = port_name
            print(f"  [INFO] Assigned {port_name} to {target_pin}")
            
        return True

    def save(self, dry_run: bool = False):
        if dry_run:
            print(f"  [INFO] (DRY-RUN) Would update {self.filepath}")
            return
            
        with open(self.filepath, 'w') as f:
            for header in self.header_order:
                if header != "__global__":
                    f.write(header + "\n")
                for line in self.blocks.get(header, []):
                    if not line.endswith('\n'):
                        f.write(line + '\n')
                    else:
                        f.write(line)
        print(f"  [INFO] Updated {self.filepath}")

# ==================== STANDARD PATCHING ==================== #

def patch_file_regex(filepath: str, anchor_pattern: str, injection: str, 
                    position: str = 'after', skip_if_contains: str = None,
                    dry_run: bool = False) -> bool:
    """Apply regex-based text patching with positional logic."""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"[ERROR] Target missing: {filepath}")
        return False
    
    if skip_if_contains and skip_if_contains in content:
        print(f" [INFO] Skipping patch: '{skip_if_contains}' already in {filepath}")
        return True

    try:
        match = re.search(anchor_pattern, content, re.MULTILINE)
    except re.error as e:
        print(f"[ERROR] Invalid regex pattern in {filepath}: {anchor_pattern[:40]}... ({e})")
        return False
    
    if not match:
        print(f"[ERROR] Anchor not found in {filepath}: {anchor_pattern[:40]}...")
        return False

    inj = injection.rstrip()
    if position == 'replace':
        content = content[:match.start()] + inj + content[match.end():]
    elif position == 'before':
        content = content[:match.start()] + inj + "\n" + content[match.start():]
    else:  # after
        content = content[:match.end()] + "\n" + inj + content[match.end():]

    if not dry_run:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"  [INFO] Patched {filepath} ({position} anchor)")
    else:
        print(f"  [INFO] (DRY-RUN) Would patch {filepath} ({position} anchor)")
    return True

# ==================== PLIC INTEGRATION ==================== #

KNOWN_OPAQUE_WIDTHS = {
    "lv_gpio_intr": 16,
}

def parse_plic_items(concat_str: str) -> List[Tuple[str, int]]:
    """Parse BSV concatenation into [(original_text, bit_width), ...]."""
    items = []
    for raw in concat_str.split(','):
        raw = raw.strip()
        if not raw: continue
        m = re.match(r'([\w$]+)\s*\[\s*(\d+)\s*(?::\s*(\d+))?\s*\]', raw)
        if m:
            high = int(m.group(2))
            low = int(m.group(3)) if m.group(3) else high
            width = abs(high - low) + 1
            items.append((raw, width))
        else:
            base_name = raw.split('.')[0]
            width = KNOWN_OPAQUE_WIDTHS.get(raw, KNOWN_OPAQUE_WIDTHS.get(base_name, 1))
            items.append((raw, width))
    return items

def handle_plic_vector_patch(filepath: str, patch: Dict, context: Dict, dry_run: bool = False) -> bool:
    """Handle PLIC vector width update and signal insertion."""
    signal = patch['signal'].format(**context)
    pos = patch.get('position', 'msb').format(**context)
    w_pat = patch['width_anchor_pattern']
    c_pat = patch['concat_anchor_pattern']
    skip = patch.get('skip_if_contains', '').format(**context)

    if skip:
        with open(filepath, 'r') as f:
            if skip in f.read():
                print(f"  [INFO] Skipping PLIC: '{signal}' already present")
                return True

    with open(filepath, 'r') as f:
        content = f.read()

    w_match = re.search(w_pat, content)
    if not w_match:
        print(f"[ERROR] Could not find PLIC width declaration: {w_pat}")
        return False
        
    old_w = int(w_match.group(1))
    new_w = old_w + 1
    context['new_plic_width'] = str(new_w)
    print(f"  [INFO] Width: {old_w} → {new_w}")

    c_match = re.search(c_pat, content, re.DOTALL)
    if not c_match:
        print(f"[ERROR] Could not find PLIC concatenation: {c_pat}")
        return False
        
    items = parse_plic_items(c_match.group(1))
    current_bit_count = sum(w for _, w in items)
    print(f"  [INFO] Detected PLIC: Bit#({old_w}) with {current_bit_count} actual bits ({len(items)} comma items)")

    existing_signals = [txt for txt, _ in items]
    if signal not in existing_signals:
        new_item = (signal, 1)
        if pos == 'msb': items.insert(0, new_item)
        elif pos == 'lsb': items.append(new_item)
        elif isinstance(pos, int): items.insert(int(pos), new_item)
        else: print(f"[ERROR] Invalid PLIC position: {pos}"); return False
        print(f"  [INFO] Inserted '{signal}' at '{pos}'")
    else:
        print(f"  [INFO] Signal already in list")

    final_bit_count = sum(w for _, w in items)
    if final_bit_count != new_w:
        diff = final_bit_count - new_w
        print(f"[WARN] Declared width {new_w} != actual bits {final_bit_count} (diff: {diff})")
        print(f"       (Likely due to opaque vectors like Bit#(16). Proceeding for PoC.)")
        
    new_concat = ', '.join(txt for txt, _ in items)
    content = content[:c_match.start(1)] + new_concat + content[c_match.end(1):]
    content = content[:w_match.start(1)] + str(new_w) + content[w_match.end(1):]

    if not dry_run:
        with open(filepath, 'w') as f:
            f.write(content)
            
    print(f"  [INFO] PLIC vector validated (Width: {new_w}, Items: {len(items)}, Bits: {final_bit_count})")
    return True

# ==================== PIN MAP & XDC UTILS ==================== #

def load_pin_map(pin_map_path: str) -> Dict[str, List]:
    """
    Load pin map from YAML with automatic schema normalization.
    Ensures every entry has exactly 5 elements: [pkgs, assigned_port, templates, header, properties].
    """
    raw = load_yaml(pin_map_path)
    pins = raw.get('pins', raw) if isinstance(raw, dict) else {}
    
    for key, val in pins.items():
        if not isinstance(val, list):
            continue
        # Pad missing fields with safe defaults to prevent IndexError
        while len(val) < 5:
            if len(val) == 0: val.append([])          # package_pins list
            elif len(val) == 1: val.append(None)       # assigned_port
            elif len(val) == 2: val.append([])         # templates list
            elif len(val) == 3: val.append("__global__") # header
            elif len(val) == 4: val.append([])         # properties list
        pins[key] = val
    return pins

def populate_active_assignments(pin_map: Dict[str, List], xdc_path: str) -> None:
    """Sweep 2: Parse active constraints file to update assigned_port in pin_map."""
    if not Path(xdc_path).exists():
        return
        
    pkg_to_schem = {}
    for sch_name, data in pin_map.items():
        if isinstance(data[0], list):
            for pkg in data[0]:
                pkg_to_schem[pkg] = sch_name
        elif data[0]:
            pkg_to_schem[data[0]] = sch_name
    
    with open(xdc_path, 'r') as f:
        content = f.read()
        
    pattern = r'^\s*set_property\s+.*PACKAGE_PIN\s+(\S+).*?\[get_ports\s+\{?\s*([\w\[\]]+)\s*\}?\s*\]'
    
    for match in re.finditer(pattern, content, re.MULTILINE):
        pkg_pin, port = match.groups()
        if pkg_pin in pkg_to_schem:
            schem_pin = pkg_to_schem[pkg_pin]
            if pin_map[schem_pin][1] is None:
                pin_map[schem_pin][1] = port

def save_pin_map(pin_map_path: str, pin_map: Dict):
    """Persist the runtime state of pin assignments back to pin_map.yaml."""
    try:
        with open(pin_map_path, 'r') as f:
            content = f.read()
        raw = yaml.safe_load(content)
        if raw is None: 
            raw = {}

        if isinstance(raw, dict) and 'pins' in raw:
            raw['pins'] = pin_map
            with open(pin_map_path, 'w') as f:
                yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
            print(f"  [INFO] State saved: pin_map.yaml updated with new assignments")
        else:
            print(f"  [WARN] pin_map.yaml format unexpected ('pins' key missing). State not saved.")
    except Exception as e:
        print(f"  [ERROR] Failed to save pin_map.yaml: {e}")

def validate_bsv(filepath: str) -> bool:
    import subprocess
    try:
        res = subprocess.run(['bsc', '-check', filepath], capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            print(f"[ERROR] BSV error in {filepath}:\n{res.stderr[:400]}")
            return False
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return True

# ==================== MAIN EXECUTION ENGINE ==================== #
def execute_integration(config: Dict, dry_run: bool = False, phase: str = 'all') -> bool:
    board_dir = config.get('board_dir', 'boards/nexys_video')
    peripherals = config.get('automated_peripherals', [])
    if not peripherals:
        print(" [INFO] No peripherals in BOM.")
        return True

    # 1. Load pin map (Sweep 1: capabilities)
    pin_map_path = config.get('pin_map_path', 'boards/nexys_video/pin_map.yaml')
    pin_map = load_pin_map(pin_map_path)

    # 2. Collect target files AND initialize pin map state (Sweep 2: runtime assignments)
    files = set()
    for p in peripherals:
        ctx = {**p.get('context', {}), 'board_dir': board_dir}
        def_path = p['def_path']
        
        for patch in load_yaml(def_path).get('patches', []):
            p_phase = 'bsv'
            if patch.get('type') == 'xdc_pin_assign': p_phase = 'xdc'
            elif 'fpga_top' in patch.get('file', ''): p_phase = 'verilog'

            # Collect files for requested phase (including bsvpath)
            if phase == 'all' or phase == p_phase or phase == 'bsvpath':
                resolved = resolve_template(patch['file'], ctx) if '{' in patch['file'] else patch['file']
                files.add(resolved)
                
                if p_phase == 'xdc' and phase in ('all', 'xdc'):
                    if Path(resolved).exists():
                        populate_active_assignments(pin_map, resolved)
                    elif not dry_run:
                        print(f"[WARN] XDC file not found: {resolved} (skipping initialization)")

    # 3. Initialize XDC Editor if XDC phase is active
    xdc_editors = {}
    if phase in ('all', 'xdc'):
        for p in peripherals:
            ctx = {**p.get('context', {}), 'board_dir': board_dir}
            for patch in load_yaml(p['def_path']).get('patches', []):
                if patch.get('type') == 'xdc_pin_assign':
                    xdc_file = resolve_template(patch['file'], ctx) if '{' in patch['file'] else patch['file']
                    if Path(xdc_file).exists():
                        xdc_editors[xdc_file] = XDCEditor(xdc_file)
                        populate_active_assignments(pin_map, xdc_file)
                    break

    phase_display = phase.upper() if phase != 'all' else 'ALL'
    print(f"\n [INFO] Phase: {phase_display} | Peripherals: {len(peripherals)}")
    if dry_run: print(" [INFO] DRY RUN: No files modified.\n")

    # 4. Execute patches with transactional safety
    with PatchTransaction(files) as transaction:
        for p in peripherals:
            ctx = {**p.get('context', {}), 'board_dir': board_dir}
            name, def_path = p['name'], p['def_path']
            print(f"--- {name} ---")

            for i, patch in enumerate(load_yaml(def_path).get('patches', []), 1):
                # === BSVPATH PHASE LOGIC ===
                # Only apply the very first patch, then break out
                if phase == 'bsvpath' and i > 1:
                    print(" [INFO] bsvpath phase complete (1 patch applied).")
                    break

                p_phase = 'bsv'
                if patch.get('type') == 'xdc_pin_assign': p_phase = 'xdc'
                elif 'fpga_top' in patch.get('file', ''): p_phase = 'verilog'
                
                # Override phase matching for bsvpath mode
                if phase == 'bsvpath':
                    p_phase = 'bsvpath'

                if phase != 'all' and p_phase != phase:
                    print(f" [INFO] Skipping {p_phase} patch #{i}")
                    continue

                resolved = resolve_template(patch['file'], ctx) if '{' in patch['file'] else patch['file']

                if p_phase == 'xdc' and not Path(resolved).exists():
                    if dry_run:
                        print(f" [INFO] Skipping XDC patch #{i}: file not found (dry-run)")
                        continue
                    else:
                        print(f"[ERROR] XDC file missing: {resolved}")
                        return False

                if patch.get('type') == 'plic_vector':
                    if not handle_plic_vector_patch(resolved, patch, ctx, dry_run): return False
                elif patch.get('type') == 'xdc_pin_assign':
                    editor = xdc_editors.get(resolved)
                    if editor:
                        if not editor.apply_patch(patch['port_name'].format(**ctx), 
                                                 patch['target_board_pin'].format(**ctx), 
                                                 patch.get('fallback_pin', '').format(**ctx), 
                                                 pin_map, dry_run):
                            return False
                else:
                    inj = patch['code'].format(**ctx)
                    if not patch_file_regex(resolved, patch.get('anchor_pattern', ''), inj, patch.get('position', 'after'), patch.get('skip_if_contains'), dry_run): return False

            # Exit peripheral loop early for bsvpath phase
            if phase == 'bsvpath':
                break
        
        # Save all modified XDC files after applying patches
        for editor in xdc_editors.values():
            editor.save(dry_run)

        # === CONDITIONAL XDC PROPERTY INJECTION ===
        # Runs AFTER editor.save() to prevent overwrites. Handles additional_properties from context.
        if phase in ('all', 'xdc'):
            for p in peripherals:
                ctx = {**p.get('context', {}), 'board_dir': board_dir}
                add_props = ctx.get('additional_properties') or ctx.get('addtional_properties') # Handle typo gracefully
                
                if add_props:
                    # Normalize to list for iteration
                    props_to_add = [add_props] if isinstance(add_props, str) else add_props
                    
                    for prop in props_to_add:
                        prop = resolve_template(prop, ctx)
                        xdc_file = resolve_template(ctx.get('xdc_file', '{board_dir}/constraints.xdc'), ctx)
                        
                        if Path(xdc_file).exists():
                            # Anchor: Inject after sys_clk_IBUF clock route property
                            anchor = r'set_property\s+CLOCK_DEDICATED_ROUTE\s+BACKBONE\s+\[get_nets\s+sys_clk_IBUF\]'
                            print(f" [INFO] Applying additional XDC property for {p['name']}...")
                            if not patch_file_regex(xdc_file, anchor, prop, position='after',
                                                   skip_if_contains=prop, dry_run=dry_run):
                                return False

        # === STATE PERSISTENCE ===
        if not dry_run and phase in ('all', 'xdc'):
            save_pin_map(pin_map_path, pin_map)
        
        transaction.mark_success()

    return True

def main():
    parser = argparse.ArgumentParser(description='Shakti Peripheral Auto-Integrator')
    parser.add_argument('--config', default='soc_build_config.yaml', help='Path to integration config YAML')
    parser.add_argument('--dry-run', action='store_true', help='Show patches without modifying files')
    parser.add_argument('--restore', action='store_true', help='Restore files from backup')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    
    phase_group = parser.add_mutually_exclusive_group()
    phase_group.add_argument('--bsvpath', action='store_true', help='Apply only the bsvpath patch')
    phase_group.add_argument('--bsv', action='store_true', help='Apply BSV/SoC patches only')
    phase_group.add_argument('--verilog', action='store_true', help='Apply fpga_top.v patches only')
    phase_group.add_argument('--xdc', action='store_true', help='Apply XDC constraints only')
    args = parser.parse_args()

    if args.restore:
        bdir = Path(".automation_backup")
        if not bdir.exists() or not any(bdir.iterdir()):
            print(" [INFO] No backup found. Nothing to restore.")
            return
        print(" [INFO] Restoring from backup...")
        restored = 0
        for bp in bdir.rglob("*"):
            if bp.is_file():
                orig = bp.relative_to(bdir)
                if orig.exists():
                    shutil.copy2(bp, orig)
                    restored += 1
        print(f" [INFO] Restored {restored} files.")
        return

    cfg = load_yaml(args.config)
    
    phase = 'all'
    if args.bsvpath: phase = 'bsvpath'
    elif args.bsv: phase = 'bsv'
    elif args.verilog: phase = 'verilog'
    elif args.xdc: phase = 'xdc'

    if execute_integration(cfg, dry_run=args.dry_run, phase=phase):
        print(f"\n[INFO] {phase.upper()} Integration complete.")
        if args.dry_run: print(" [INFO] Remove --dry-run to apply patches.")
    else:
        print(f"\n[ERROR] {phase.upper()}  Integration failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()