#!/usr/bin/env python3
"""
Peripheral Auto-Integrator V2 for Shakti GC2025 -- EXECUTOR

Single entry point for integration, auditing, pin-tracking, YAML migration
and fresh-clone verification. This file holds the CLI and the
orchestration only; every piece of logic lives in a functional module under
scripts/autointeg/ (see that package's docstring for the map).

Audit Separation:
- --pre-audit / --post-audit: Run audits ONLY (no patch application)
- Default execution: Apply patches ONLY (no auto-audit)
- Audits are run via separate Makefile targets: pre_validate_automation,
  post_validate_automation
"""

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from autointeg.common import (
    log_pass, log_fail, log_warn, log_info, log_debug, set_verbose, load_yaml,
    resolve_template,
)
from autointeg.audit import run_pre_audit, run_post_audit
from autointeg.backup import BACKUP_DIR, load_manifest, save_manifest, snapshot_files
from autointeg.bootstrap import bootstrap_board_anchors
from autointeg.config import (
    check_migration_needed, migrate_soc_config, resolve_def_path,
)
from autointeg.engine import execute_integration
from autointeg.tracker import ResourceTracker
from autointeg.verify import fresh_clone_verify
from autointeg.xdc import BoardSupportAnalyzer


def main() -> None:
    """Unified CLI for integration, auditing, and migration."""
    parser = argparse.ArgumentParser(
        description='Shakti Peripheral Auto-Integrator V2.0',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # Core arguments
    parser.add_argument(
        '--config', 
        default='soc_build_config.yaml', 
        help='Path to SOC configuration YAML'
    )
    parser.add_argument(
        '--dry-run', 
        action='store_true', 
        help='Simulate changes without writing to disk'
    )
    parser.add_argument(
        '--restore',
        action='store_true',
        help='Restore files from .automation_backup manifest'
    )
    parser.add_argument(
        '--backup',
        nargs='+',
        metavar='FILE',
        help='Snapshot FILEs into .automation_backup and record them in the '
             'manifest, so --restore rewinds them too. Used by the sync targets '
             'to protect the hw/ root copies before they are overwritten.'
    )
    parser.add_argument(
        '--reset-baseline',
        action='store_true',
        help='Discard .automation_backup so the next run re-captures the pristine '
             'baseline. Run only from a restored/clean tree.'
    )
    parser.add_argument(
        '--bootstrap-board',
        action='store_true',
        help='Seed the automation anchors a fresh clone lacks (derived-width '
             'macros, comment markers, one-per-line concats) into the board '
             'sources. Idempotent; runs automatically before pre-audit and '
             'integration, but exposed here so it can be invoked on its own.'
    )
    parser.add_argument(
        '--verbose', '-v', 
        action='store_true', 
        help='Enable verbose/debug logging'
    )
    
    # YAML migration group
    migration_group = parser.add_argument_group('YAML Migration')
    migration_group.add_argument(
        '--migrate', 
        action='store_true', 
        help='Strip auto-generated naming keys from soc_build_config.yaml'
    )
    migration_group.add_argument(
        '--check-migration', 
        action='store_true', 
        help='Check if config requires YAML migration'
    )
    
    # Pin tracking
    parser.add_argument(
        '--track-pin-map', 
        action='store_true', 
        help='Parse XDC files and regenerate pin_map.yaml'
    )
    
    # Audit group (INDEPENDENT from patch application)
    audit_group = parser.add_argument_group('Auditing (Independent)')
    audit_group.add_argument(
        '--pre-audit', 
        action='store_true', 
        help='Run pre-integration validation ONLY (no patch application)'
    )
    audit_group.add_argument(
        '--post-audit', 
        action='store_true', 
        help='Run post-integration validation ONLY (no patch application)'
    )
    
    # Phase execution group
    phase_group = parser.add_argument_group('Phased Execution')
    phase_group.add_argument(
        '--bsvpath', 
        action='store_true', 
        help='Apply only bsvpath phase'
    )
    phase_group.add_argument(
        '--bsv', 
        action='store_true', 
        help='Apply BSV/SoC patches only'
    )
    phase_group.add_argument(
        '--verilog', 
        action='store_true', 
        help='Apply fpga_top.v routing patches only'
    )
    phase_group.add_argument(
        '--xdc', 
        action='store_true', 
        help='Apply XDC constraint patches only'
    )
    
    # Framework tooling group (folded in from the former shell scripts)
    tooling_group = parser.add_argument_group('Framework Tooling')
    tooling_group.add_argument(
        '--fresh-clone-verify',
        nargs=2,
        metavar=('CLONE_HW_DIR', 'SRC_GIT_DIR'),
        help='End-to-end fresh-clone verification (replaces '
             'scripts/fresh_clone_verify.sh): reset the clone\'s tracked board '
             'files to HEAD, run the whole flow, and assert that restore '
             'returns the tree to its pristine fresh-clone state.'
    )

    args = parser.parse_args()
    
    # Set verbose mode globally via utils
    set_verbose(args.verbose)

    # Phase selection is shared by patch application and post-audit: an audit
    # must only check the phases that were actually applied.
    phase = 'all'
    if args.bsvpath:
        phase = 'bsvpath'
    elif args.bsv:
        phase = 'bsv'
    elif args.verilog:
        phase = 'verilog'
    elif args.xdc:
        phase = 'xdc'
    
    # === FRAMEWORK TOOLING MODES (no soc_build_config.yaml needed) ===
    if args.fresh_clone_verify:
        clone_hw, src_git = args.fresh_clone_verify
        ok = fresh_clone_verify(Path(clone_hw), Path(src_git))
        sys.exit(0 if ok else 1)

    # Load configuration
    config = load_yaml(args.config)
    
    # === YAML MIGRATION MODE ===
    if args.check_migration:
        log_info("[CLI] Migration check mode activated")
        needed = check_migration_needed(args.config)
        if needed:
            log_warn("[CLI] Migration recommended: config contains auto-generated keys.")
            log_info("[CLI] Run with --migrate --dry-run to preview changes safely.")
            sys.exit(1)
        else:
            log_pass("[CLI] Config is already minimal. No migration needed.")
            sys.exit(0)
    
    if args.migrate:
        log_info("[CLI] YAML migration mode activated")
        success = migrate_soc_config(args.config, dry_run=args.dry_run)
        sys.exit(0 if success else 1)
    
    # === PIN TRACKING MODE ===
    if args.track_pin_map:
        log_info("[CLI] Pin tracking mode activated")
        board_dir = config.get('board_dir', 'boards/nexys_video')
        master_xdc = config.get('master_xdc_path', f'{board_dir}/master_constraints.xdc')
        active_xdc = config.get('active_xdc_path', f'{board_dir}/constraints.xdc')
        pin_map_path = config.get('pin_map_path', f'{board_dir}/pin_map.yaml')
        
        analyzer = BoardSupportAnalyzer()
        analyzer.analyze({}, master_xdc, active_xdc)
        analyzer.export_pin_map(
            pin_map_path,
            master_xdc_path=master_xdc,
            active_xdc_path=active_xdc,
            board_name=config.get('target_board', board_dir.split('/')[-1]),
            fpga_part=config.get('fpga_part', '')
        )
        log_pass("[CLI] Pin map updated successfully.")
        return
    
    # === BACKUP MODE ===
    # Snapshot extra files (the hw/ root copies) into the SAME manifest the
    # patch phases use, so one --restore rewinds board sources and root copies
    # together. No-op for files an earlier phase already captured.
    if args.backup:
        log_info("[CLI] Backup mode activated")
        manifest = load_manifest()
        existing = [f for f in args.backup if Path(f).exists()]
        added = snapshot_files(existing, manifest)
        save_manifest(manifest)
        log_pass(f"[CLI] Backed up {added} new file(s); {len(existing) - added} already tracked.")
        return

    # === RESTORE MODE ===
    if args.restore:
        log_info("[CLI] Restore mode activated")
        backup_dir = BACKUP_DIR

        if not backup_dir.exists():
            log_info("[CLI] No backup directory found. Nothing to restore.")
            return

        manifest_path = backup_dir / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            log_info(f"[CLI] Restoring from manifest: {len(manifest['modified'])} modified, {len(manifest.get('created', []))} created")

            for filepath in manifest["modified"]:
                src = backup_dir / filepath
                dst = Path(filepath)
                if src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                else:
                    log_warn(f"[CLI] Manifest lists '{filepath}' but its snapshot is missing; left untouched.")

            for filepath in manifest.get("created", []):
                Path(filepath).unlink(missing_ok=True)
        else:
            log_warn("[CLI] Legacy backup detected. Falling back to directory scan restoration")
            for backup_file in backup_dir.rglob("*"):
                if backup_file.is_file() and backup_file.name != "manifest.json":
                    original_path = backup_file.relative_to(backup_dir)
                    if original_path.exists():
                        shutil.copy2(backup_file, original_path)

        # The baseline is deliberately KEPT. It is the one surviving copy of the
        # pre-automation sources; deleting it here let the next integration run
        # re-cut a baseline from an already-patched tree, which is how a restore
        # silently degrades into a no-op. Use --reset-baseline to drop it.
        log_pass("[CLI] Restore complete. Baseline retained in .automation_backup/")
        return

    # === BOARD BOOTSTRAP MODE ===
    # Seed the anchors a fresh clone lacks, then stop. Integration and pre-audit
    # invoke bootstrap_board_anchors() themselves, so this standalone mode exists
    # mainly for the Makefile `bootstrap_board` target and manual recovery.
    if args.bootstrap_board:
        log_info("[CLI] Board bootstrap mode activated")
        if bootstrap_board_anchors(config, dry_run=args.dry_run):
            log_pass("[CLI] Board bootstrap complete.")
            return
        else:
            log_fail("[CLI] Board bootstrap failed")
            sys.exit(1)

    # === RESET-BASELINE MODE ===
    # Deliberately forget the pristine baseline. Only meaningful from a restored
    # (or otherwise known-good) tree: the NEXT integration run re-captures the
    # baseline from whatever is on disk at that moment.
    if args.reset_baseline:
        if not BACKUP_DIR.exists():
            log_info("[CLI] No baseline to reset.")
            return
        shutil.rmtree(BACKUP_DIR)
        log_pass("[CLI] Baseline discarded. The next integration run will re-capture one.")
        return

    # === AUDIT-ONLY MODES (Independent from patch application) ===
    if args.pre_audit or args.post_audit:
        # A fresh clone lacks the automation anchors, so pre-audit would fail
        # before it began. Seed them first (idempotent) so the anchors every IP
        # def depends on exist on disk. post-audit runs on an already-seeded tree,
        # where this is a no-op.
        if not bootstrap_board_anchors(config, dry_run=args.dry_run):
            log_fail("[CLI] Board bootstrap failed")
            sys.exit(1)

        # Load VFS for audit (no patch application)
        board_dir = config.get('board_dir', 'boards/nexys_video')
        peripherals = config.get('automated_peripherals', [])
        
        rt = ResourceTracker()
        vfs: Dict[str, str] = {}
        
        for p in peripherals:
            ctx = {**p.get('base_context', {}), 'board_dir': board_dir}
            peripheral_yaml = load_yaml(resolve_def_path(p, config))
            for anchor in peripheral_yaml.get('anchors', {}).values():
                filepath = resolve_template(anchor['file'], ctx)
                if Path(filepath).exists():
                    vfs[filepath] = Path(filepath).read_text()
        
        rt.ingest_static_state(
            vfs, 
            config.get('master_xdc_path', f'{board_dir}/master_constraints.xdc'),
            config.get('active_xdc_path', f'{board_dir}/constraints.xdc')
        )
        
        if args.pre_audit:
            log_info("[CLI] Pre-audit mode activated")
            if run_pre_audit(config, vfs, rt):
                log_pass("[CLI] Pre-audit passed")
                sys.exit(0)
            else:
                log_fail("[CLI] Pre-audit failed")
                sys.exit(1)
        
        if args.post_audit:
            log_info(f"[CLI] Post-audit mode activated (phase='{phase}')")
            if run_post_audit(config, vfs, rt, phase):
                log_pass("[CLI] Post-audit passed")
                sys.exit(0)
            else:
                log_fail("[CLI] Post-audit failed")
                sys.exit(1)
    
    # === PATCH APPLICATION MODE (Default) ===
    log_info(f"[CLI] Executing integration: phase='{phase}', dry_run={args.dry_run}")
    
    if execute_integration(config, args.dry_run, phase):
        log_pass("[CLI] Patch application successful")
    else:
        log_fail("[CLI] Patch application failed")
        sys.exit(1)


if __name__ == "__main__":
    main()