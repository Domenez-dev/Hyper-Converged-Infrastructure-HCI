import requests
from config import PVE_HOST, PVE_TOKEN_ID, PVE_TOKEN_SEC

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
        """Returns nodes with cpu (0.0-1.0), maxcpu, mem, maxmem, status."""
        return self._get("/nodes")

    def get_vms_on_node(self, node: str) -> list:
        try:
            vms = self._get(f"/nodes/{node}/qemu")
            log.debug(f"Got {len(vms)} VMs on {node}")
            return vms
        except requests.HTTPError as e:
            log.error(f"Failed to list VMs on {node}: {e}")
            return []

    def get_cts_on_node(self, node: str) -> list:
        try:
            cts = self._get(f"/nodes/{node}/lxc")
            log.debug(f"Got {len(cts)} containers on {node}")
            return cts
        except requests.HTTPError as e:
            log.error(f"Failed to list containers on {node}: {e}")
            return []

    def get_vm_status(self, node: str, vmid: int) -> dict:
        return self._get(f"/nodes/{node}/qemu/{vmid}/status/current")

    def get_ct_status(self, node: str, vmid: int) -> dict:
        return self._get(f"/nodes/{node}/lxc/{vmid}/status/current")

    def migrate_vm(self, vmid: int, source_node: str, target_node: str) -> dict:
        log.info(
            f"Migrating VM {vmid} from {source_node} to {target_node}"
        )
        r = self.session.post(
            f"{self.base}/api2/json/nodes/{source_node}/qemu/{vmid}/migrate",
            json={
                "target":           target_node,
                "online":           1,
                "with-local-disks": 0,
            }
        )
        r.raise_for_status()
        return r.json()

    def migrate_ct(self, vmid: int, source_node: str, target_node: str) -> dict:
        log.info(
            f"Migrating CT {vmid} from {source_node} to {target_node}"
        )
        r = self.session.post(
            f"{self.base}/api2/json/nodes/{source_node}/lxc/{vmid}/migrate",
            json={
                "target": target_node,
                "online": 1,
            }
        )
        r.raise_for_status()
        return r.json()
