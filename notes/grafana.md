Let's tackle these in order: HA fix first, then DRS design, then dashboards.

---

# Part 1: Fix HA

## Why HA is not migrating automatically

HA in Proxmox requires **three things** all working simultaneously. If any one fails, HA does nothing:

```
1. Cluster quorum (corosync healthy, all nodes seeing each other)
2. Fencing configured (so HA can safely kill a node before migrating its VMs)
3. VM/CT disk on shared storage (Ceph in your case)
```

The most common reason HA silently fails is **fencing is not configured**. Without fencing, HA refuses to migrate because it cannot guarantee the source node is actually dead and not just partitioned. Running the same VM on two nodes simultaneously would corrupt data.

## Step 1: Check current HA status

In the Proxmox web UI go to **Datacenter -> HA -> Status tab**. You should see:

```
quorum OK
master: pve-02 (or whichever node is HA master)
```

If quorum shows `NOK` that is your first problem and corosync needs fixing before anything else.

Also check **Datacenter -> HA -> Resources** and confirm your VMs and the monitoring CT are listed with state `started`.

## Step 2: Configure fencing (watchdog)

Fencing is what allows HA to safely declare a node dead. On each PVE node run:

```bash
# Check if watchdog is loaded
lsmod | grep watchdog

# Load the software watchdog (simplest option, no hardware needed)
echo "softdog" >> /etc/modules
modprobe softdog

# Verify it loaded
lsmod | grep softdog
```

Then configure the HA watchdog on each node:

```bash
vim /etc/pve/watchdog.conf
```

```
watchdog-mux-socket /run/watchdog-mux.sock
```

Then enable and start the watchdog service:

```bash
systemctl enable --now pve-ha-lrm
systemctl enable --now pve-ha-crm
systemctl enable --now watchdog-mux
```

Verify on each node:

```bash
systemctl status pve-ha-lrm
systemctl status pve-ha-crm
```

Both should show `active (running)`.

## Step 3: Verify shared storage for HA resources

In the Proxmox web UI, click on your monitoring CT (102), go to **Hardware**. The disk must show your Ceph pool name, something like `ceph-pool:vm-102-disk-0`. If it shows `local-lvm` or `local`, HA cannot migrate it because local storage is not accessible from other nodes.

If the disk is on local storage you need to move it:

Go to CT 102 -> **Hardware** -> select the disk -> **Move Volume** -> select your Ceph pool -> confirm.

## Step 4: Set up HA groups

HA groups let you control which nodes a VM prefers and what happens during failover. In the web UI go to **Datacenter -> HA -> Groups -> Create**:

```
ID:          production-group
Nodes:       pve-01:100, pve-02:90, pve-03:80
             (priority 100 = preferred, lower = fallback)
Restricted:  No   (if yes, VM only runs on nodes in this group)
Nofailback:  No   (VM returns to preferred node when it recovers)
```

Then assign this group to your HA resources: **Datacenter -> HA -> Resources** -> click your VM -> Edit -> set Group to `production-group`.

## Step 5: Test HA properly

The right way to test HA is not pulling the power cord. Use the Proxmox fencing simulation:

```bash
# On pve-01, simulate a node failure gracefully
# This tells HA "pretend this node is dead" without actually killing it
ha-manager crm-command node-fence pve-03
```

Watch **Datacenter -> HA -> Status** in the web UI. Within 60-120 seconds you should see the VMs that were on pve-03 start appearing on pve-01 or pve-02.

To recover the fenced node:

```bash
ha-manager crm-command node-unfence pve-03
```

---

# Part 2: DRS Design

## The architecture

```
InfluxDB  <----  Telegraf (on each PVE node + inside each VM)
    |
    v
Python DRS script (runs as systemd service on monitoring CT)
    |
    v
Proxmox API  (executes live migrations when thresholds exceeded)
```

The script runs every 60 seconds, queries InfluxDB for the last 5 minutes of metrics, calculates load scores per node, decides if rebalancing is needed, picks the best VM to move and the best destination node, then calls the Proxmox API to live migrate.

## Metrics the DRS script needs

For **node-level decisions** (is this node overloaded):

| Metric             | Source          | Threshold to trigger migration |
| ------------------ | --------------- | ------------------------------ |
| CPU usage %        | Telegraf system | > 80% sustained 5 min          |
| RAM usage %        | Telegraf system | > 85% sustained 5 min          |
| Network throughput | Telegraf net    | > 80% of link capacity         |
| Disk IO wait       | Telegraf diskio | > 30% iowait sustained         |
| Ceph OSD latency   | Telegraf ceph   | > 50ms write latency           |

For **VM-level decisions** (which VM to move off the loaded node):

| Metric         | Source                            | Why it matters                          |
| -------------- | --------------------------------- | --------------------------------------- |
| VM CPU usage % | Telegraf inside VM or Proxmox API | Pick the heaviest consumer              |
| VM RAM usage % | Proxmox API                       | Ensure destination has enough RAM       |
| VM disk IO     | Telegraf inside VM                | Avoid moving IO-heavy VMs to busy nodes |
| VM network IO  | Proxmox API                       | Migration cost estimate                 |
| VM uptime      | Proxmox API                       | Avoid migrating recently started VMs    |

For **migration decisions** (where to move the VM):

| Metric                                      | Why                            |
| ------------------------------------------- | ------------------------------ |
| Destination node CPU headroom               | Must have enough free CPU      |
| Destination node RAM headroom               | Must have enough free RAM      |
| Live migration network (VLAN 50) throughput | Is migration network congested |
| Number of recent migrations                 | Avoid migration storms         |

## Install Telegraf inside each VM

For VM-level metrics the DRS script needs Telegraf running inside each guest VM. On each guest (Debian/Ubuntu based):

```bash
curl -s https://repos.influxdata.com/influxdata-archive_compat.key \
  | gpg --dearmor -o /etc/apt/trusted.gpg.d/influxdata.gpg

echo 'deb [signed-by=/etc/apt/trusted.gpg.d/influxdata.gpg] https://repos.influxdata.com/debian stable main' \
  > /etc/apt/sources.list.d/influxdb.list

apt update && apt install -y telegraf
```

Config for inside guest VMs is simpler than the node config:

```toml
# /etc/telegraf/telegraf.conf (inside a guest VM)
[agent]
  interval = "10s"
  hostname = "haproxy-vm"   # use the actual VM name here

[[outputs.influxdb_v2]]
  urls = ["http://192.168.10.99:8086"]
  token = "YOUR_TELEGRAF_WRITE_TOKEN"
  organization = "proxmox-lab"
  bucket = "proxmox-metrics"

[[inputs.cpu]]
  percpu = false
  totalcpu = true

[[inputs.mem]]

[[inputs.disk]]
  ignore_fs = ["tmpfs", "devtmpfs"]

[[inputs.net]]

[[inputs.processes]]
```

## The DRS Python script structure

Here is the full script skeleton. Save this as `/opt/drs/drs.py` on the monitoring container:

```python
#!/usr/bin/env python3
"""
Proxmox DRS - Automatic load balancing service
Reads metrics from InfluxDB, balances VMs across PVE nodes via Proxmox API
"""

import time
import logging
import requests
from influxdb_client import InfluxDBClient
from dataclasses import dataclass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DRS] %(levelname)s %(message)s"
)
log = logging.getLogger("drs")

# Config
INFLUX_URL    = "http://localhost:8086"
INFLUX_TOKEN  = "YOUR_TOKEN"
INFLUX_ORG    = "proxmox-lab"
INFLUX_BUCKET = "proxmox-metrics"

PVE_HOST      = "https://192.168.10.11:8006"
PVE_TOKEN_ID  = "ansible@pam!drs"
PVE_TOKEN_SEC = "YOUR_PVE_TOKEN"

# Thresholds
CPU_HIGH      = 80.0   # % - node is overloaded above this
CPU_LOW       = 30.0   # % - node is underloaded below this
RAM_HIGH      = 85.0   # %
CHECK_INTERVAL = 60    # seconds between DRS cycles
MIGRATION_COOLDOWN = 300  # seconds between migrations (avoid storms)

@dataclass
class NodeLoad:
    name: str
    cpu_pct: float
    ram_pct: float
    vm_count: int

@dataclass
class VMLoad:
    vmid: int
    name: str
    node: str
    cpu_pct: float
    ram_mb: float
    ram_max_mb: float


class InfluxReader:
    def __init__(self):
        self.client = InfluxDBClient(
            url=INFLUX_URL,
            token=INFLUX_TOKEN,
            org=INFLUX_ORG
        )
        self.query_api = self.client.query_api()

    def query(self, flux):
        return self.query_api.query(flux)

    def get_node_cpu(self):
        """Returns dict: {node_name: cpu_usage_pct}"""
        flux = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: -5m)
          |> filter(fn: (r) => r["_measurement"] == "cpu")
          |> filter(fn: (r) => r["_field"] == "usage_idle")
          |> filter(fn: (r) => r["cpu"] == "cpu-total")
          |> group(columns: ["host"])
          |> mean()
        '''
        result = {}
        for table in self.query(flux):
            for record in table.records:
                host = record.values["host"]
                idle = record.get_value()
                result[host] = round(100.0 - idle, 2)
        return result

    def get_node_ram(self):
        """Returns dict: {node_name: ram_usage_pct}"""
        flux = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: -5m)
          |> filter(fn: (r) => r["_measurement"] == "mem")
          |> filter(fn: (r) => r["_field"] == "used_percent")
          |> group(columns: ["host"])
          |> mean()
        '''
        result = {}
        for table in self.query(flux):
            for record in table.records:
                result[record.values["host"]] = round(record.get_value(), 2)
        return result

    def get_vm_cpu(self):
        """Returns dict: {vm_name: cpu_pct} for all guest VMs"""
        flux = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: -5m)
          |> filter(fn: (r) => r["_measurement"] == "cpu")
          |> filter(fn: (r) => r["_field"] == "usage_idle")
          |> filter(fn: (r) => r["cpu"] == "cpu-total")
          |> group(columns: ["host"])
          |> mean()
        '''
        # Filter out PVE nodes (they start with "pve-")
        result = {}
        for table in self.query(flux):
            for record in table.records:
                host = record.values["host"]
                if not host.startswith("pve-"):
                    idle = record.get_value()
                    result[host] = round(100.0 - idle, 2)
        return result


class ProxmoxAPI:
    def __init__(self):
        self.base = PVE_HOST
        self.headers = {
            "Authorization": f"PVEAPIToken={PVE_TOKEN_ID}={PVE_TOKEN_SEC}"
        }
        self.session = requests.Session()
        self.session.verify = False  # self-signed cert on PVE
        self.session.headers.update(self.headers)

    def get_nodes(self):
        r = self.session.get(f"{self.base}/api2/json/nodes")
        return r.json()["data"]

    def get_vms_on_node(self, node):
        r = self.session.get(f"{self.base}/api2/json/nodes/{node}/qemu")
        return r.json()["data"]

    def get_vm_status(self, node, vmid):
        r = self.session.get(
            f"{self.base}/api2/json/nodes/{node}/qemu/{vmid}/status/current"
        )
        return r.json()["data"]

    def migrate_vm(self, vmid, source_node, target_node):
        log.info(f"Migrating VM {vmid} from {source_node} to {target_node}")
        r = self.session.post(
            f"{self.base}/api2/json/nodes/{source_node}/qemu/{vmid}/migrate",
            json={
                "target": target_node,
                "online": 1,      # live migration, VM stays running
                "with-local-disks": 0  # disks are on Ceph, no need to copy
            }
        )
        return r.json()

    def get_node_status(self, node):
        r = self.session.get(f"{self.base}/api2/json/nodes/{node}/status")
        return r.json()["data"]


class DRSEngine:
    def __init__(self):
        self.influx = InfluxReader()
        self.pve = ProxmoxAPI()
        self.last_migration = 0

    def cooldown_ok(self):
        return (time.time() - self.last_migration) > MIGRATION_COOLDOWN

    def get_node_loads(self):
        cpu_data = self.influx.get_node_cpu()
        ram_data = self.influx.get_node_ram()
        nodes = []
        for node_name, cpu in cpu_data.items():
            if not node_name.startswith("pve-"):
                continue
            ram = ram_data.get(node_name, 0)
            vms = self.pve.get_vms_on_node(node_name)
            running_vms = [v for v in vms if v["status"] == "running"]
            nodes.append(NodeLoad(
                name=node_name,
                cpu_pct=cpu,
                ram_pct=ram,
                vm_count=len(running_vms)
            ))
        return nodes

    def find_overloaded_nodes(self, nodes):
        return [n for n in nodes if n.cpu_pct > CPU_HIGH or n.ram_pct > RAM_HIGH]

    def find_best_destination(self, nodes, source_node, vm_ram_mb):
        candidates = [
            n for n in nodes
            if n.name != source_node
            and n.cpu_pct < CPU_LOW
            and n.ram_pct < RAM_HIGH
        ]
        if not candidates:
            return None
        # Pick the node with the most free CPU
        return sorted(candidates, key=lambda n: n.cpu_pct)[0]

    def pick_vm_to_move(self, node_name):
        """Pick the best VM to migrate off an overloaded node.
        Prefer VMs with high CPU that are not recently started."""
        vm_cpu = self.influx.get_vm_cpu()
        vms = self.pve.get_vms_on_node(node_name)
        running = [v for v in vms if v["status"] == "running"]

        # Sort by CPU usage descending, move the heaviest consumer
        def vm_score(vm):
            name = vm.get("name", "")
            return vm_cpu.get(name, 0)

        running.sort(key=vm_score, reverse=True)
        return running[0] if running else None

    def run_cycle(self):
        log.info("DRS cycle starting")
        nodes = self.get_node_loads()

        for node in nodes:
            log.info(
                f"  {node.name}: CPU={node.cpu_pct}% "
                f"RAM={node.ram_pct}% VMs={node.vm_count}"
            )

        overloaded = self.find_overloaded_nodes(nodes)
        if not overloaded:
            log.info("All nodes balanced, nothing to do")
            return

        if not self.cooldown_ok():
            log.info("Migration cooldown active, skipping this cycle")
            return

        for hot_node in overloaded:
            log.warning(f"Node {hot_node.name} is overloaded")

            vm = self.pick_vm_to_move(hot_node.name)
            if not vm:
                log.warning(f"No migratable VMs found on {hot_node.name}")
                continue

            vm_status = self.pve.get_vm_status(hot_node.name, vm["vmid"])
            vm_ram_mb = vm_status.get("maxmem", 0) / 1024 / 1024

            dest = self.find_best_destination(nodes, hot_node.name, vm_ram_mb)
            if not dest:
                log.warning("No suitable destination node found")
                continue

            result = self.pve.migrate_vm(vm["vmid"], hot_node.name, dest.name)
            log.info(f"Migration result: {result}")
            self.last_migration = time.time()

    def run(self):
        log.info("DRS engine started")
        while True:
            try:
                self.run_cycle()
            except Exception as e:
                log.error(f"DRS cycle failed: {e}")
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    DRSEngine().run()
```

Install dependencies and set it up as a systemd service on the monitoring CT:

```bash
# Inside monitoring CT
apt install -y python3-pip python3-venv

python3 -m venv /opt/drs/venv
/opt/drs/venv/bin/pip install influxdb-client requests

# Create systemd unit
vim /etc/systemd/system/proxmox-drs.service
```

```ini
[Unit]
Description=Proxmox DRS Load Balancer
After=network.target influxdb.service

[Service]
Type=simple
ExecStart=/opt/drs/venv/bin/python3 /opt/drs/drs.py
Restart=always
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now proxmox-drs
journalctl -u proxmox-drs -f
```

Create a dedicated API token for DRS in Proxmox web UI under **Datacenter -> Permissions -> API Tokens -> Add**:

```
User:           root@pam
Token ID:       drs
Privilege sep:  No
```

Then give it the right permissions under **Datacenter -> Permissions -> Add -> API Token Permission**:

```
Path:       /
Token:      root@pam!drs
Role:       PVEAdmin
Propagate:  Yes
```

---

# Part 3: Dashboards

You need exactly **4 dashboards** for your setup. Here is what each one shows and the Flux queries to get the data.

## Dashboard 1: Cluster Overview (daily driver)

**Panel 1: CPU per node - time series**

```flux
from(bucket: "proxmox-metrics")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "cpu")
  |> filter(fn: (r) => r["_field"] == "usage_idle")
  |> filter(fn: (r) => r["cpu"] == "cpu-total")
  |> filter(fn: (r) => r["host"] =~ /^pve-/)
  |> map(fn: (r) => ({r with _value: 100.0 - r._value}))
  |> aggregateWindow(every: v.windowPeriod, fn: mean)
  |> group(columns: ["host"])
```

**Panel 2: RAM per node - time series**

```flux
from(bucket: "proxmox-metrics")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "mem")
  |> filter(fn: (r) => r["_field"] == "used_percent")
  |> filter(fn: (r) => r["host"] =~ /^pve-/)
  |> aggregateWindow(every: v.windowPeriod, fn: mean)
  |> group(columns: ["host"])
```

**Panel 3: Network throughput per node**

```flux
from(bucket: "proxmox-metrics")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "net")
  |> filter(fn: (r) => r["_field"] == "bytes_recv" or r["_field"] == "bytes_sent")
  |> filter(fn: (r) => r["interface"] == "vmbr0")
  |> filter(fn: (r) => r["host"] =~ /^pve-/)
  |> derivative(unit: 1s, nonNegative: true)
  |> aggregateWindow(every: v.windowPeriod, fn: mean)
  |> group(columns: ["host", "_field"])
```

**Panel 4: Stat panels (current values)**

Use the same CPU and RAM queries above but replace `aggregateWindow` with `last()` and display as Stat panel with thresholds: green below 60%, yellow 60-80%, red above 80%.

## Dashboard 2: Ceph Storage Health

**Panel 1: Ceph cluster capacity used vs free**

```flux
from(bucket: "proxmox-metrics")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "ceph")
  |> filter(fn: (r) => r["_field"] == "num_bytes" or r["_field"] == "num_bytes_avail")
  |> filter(fn: (r) => r["host"] == "pve-01")
  |> aggregateWindow(every: v.windowPeriod, fn: last)
  |> group(columns: ["_field"])
```

**Panel 2: OSD write latency per OSD**

```flux
from(bucket: "proxmox-metrics")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "ceph")
  |> filter(fn: (r) => r["_field"] == "op_w_latency_ms")
  |> aggregateWindow(every: v.windowPeriod, fn: mean)
  |> group(columns: ["host", "id"])
```

**Panel 3: Ceph read/write throughput**

```flux
from(bucket: "proxmox-metrics")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "ceph")
  |> filter(fn: (r) => r["_field"] == "op_w_bytes" or r["_field"] == "op_r_bytes")
  |> derivative(unit: 1s, nonNegative: true)
  |> aggregateWindow(every: v.windowPeriod, fn: mean)
  |> group(columns: ["host", "_field"])
```

## Dashboard 3: VM Metrics (DRS input view)

This is the dashboard that mirrors what your DRS script sees. It shows per-VM resource usage so you can visually verify the script is making correct decisions.

**Panel 1: CPU per VM**

```flux
from(bucket: "proxmox-metrics")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "cpu")
  |> filter(fn: (r) => r["_field"] == "usage_idle")
  |> filter(fn: (r) => r["cpu"] == "cpu-total")
  |> filter(fn: (r) => r["host"] !~ /^pve-/)
  |> map(fn: (r) => ({r with _value: 100.0 - r._value}))
  |> aggregateWindow(every: v.windowPeriod, fn: mean)
  |> group(columns: ["host"])
```

**Panel 2: RAM per VM**

```flux
from(bucket: "proxmox-metrics")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "mem")
  |> filter(fn: (r) => r["_field"] == "used_percent")
  |> filter(fn: (r) => r["host"] !~ /^pve-/)
  |> aggregateWindow(every: v.windowPeriod, fn: mean)
  |> group(columns: ["host"])
```

## Dashboard 4: DRS Activity Log

This one you build after the DRS script is running. The script writes migration events to a dedicated InfluxDB measurement. Add this to the DRS script after a successful migration:

```python
from influxdb_client import Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

write_api = self.influx.client.write_api(write_options=SYNCHRONOUS)

point = Point("drs_migration") \
    .tag("source_node", hot_node.name) \
    .tag("dest_node", dest.name) \
    .tag("vm_name", vm.get("name", str(vm["vmid"]))) \
    .field("vmid", vm["vmid"]) \
    .field("reason_cpu", hot_node.cpu_pct) \
    .field("reason_ram", hot_node.ram_pct)

write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
```

Then in Grafana this dashboard shows a table of all migrations: when, which VM, from where, to where, and what triggered it. Extremely useful for debugging the DRS logic.
