#!/usr/bin/env python3
"""
migrate_yaml.py - Utility to strip auto-generated naming keys from YAML files.

Enables gradual migration from explicit naming to registry-generated conventions.

Usage:
    python3 scripts/migrate_yaml.py \
        --input ip_bsv/rtc_v1/rtc_v1.yaml \
        --output ip_bsv/rtc_v1/rtc_v1_minimal.yaml \
        --peripheral rtc_v1 \
        --dry-run

Features:
    - Preserves all non-naming keys (addresses, PLIC indices, pin hints, etc.)
    - Backs up original file before modification
    - Dry-run mode shows changes without writing
    - Supports multiple peripherals via --peripheral flag
"""

import argparse
import copy
import shutil
import sys
from pathlib import Path
from typing import Dict, Any

# Project utilities
import yaml
from utils import log_info, log_warn, log_fail
from peripheral_registry import CONTEXT_GENERATORS


def strip_generated_keys(
    data: Dict[str, Any], 
    peripheral_name: str, 
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Recursively remove generated naming keys from YAML structure.
    
    Args:
        data: Loaded YAML content as dictionary.
        peripheral_name: Name of peripheral (for generator lookup).
        dry_run: If True, print changes without modifying data.
        
    Returns:
        Cleaned dictionary with generated keys removed (or original if dry_run).
    """
    generator = CONTEXT_GENERATORS.get(peripheral_name)
    if not generator:
        log_warn(f"No generator for '{peripheral_name}'; skipping key stripping")
        return data
    
    # Work on a deep copy to avoid mutating original
    cleaned = copy.deepcopy(data)
    
    # Get keys that can be auto-generated for this peripheral
    generated_keys = generator.get_generated_keys()
    
    # Process instances list
    if 'instances' in cleaned and isinstance(cleaned['instances'], list):
        for inst in cleaned['instances']:
            inst_id = inst.get('instance_id', '?')
            for key in generated_keys:
                if key in inst:
                    if dry_run:
                        log_info(f"[DRY-RUN] Would remove '{key}' from instance {inst_id}")
                    else:
                        del inst[key]
                        log_info(f"Removed '{key}' from instance {inst_id}")
    
    return cleaned


def main() -> int:
    """
    CLI entry point for YAML migration utility.
    
    Returns:
        Exit code: 0 for success, 1 for error.
    """
    parser = argparse.ArgumentParser(
        description='Strip auto-generated naming keys from YAML files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Preview changes without writing
  python3 scripts/migrate_yaml.py --input rtc_v1.yaml --output rtc_v1_min.yaml --peripheral rtc_v1 --dry-run
  
  # Apply migration with backup
  python3 scripts/migrate_yaml.py --input rtc_v1.yaml --output rtc_v1.yaml --peripheral rtc_v1
        '''
    )
    
    parser.add_argument(
        '--input', 
        required=True, 
        help='Input YAML file path'
    )
    parser.add_argument(
        '--output', 
        required=True, 
        help='Output YAML file path'
    )
    parser.add_argument(
        '--peripheral', 
        required=True, 
        help='Peripheral name (e.g., rtc_v1)'
    )
    parser.add_argument(
        '--dry-run', 
        action='store_true', 
        help='Show changes without writing'
    )
    
    args = parser.parse_args()
    
    input_path = Path(args.input)
    output_path = Path(args.output)
    
    # Validate input file
    if not input_path.exists():
        log_fail(f"Input file not found: {input_path}")
        return 1
    
    log_info(f"Loading {input_path}")
    
    try:
        with open(input_path, 'r') as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        log_fail(f"YAML parse error in {input_path}: {e}")
        return 1
    except Exception as e:
        log_fail(f"Error reading {input_path}: {e}")
        return 1
    
    # Strip generated keys
    cleaned = strip_generated_keys(data, args.peripheral, args.dry_run)
    
    if args.dry_run:
        log_info("Dry-run complete. No files modified.")
        return 0
    
    # Backup original if output path is same as input
    if output_path == input_path:
        backup = input_path.with_suffix(input_path.suffix + ".bak")
        shutil.copy2(input_path, backup)
        log_info(f"Backed up original to {backup}")
    
    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write cleaned YAML
    try:
        with open(output_path, 'w') as f:
            # Preserve header comment if present in original
            with open(input_path, 'r') as orig:
                first_line = orig.readline()
                if first_line.strip().startswith('#'):
                    f.write(first_line)
            yaml.dump(cleaned, f, default_flow_style=False, sort_keys=False)
        log_info(f"Minimalized YAML written to {output_path}")
    except Exception as e:
        log_fail(f"Error writing {output_path}: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())