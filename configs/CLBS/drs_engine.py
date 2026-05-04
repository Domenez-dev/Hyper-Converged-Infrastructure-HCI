import time
from typing import Optional

from config import *
from models import NodeLoad, VMInfo
from influx_reader import InfluxReader
from proxmox_api import ProxmoxAPI
from band_calculator import BandCalculator
from logging_setup import get_logger

log = get_logger()

class DRSEngine:
    def __init__(self):
        self.influx = InfluxReader()
        self.pve = ProxmoxAPI()
        self.band_calc = BandCalculator()

        self.last_migration = 0.0
        self.last_migrated_vmid: Optional[int] = None

        self.migration_history = {}
        self.cluster_migrations = []
        self.storm_pause_until = 0.0

    # Cooldowns and safety checks
    def _cooldown_ok(self) -> bool:
        elapsed = time.time() - self.last_migration
        if elapsed < MIGRATION_COOLDOWN:
            log.info(
                f"Cooldown active - {MIGRATION_COOLDOWN - elapsed:.0f}s remaining"
            )
            return False
        return True

    def _vm_cooldown_ok(self, vmid: int, name: str) -> bool:
        """Check per-VM double-migration cooldown (condition 2)."""
        now       = time.time()
        history   = self.migration_history.get(vmid, [])
        recent    = [t for t in history if now - t < VM_DOUBLE_MIGRATION_WINDOW]

        if len(recent) >= 2:
            cooldown_expires = recent[0] + VM_DOUBLE_MIGRATION_COOLDOWN
            remaining        = cooldown_expires - now
            if remaining > 0:
                log.warning(
                    f"{C.vm_name(name)} ({vmid}) migrated {len(recent)}x "
                    f"in last {VM_DOUBLE_MIGRATION_WINDOW/60:.0f}min - "
                    f"personal cooldown {remaining/60:.1f}min remaining"
                )
                return False

        return True

    def _check_migration_storm(self) -> bool:
        """
        Returns True if a migration storm is detected or a storm pause is active.
        Logs CRITICAL and blocks the cycle without killing the process (condition 3).
        """
        now = time.time()

        if now < self.storm_pause_until:
            remaining = self.storm_pause_until - now
            log.critical(
                f"MIGRATION STORM PAUSE ACTIVE - "
                f"{remaining/60:.1f}min remaining - manual cluster review required"
            )
            return True

        self.cluster_migrations = [
            t for t in self.cluster_migrations
            if now - t < STORM_WINDOW
        ]

        if len(self.cluster_migrations) >= STORM_THRESHOLD:
            self.storm_pause_until = now + STORM_PAUSE
            log.critical(
                f"MIGRATION STORM DETECTED - "
                f"{len(self.cluster_migrations)} migrations in "
                f"{STORM_WINDOW/60:.0f}min - "
                f"pausing DRS for {STORM_PAUSE/60:.0f}min - "
                f"manual cluster review required"
            )
            return True

        return False

    def _destination_can_hold(
        self,
        dest: NodeLoad,
        vm: VMInfo,
        upper: float,
    ) -> bool:
        """
        Simulate post-migration load on dest (condition 1).
        Uses vm.cpu_pct as the estimated CPU contribution on the new node,
        and converts vm.ram_max_mb to a percentage of dest total RAM.
        """
        projected_cpu = dest.cpu_pct + vm.cpu_pct
        vm_ram_pct    = (
            (vm.ram_max_mb / dest.ram_total_mb) * 100.0
            if dest.ram_total_mb > 0 else 0.0
        )
        projected_ram = dest.ram_pct + vm_ram_pct

        if projected_cpu > upper:
            log.info(
                f"  Destination {C.node(dest.name, 'light')} rejected - "
                f"projected CPU {C.cpu(projected_cpu)} would exceed "
                f"upper band {C.threshold(upper)}"
            )
            return False

        if projected_ram >= RAM_HIGH:
            log.info(
                f"  Destination {C.node(dest.name, 'light')} rejected - "
                f"projected RAM {C.ram(projected_ram)} would hit limit"
            )
            return False

        log.info(
            f"  Destination {C.node(dest.name, 'light')} accepted - "
            f"projected CPU={C.cpu(projected_cpu)} RAM={C.ram(projected_ram)}"
        )
        return True

    # Node load builder

    def _build_node_loads(self) -> list[NodeLoad]:
        raw_nodes = self.pve.get_nodes()
        nodes     = []

        for n in raw_nodes:
            if n.get("status") != "online":
                continue

            name         = n["node"]
            cpu_pct      = round(n.get("cpu", 0.0) * 100.0, 2)
            mem          = n.get("mem", 0)
            maxmem       = n.get("maxmem", 1)
            ram_pct      = round((mem / maxmem) * 100.0, 2) if maxmem else 0.0
            ram_total_mb = maxmem / 1024 / 1024

            vms         = self.pve.get_vms_on_node(name)
            cts         = self.pve.get_cts_on_node(name)
            running_vms = [v for v in vms if v.get("status") == "running"]
            running_cts = [c for c in cts if c.get("status") == "running"]
            total_count = len(running_vms) + len(running_cts)

            nodes.append(NodeLoad(
                name=name,
                cpu_pct=cpu_pct,
                ram_pct=ram_pct,
                ram_total_mb=ram_total_mb,
                vm_count=total_count,
            ))

        return nodes

    # Phase 2 - Profitability

    def _is_profitable(self, nodes: list[NodeLoad]) -> bool:
        has_heavy = any(n.band == "heavy" for n in nodes)
        has_light = any(n.band == "light" for n in nodes)
        return has_heavy and has_light

    # Phase 3 - Work Transfer Vector

    def _work_transfer_vector(self, heavy_node: NodeLoad, threshold: float) -> float:
        return round(heavy_node.cpu_pct - threshold, 2)

    # Phase 4 - VM+destination selection (combined so we can skip bad pairs)

    def _select_vm_with_destination(
        self,
        node_name: str,
        wtv: float,
        nodes: list[NodeLoad],
        upper: float,
    ) -> tuple[Optional[VMInfo], Optional[NodeLoad]]:
        vm_cpu = self.influx.get_vm_cpu(node_name)
        ct_cpu = self.influx.get_ct_cpu(node_name)

        vms = self.pve.get_vms_on_node(node_name)
        cts = self.pve.get_cts_on_node(node_name)

        running_vms = [
            v for v in vms
            if v.get("status") == "running"
            and v.get("name") not in EXCLUDED_VMS
            and int(v.get("vmid", -1)) not in EXCLUDED_VM_IDS
            and int(v.get("vmid", -1)) != self.last_migrated_vmid
        ]
        running_cts = [
            c for c in cts
            if c.get("status") == "running"
            and c.get("name") not in EXCLUDED_CTS
            and int(c.get("vmid", -1)) not in EXCLUDED_CT_IDS
            and int(c.get("vmid", -1)) != self.last_migrated_vmid
        ]

        all_candidates = (
            [(v, "qemu", vm_cpu) for v in running_vms] +
            [(c, "lxc",  ct_cpu) for c in running_cts]
        )

        if not all_candidates:
            return None, None

        log.info(
            f"Evaluating {len(running_vms)} VMs + {len(running_cts)} CTs "
            f"on {C.node(node_name, 'heavy')} against WTV={C.wtv(wtv)}"
        )

        # Score all candidates - VMs preferred via bonus on containers
        scored = []
        for guest, kind, cpu_map in all_candidates:
            guest_name = guest.get("name", "")
            cpu        = cpu_map.get(guest_name, 0.0)
            diff       = abs(cpu - wtv)
            score      = diff if kind == "qemu" else diff + VM_PRIORITY_BONUS
            kind_label = "VM" if kind == "qemu" else "CT"

            log.info(
                f"  [{kind_label}] {C.candidate(guest_name)} (vmid={guest['vmid']}): "
                f"cpu={C.cpu(cpu)} diff_to_wtv={C.diff(diff)} score={score:.1f}"
            )
            scored.append((score, diff, guest, kind, cpu))

        scored.sort(key=lambda x: x[0])

        # Walk best-to-worst, find first candidate that passes all checks
        for score, diff, guest, kind, cpu in scored:
            vmid       = int(guest["vmid"])
            guest_name = guest.get("name", "")
            kind_label = "VM" if kind == "qemu" else "CT"

            # Per-VM double-migration cooldown
            if not self._vm_cooldown_ok(vmid, guest_name):
                continue

            if kind == "qemu":
                status = self.pve.get_vm_status(node_name, vmid)
            else:
                status = self.pve.get_ct_status(node_name, vmid)

            candidate_vm = VMInfo(
                vmid=vmid,
                name=guest_name,
                node=node_name,
                cpu_pct=cpu,
                ram_max_mb=status.get("maxmem", 0) / 1024 / 1024,
                kind=kind,
            )

            # Find the best light destination that can hold this guest
            dest = self._find_destination(nodes, node_name, candidate_vm, upper)
            if dest:
                log.info(
                    f"Selected [{kind_label}] {C.vm_name(guest_name)} ({vmid}) "
                    f"cpu={C.cpu(cpu)} score={score:.1f}"
                )
                return candidate_vm, dest

            log.info(
                f"  [{kind_label}] {C.candidate(guest_name)} skipped - "
                f"no destination can hold it"
            )

        return None, None

    # Destination picker

    def _find_destination(
        self,
        nodes: list[NodeLoad],
        source_name: str,
        vm: VMInfo,
        upper: float,
        cpu_weight: float = 0.7,
        ram_weight: float = 0.3,
    ) -> Optional[NodeLoad]:
        candidates = [
            n for n in nodes
            if n.band == "light"
            and n.name != source_name
            and n.ram_pct < RAM_HIGH
        ]
        if not candidates:
            return None

        def score(n: NodeLoad) -> float:
            return cpu_weight * n.cpu_pct + ram_weight * n.ram_pct

        for best in sorted(candidates, key=score):
            if self._destination_can_hold(best, vm, upper):
                log.info(
                    f"Destination: {C.node(best.name, 'light')} "
                    f"score={score(best):.2f} "
                    f"cpu={C.cpu(best.cpu_pct)} ram={C.ram(best.ram_pct)}"
                )
                return best

        log.warning("All light node candidates rejected by capacity check")
        return None

    # Migration record keeping

    def _record_migration(self, vmid: int, name: str):
        now = time.time()
        self.last_migration     = now
        self.last_migrated_vmid = vmid
        self.cluster_migrations.append(now)

        if vmid not in self.migration_history:
            self.migration_history[vmid] = []
        self.migration_history[vmid].append(now)

        log.info(
            f"Recorded migration for {C.vm_name(name)} ({vmid}) - "
            f"total cluster migrations in window: {len(self.cluster_migrations)}"
        )

    # Main cycle

    def run_cycle(self):
        log.info("=" * 55)
        log.info("DRS cycle starting")

        # Storm guard - checked before anything else
        if self._check_migration_storm():
            return

        # Phase 1 - Load Evaluation
        nodes = self._build_node_loads()
        if not nodes:
            log.warning("No PVE nodes found, skipping")
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
                f"Heavy node: {C.node(heavy.name, 'heavy')} "
                f"CPU={C.cpu(heavy.cpu_pct)} RAM={C.ram(heavy.ram_pct)}"
            )

            if heavy.vm_count <= 1:
                log.info(
                    f"Skipping {C.node(heavy.name, 'heavy')} - "
                    f"only {heavy.vm_count} guest, nothing to migrate away"
                )
                continue

            # Phase 3 - Work Transfer Vector
            wtv = self._work_transfer_vector(heavy, threshold)
            log.info(
                f"Work Transfer Vector for {C.node(heavy.name, 'heavy')}: {C.wtv(wtv)}"
            )

            # Phase 4 - VM Selection + destination check combined
            vm, dest = self._select_vm_with_destination(heavy.name, wtv, nodes, upper)

            if not vm:
                log.warning(
                    f"No viable guest+destination pair found for {heavy.name}, skipping"
                )
                continue

            log.info(
                f"Plan: move [{vm.kind.upper()}] {C.vm_name(vm.name)} ({vm.vmid}) "
                f"from {C.node(heavy.name, 'heavy')} to {C.node(dest.name, 'light')}"
            )

            # Phase 5 - Migration
            if vm.kind == "qemu":
                result = self.pve.migrate_vm(vm.vmid, heavy.name, dest.name)
            else:
                result = self.pve.migrate_ct(vm.vmid, heavy.name, dest.name)

            task_id = result.get("data", result)
            log.info(f"Migration task submitted: {task_id}")

            self._record_migration(vm.vmid, vm.name)

            # One migration per cycle - re-evaluate on next cycle after cooldown
            break

    def run(self):
        log.info(f"Check interval: {CHECK_INTERVAL}s  Cooldown: {MIGRATION_COOLDOWN}s")

        while True:
            try:
                self.run_cycle()
            except Exception as e:
                log.error(f"DRS cycle failed: {e}", exc_info=True)
            time.sleep(CHECK_INTERVAL)
