#!/usr/bin/env python3
"""
Proxmox DRS - Central Scheduler Load Balancing (CSLB)
Based on the algorithm described in:
  "Dynamic Load Balancing of Virtual Machines using QEMU-KVM"
  Chandak et al., IJCA Vol.46 No.6, May 2012

Five phases:
  1. Load Evaluation      - compute threshold bands from CPU usage
  2. Profitability Check  - only migrate if heavy+light bands both exist
  3. Work Transfer Vector - how much CPU to move off the heavy node
  4. VM Selection         - pick the VM whose CPU usage is closest to WTV
  5. VM Migration         - live-migrate via Proxmox API

Runs as a service inside a Proxmox VM or LXC container.
"""

import time
import logging
import requests
import os
import dotenv
from influxdb_client import InfluxDBClient
from dataclasses import dataclass, field
from typing import Optional
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DRS] %(levelname)s %(message)s"
)
log = logging.getLogger("drs")

# Config
dotenv.load_dotenv()

INFLUX_URL    = os.getenv("INFLUX_URL")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN")
INFLUX_ORG    = os.getenv("INFLUX_ORG")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET")

PVE_HOST      = os.getenv("PVE_HOST")
PVE_TOKEN_ID  = os.getenv("PVE_TOKEN_ID")
PVE_TOKEN_SEC = os.getenv("PVE_TOKEN_SEC")

# CSLB tuning
RAM_HIGH           = 85.0   # % - never migrate TO a node above this RAM usage
CHECK_INTERVAL     = 300    # seconds between DRS cycles
MIGRATION_COOLDOWN = 300    # seconds to wait after any migration
MIN_BAND_WIDTH     = 10.0   # minimum half-width of moderate band
PVE_NODE_PREFIX    = "pve-"


# Data classes

@dataclass
class NodeLoad:
    name: str
    cpu_pct: float
    ram_pct: float
    vm_count: int
    band: str = "moderate"   # "light" | "moderate" | "heavy"

@dataclass
class VMInfo:
    vmid: int
    name: str
    node: str
    cpu_pct: float
    ram_max_mb: float

# CSLB Band Calculator  (Phase 1 - Load Evaluation)

class BandCalculator:
    """
        threshold  = mean(cpu_i for all nodes)
        mean       = (cpu_min + cpu_max) / 2
        diff       = |threshold - mean|
        half_width = max(diff, MIN_BAND_WIDTH)
        moderate   = [threshold - half_width, threshold + half_width]
    """

    def compute(self, nodes: list[NodeLoad]) -> tuple[float, float, float]:
        # Returns (threshold, lower_bound, upper_bound) for the moderate band.

        if not nodes:
            return 0.0, 0.0, 0.0

        cpu_values = [n.cpu_pct for n in nodes]
        threshold  = sum(cpu_values) / len(cpu_values)
        cpu_min    = min(cpu_values)
        cpu_max    = max(cpu_values)
        mean_val   = (cpu_min + cpu_max) / 2.0
        diff       = abs(threshold - mean_val)
        half_width = max(diff, MIN_BAND_WIDTH)

        lower = threshold - half_width
        upper = threshold + half_width

        log.info(
            f"Band calc: threshold={threshold:.1f}% "
            f"mean={mean_val:.1f}% diff={diff:.1f}% "
            f"moderate=[{lower:.1f}%, {upper:.1f}%]"
        )

        for node in nodes:
            if node.cpu_pct > upper:
                node.band = "heavy"
            elif node.cpu_pct < lower:
                node.band = "light"
            else:
                node.band = "moderate"
            log.info(
                f"  {node.name}: CPU={node.cpu_pct:.1f}% "
                f"RAM={node.ram_pct:.1f}% band={node.band} VMs={node.vm_count}"
            )

        return threshold, lower, upper


# InfluxDB reader

class InfluxReader:
    def __init__(self):
        self.client    = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        self.query_api = self.client.query_api()

    def _query(self, flux: str) -> dict:
        result = {}
        for table in self.query_api.query(flux):
            for record in table.records:
                host  = record.values.get("host", "")
                value = record.get_value()
                if host and value is not None:
                    result[host] = round(float(value), 2)
        return result

    def get_node_cpu(self) -> dict[str, float]:
        # CPU usage % per PVE node (host must start with PVE_NODE_PREFIX).

        flux = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: -5m)
          |> filter(fn: (r) => r["_measurement"] == "cpu")
          |> filter(fn: (r) => r["_field"] == "usage_idle")
          |> filter(fn: (r) => r["cpu"] == "cpu-total")
          |> group(columns: ["host"])
          |> mean()
        '''
        raw = self._query(flux)
        return {
            host: round(100.0 - idle, 2)
            for host, idle in raw.items()
            if host.startswith(PVE_NODE_PREFIX)
        }

    def get_node_ram(self) -> dict[str, float]:
        # RAM usage % per PVE node.

        flux = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: -5m)
          |> filter(fn: (r) => r["_measurement"] == "mem")
          |> filter(fn: (r) => r["_field"] == "used_percent")
          |> group(columns: ["host"])
          |> mean()
        '''
        raw = self._query(flux)
        return {
            host: val
            for host, val in raw.items()
            if host.startswith(PVE_NODE_PREFIX)
        }

    def get_vm_cpu(self) -> dict[str, float]:
        """CPU usage % per guest VM (hosts that do NOT start with PVE_NODE_PREFIX)."""
        flux = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: -5m)
          |> filter(fn: (r) => r["_measurement"] == "cpu")
          |> filter(fn: (r) => r["_field"] == "usage_idle")
          |> filter(fn: (r) => r["cpu"] == "cpu-total")
          |> group(columns: ["host"])
          |> mean()
        '''
        raw = self._query(flux)
        return {
            host: round(100.0 - idle, 2)
            for host, idle in raw.items()
            if not host.startswith(PVE_NODE_PREFIX)
        }


# Proxmox API

class ProxmoxAPI:
    def __init__(self):
        self.base    = PVE_HOST.rstrip("/")
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            "Authorization": f"PVEAPIToken={PVE_TOKEN_ID}={PVE_TOKEN_SEC}"
        })

    def _get(self, path: str) -> dict:
        r = self.session.get(f"{self.base}/api2/json{path}")
        r.raise_for_status()
        return r.json()["data"]

    def get_nodes(self) -> list:
        return self._get("/nodes")

    def get_vms_on_node(self, node: str) -> list:
        return self._get(f"/nodes/{node}/qemu")

    def get_vm_status(self, node: str, vmid: int) -> dict:
        return self._get(f"/nodes/{node}/qemu/{vmid}/status/current")

    def migrate_vm(self, vmid: int, source_node: str, target_node: str) -> dict:
        log.info(f"Migrating VM {vmid} from {source_node} to {target_node}")
        r = self.session.post(
            f"{self.base}/api2/json/nodes/{source_node}/qemu/{vmid}/migrate",
            json={
                "target":          target_node,
                "online":          1,   # live migration - VM keeps running
                "with-local-disks": 0   # disks are on shared storage (Ceph/NFS)
            }
        )
        r.raise_for_status()
        return r.json()


# DRS Engine - CSLB

class DRSEngine:

    def __init__(self):
        self.influx         = InfluxReader()
        self.pve            = ProxmoxAPI()
        self.band_calc      = BandCalculator()
        self.last_migration = 0.0

    # Helpers

    def _cooldown_ok(self) -> bool:
        elapsed = time.time() - self.last_migration
        if elapsed < MIGRATION_COOLDOWN:
            log.info(f"Cooldown active - {MIGRATION_COOLDOWN - elapsed:.0f}s remaining")
            return False
        return True

    def _build_node_loads(self) -> list[NodeLoad]:
        cpu_data = self.influx.get_node_cpu()
        ram_data = self.influx.get_node_ram()
        nodes    = []

        for name, cpu in cpu_data.items():
            ram  = ram_data.get(name, 0.0)
            vms  = self.pve.get_vms_on_node(name)
            running = [v for v in vms if v.get("status") == "running"]
            nodes.append(NodeLoad(
                name=name,
                cpu_pct=cpu,
                ram_pct=ram,
                vm_count=len(running)
            ))

        return nodes

    # Phase 2 - Profitability: needs at least one heavy AND one light node

    def _is_profitable(self, nodes: list[NodeLoad]) -> bool:
        has_heavy = any(n.band == "heavy" for n in nodes)
        has_light = any(n.band == "light" for n in nodes)
        return has_heavy and has_light

    # Phase 3 - Work Transfer Vector
    # How much CPU to move off the heavy node so it reaches the threshold

    def _work_transfer_vector(self, heavy_node: NodeLoad, threshold: float) -> float:
        return round(heavy_node.cpu_pct - threshold, 2)

    # Phase 4 - VM Selection
    # Pick the VM whose CPU usage is closest to the WTV

    def _select_vm(self, node_name: str, wtv: float) -> Optional[VMInfo]:
        vm_cpu  = self.influx.get_vm_cpu()
        vms     = self.pve.get_vms_on_node(node_name)
        running = [v for v in vms if v.get("status") == "running"]

        if not running:
            return None

        best     = None
        best_diff = float("inf")

        for vm in running:
            vm_name = vm.get("name", "")
            cpu     = vm_cpu.get(vm_name, 0.0)
            diff    = abs(cpu - wtv)

            if diff < best_diff:
                best_diff = diff
                status    = self.pve.get_vm_status(node_name, vm["vmid"])
                best = VMInfo(
                    vmid=vm["vmid"],
                    name=vm_name,
                    node=node_name,
                    cpu_pct=cpu,
                    ram_max_mb=status.get("maxmem", 0) / 1024 / 1024
                )

        if best:
            log.info(
                f"Selected VM: {best.name} (vmid={best.vmid}) "
                f"CPU={best.cpu_pct:.1f}% WTV={wtv:.1f}%"
            )
        return best

    # Destination picker - lightest CPU node that can accept the VM

    def _find_destination(
        self,
        nodes: list[NodeLoad],
        source_name: str,
    ) -> Optional[NodeLoad]:
        # Only consider lightly loaded nodes with RAM headroom
        candidates = [
            n for n in nodes
            if n.band == "light"
            and n.name != source_name
            and n.ram_pct < RAM_HIGH
        ]
        if not candidates:
            return None
        # Pick the one with the most CPU headroom
        return sorted(candidates, key=lambda n: n.cpu_pct)[0]

    # Main cycle

    def run_cycle(self):
        log.info("=" * 55)
        log.info("DRS cycle starting")

        # Phase 1 - Load Evaluation
        nodes = self._build_node_loads()
        if not nodes:
            log.warning("No PVE nodes found in InfluxDB, skipping")
            return

        threshold, lower, upper = self.band_calc.compute(nodes)

        # Phase 2 - Profitability
        if not self._is_profitable(nodes):
            log.info("No heavy+light pair found - cluster is balanced, nothing to do")
            return

        if not self._cooldown_ok():
            return

        heavy_nodes = [n for n in nodes if n.band == "heavy"]

        for heavy in heavy_nodes:
            log.warning(
                f"Heavy node: {heavy.name} CPU={heavy.cpu_pct:.1f}% "
                f"RAM={heavy.ram_pct:.1f}%"
            )

            # Phase 3 - Work Transfer Vector
            wtv = self._work_transfer_vector(heavy, threshold)
            log.info(f"Work Transfer Vector for {heavy.name}: {wtv:.1f}%")

            # Phase 4 - VM Selection
            vm = self._select_vm(heavy.name, wtv)
            if not vm:
                log.warning(f"No running VMs on {heavy.name}, skipping")
                continue

            # Destination
            dest = self._find_destination(nodes, heavy.name)
            if not dest:
                log.warning("No light node available as migration target")
                continue

            log.info(
                f"Plan: move VM {vm.name} ({vm.vmid}) "
                f"from {heavy.name} to {dest.name}"
            )

            # Phase 5 - VM Migration
            result = self.pve.migrate_vm(vm.vmid, heavy.name, dest.name)
            log.info(f"Migration submitted: {result}")
            self.last_migration = time.time()

            # Only one migration per cycle to avoid thundering herd
            # Re-evaluate bands on next cycle after cooldown
            break

    def run(self):
        log.info("Proxmox DRS (CSLB) engine started")
        log.info(f"Check interval: {CHECK_INTERVAL}s  Cooldown: {MIGRATION_COOLDOWN}s")
        while True:
            try:
                self.run_cycle()
            except Exception as e:
                log.error(f"DRS cycle failed: {e}", exc_info=True)
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    DRSEngine().run()
