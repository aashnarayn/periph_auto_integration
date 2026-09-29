#!/usr/bin/env python3
"""Indentation toolkit CLI -- normalize / cleanup / audit.

Thin executor: the implementation lives in scripts/autointeg/indent.py.
Kept as its own entry point because UserManual sections 6 and 9 document
`python3 scripts/indent_tools.py <subcommand>` directly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from autointeg.indent import main

if __name__ == "__main__":
    sys.exit(main())
