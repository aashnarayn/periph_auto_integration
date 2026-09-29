"""Shakti GC2025 peripheral auto-integration framework.

Functional modules; `peripheral_auto_integrator.py` is the executor that imports
them and drives the tested flow.

    common      logging, YAML/template helpers, IndentationAnalyzer
    conditional `ifdef criteria, block wrapping, `define site parsing
    patchmodel  instance context, phase classification, comment stripping, gating
    config      def_path/mode resolution, YAML migration
    backup      pre-automation baseline (manifest, snapshot, restore)
    tracker     address/PLIC/macro/pin conflict detection
    xdc         board pin map and XDC constraint editing
    registry    per-peripheral context generators
    handlers    the four patch handlers
    audit       independent pre/post integration audits
    bootstrap   fresh-clone board anchor seeding
    engine      VFS build, phased application, atomic commit
    indent      indentation toolkit (normalize / cleanup / audit)
    verify      end-to-end fresh-clone verification
"""

__all__ = [
    "common", "conditional", "patchmodel", "config", "backup", "tracker",
    "xdc", "registry", "handlers", "audit", "bootstrap", "engine",
    "indent", "verify",
]
