from dataclasses import dataclass

@dataclass
class NodeLoad:
    name: str
    cpu_pct: float
    ram_pct: float
    ram_total_mb: float
    vm_count: int
    band: str = "moderate"

@dataclass
class VMInfo:
    vmid: int
    name: str
    node: str
    cpu_pct: float
    ram_max_mb: float
    kind: str = "qemu"
