#!/usr/bin/env python3
"""
peripheral_registry.py - Static registry of peripheral-specific context generators.

Add new peripherals here. Contains ONLY generators and the registry dict.
Zero CLI logic, zero migration logic, zero side-effects on import.
"""

from typing import Dict, List
from utils import ContextGenerator


class RTCv1ContextGenerator(ContextGenerator):
    """
    Generates standard naming conventions for Shakti RTC v1 peripherals.
    
    AUTO-GENERATED VARIABLES (removed from YAML via --migrate):
        - rtc_instance_name: e.g., "rtc0", "rtc1"
        - rtc_io_interface_name: e.g., "rtc0_io", "rtc1_io"
        - rtc_io_inst_name: e.g., "rtc0_io_inst", "rtc1_io_inst"
        - verilog_wire_name: e.g., "wire_rtc0_out"
        - verilog_port_name: e.g., "rtc0_out_pmod"
        - plic_source: e.g., "rtc0.rtc_sb_interrupt"
    
    USER-REQUIRED IN YAML (preserved by --migrate):
        - rtc_io_port_name: Derived from post-compilation debugsoc.bsv
                           (e.g., "rtc0_io_rtc_clock_signal")
    
    Pattern: instance_id '0' → rtc0, rtc0_io, rtc0_io_inst, etc.
    Fallback: Explicit YAML keys take precedence. Safe for incremental migration.
    """
    
    def get_required_keys(self) -> List[str]:
        """
        List of keys that MUST be provided in base_ctx or instance.
        
        Returns:
            List of required key names.
        """
        # rtc_io_port_name must be explicitly defined by the user
        return ["instance_id", "rtc_io_port_name"]
    
    def get_generated_keys(self) -> List[str]:
        """
        List of keys that this generator can auto-generate.
        
        Used for migration: keys in this list can be stripped from YAML.
        
        Returns:
            List of auto-generated key names.
        """
        return [
            "rtc_instance_name",
            "rtc_io_interface_name",
            "rtc_io_inst_name",
            "verilog_wire_name",
            "verilog_port_name",
            "plic_source",
        ]
    
    def generate(
        self, 
        base_ctx: Dict, 
        instance: Dict, 
        peripheral_name: str
    ) -> Dict[str, str]:
        """
        Generate instance-specific context fields dynamically.
        
        Pure function: no file I/O, no side effects.
        
        Args:
            base_ctx: Shared context from soc_build_config.yaml base_context.
            instance: Per-instance overrides from instances[] list.
            peripheral_name: Name of the peripheral (e.g., "rtc_v1").
            
        Returns:
            Dictionary of generated context fields.
        """
        inst_id = instance.get("instance_id", "0")
        existing = {**base_ctx, **instance}
        generated: Dict[str, str] = {}
        
        # Generate instance names with fallback to explicit YAML values
        if "rtc_instance_name" not in existing:
            generated["rtc_instance_name"] = f"rtc{inst_id}"
        if "rtc_io_interface_name" not in existing:
            generated["rtc_io_interface_name"] = f"rtc{inst_id}_io"
        if "rtc_io_inst_name" not in existing:
            generated["rtc_io_inst_name"] = f"rtc{inst_id}_io_inst"
        
        # Generate Verilog boundary names
        if "verilog_wire_name" not in existing:
            generated["verilog_wire_name"] = f"wire_rtc{inst_id}_out"
        if "verilog_port_name" not in existing:
            generated["verilog_port_name"] = f"rtc{inst_id}_out_pmod"
        
        # Generate PLIC source reference
        if "plic_source" not in existing:
            generated["plic_source"] = f"rtc{inst_id}.rtc_sb_interrupt"
        
        return generated


# =============================================================================
# REGISTRY: Add future peripherals here
# =============================================================================

CONTEXT_GENERATORS: Dict[str, ContextGenerator] = {
    "rtc_v1": RTCv1ContextGenerator(),
    # Example: Add new peripheral generators here
    # "spi_v2": SPIv2ContextGenerator(),
    # "uart_v3": UARTv3ContextGenerator(),
}