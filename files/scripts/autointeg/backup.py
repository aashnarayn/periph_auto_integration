#!/usr/bin/env python3
"""Pre-automation baseline: manifest, snapshots, restore support."""

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


BACKUP_DIR = Path(".automation_backup")

# Board-bootstrap seed root. Lives alongside this engine (scripts/bootstrap/) so a
# board's anchor-seed def ships with the framework, not the IP defs. Resolved from
# __file__ so it works regardless of the current working directory. Per-board seed:
#   scripts/bootstrap/<board>/bootstrap.yaml
BOOTSTRAP_DIR = Path(__file__).resolve().parent / "bootstrap"


def load_manifest() -> Dict[str, List[str]]:
    """Read the backup manifest, or an empty one if no backup exists yet."""
    manifest_path = BACKUP_DIR / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    return {"modified": [], "created": []}


def save_manifest(manifest: Dict[str, List[str]]) -> None:
    BACKUP_DIR.mkdir(exist_ok=True, parents=True)
    (BACKUP_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))


def snapshot_files(filepaths: Any, manifest: Dict[str, List[str]]) -> int:
    """
    Copy each not-yet-tracked file into the backup tree and record it.

    Files already named in the manifest are left alone: the manifest must keep
    the ORIGINAL pre-automation content, so a later phase must never overwrite
    an earlier phase's snapshot with its own (already patched) input.

    Returns the number of newly snapshotted files.
    """
    added = 0
    for filepath in filepaths:
        filepath = str(filepath)
        if filepath in manifest["modified"] or filepath in manifest["created"]:
            continue

        backup_path = BACKUP_DIR / filepath
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        if Path(filepath).exists():
            shutil.copy2(filepath, backup_path)
            manifest["modified"].append(filepath)
        else:
            manifest["created"].append(filepath)
        added += 1

    return added


