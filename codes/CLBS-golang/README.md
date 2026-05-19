# proxmox-drs (CLBS)

Internal service that implements a DRS (Dynamic Resource Scheduling) engine for a Proxmox VE cluster. It continuously evaluates node load, computes migration targets using the CSLB algorithm, and live-migrates VMs/CTs to keep the cluster balanced.

---

## How it works

The engine runs a cycle every 60 seconds and follows five phases lifted from the Chandak et al. CSLB paper:

1. **Load Evaluation** - queries InfluxDB for recent CPU/RAM stats of all `pve-*` nodes and classifies each node into a band: `light`, `moderate`, or `heavy`. The threshold is the mean CPU across all nodes, with a configurable band width applied symmetrically around it.

2. **Profitability Check** - migration only makes sense if at least one `heavy` node and at least one `light` node exist at the same time. If the cluster is already balanced, the cycle exits early.

3. **Work Transfer Vector (WTV)** - for each heavy node, computes how much CPU load needs to be moved off it: `WTV = node_cpu - threshold`.

4. **VM Selection** - scores every running VM and CT on the heavy node by how close their CPU usage is to the WTV. VMs (qemu) are preferred over CTs (lxc gets a +200 score penalty). Excluded guests and guests in per-VM cooldown are skipped.

5. **Migration** - calls the Proxmox API to live-migrate the selected guest to the best light node. Only one migration happens per cycle; the engine then waits out the global cooldown before considering another.

---

## Safety mechanisms

| Mechanism | Value | Description |
|---|---|---|
| Global cooldown | 300s | After any migration, no new migration for 5 minutes |
| Per-VM cooldown | 660s window | A VM migrated twice in 11 minutes is blocked for 30 minutes |
| Storm detection | 6 migrations in 1860s | If the cluster triggers 6+ migrations in ~31 minutes, all migrations pause for 30 minutes |
| RAM hard cap | 85% | A destination node is rejected if its RAM usage is above 85%, regardless of CPU score |
| Min band width | 10% | Bands narrower than 10 CPU percentage points are not acted on |

---

## Configuration

The service reads configuration from a `.env` file in the working directory (or from environment variables directly).

```env
# Proxmox VE
PVE_HOST=
PVE_TOKEN_ID=
PVE_TOKEN_SEC=

# InfluxDB (used to read VM/node CPU metrics)
INFLUX_URL=
INFLUX_TOKEN=
INFLUX_ORG=
INFLUX_BUCKET= 
```

All six variables are required. The service exits at startup with a clear error message if any are missing.

### Hardcoded exclusions

These are defined directly in `main.go` and require a recompile to change:

```go
excludedVMs   = map[string]bool{"monitoring": true, "OPNsense": true, "CLBS": true}
excludedVMIDs = map[int]bool{100: true, 102: true, 106: true}
excludedCTs   = map[string]bool{"monitoring": true}
excludedCTIDs = map[int]bool{102: true}
```

---

## Metrics endpoint

The service exposes a Prometheus-compatible scrape endpoint on `:9101/metrics`.

```
GET http://<clbs-host>:9101/metrics
GET http://<clbs-host>:9101/healthz     # returns "ok"
```

### Available metrics

| Metric | Type | Description |
|---|---|---|
| `clbs_cycles_total` | counter | Total DRS cycles executed |
| `clbs_cycle_errors_total` | counter | Cycles that panicked or errored |
| `clbs_cycle_duration_seconds` | gauge | Duration of the last cycle |
| `clbs_last_cycle_timestamp` | gauge | Unix timestamp of the last cycle |
| `clbs_migrations_total` | counter | Total migrations triggered |
| `clbs_migrations_in_storm_window` | gauge | Migrations counted in the current storm window |
| `clbs_band_threshold_percent` | gauge | Computed CPU threshold (mean of all nodes) |
| `clbs_band_lower_percent` | gauge | Lower bound of the moderate band |
| `clbs_band_upper_percent` | gauge | Upper bound of the moderate band |
| `clbs_node_cpu_percent{node}` | gauge | Per-node CPU usage |
| `clbs_node_ram_percent{node}` | gauge | Per-node RAM usage |
| `clbs_node_band{node}` | gauge | Band classification: 0=light, 1=moderate, 2=heavy |
| `clbs_storm_pause_active` | gauge | 1 if storm pause is currently active |
| `clbs_storm_pause_remaining_seconds` | gauge | Seconds left in the storm pause |
| `clbs_global_cooldown_active` | gauge | 1 if global cooldown is active |
| `clbs_global_cooldown_remaining_seconds` | gauge | Seconds left in the global cooldown |
| `clbs_migration_info{vmid,vm,kind,src,dst}` | gauge | Recent migrations (value = unix timestamp, last 24h) |

---

## Deployment

The service runs as a systemd unit on `192.168.10.104`.

```
/opt/proxmox-drs/
  proxmox-drs     # compiled binary
  .env            # credentials and endpoints
```

### Systemd unit (reference)

```ini
[Unit]
Description=Proxmox DRS - CSLB Load Balancer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/proxmox-drs
EnvironmentFile=/opt/proxmox-drs/.env
ExecStart=/opt/proxmox-drs/proxmox-drs
Restart=always
RestartSec=10
```

### Manual build and deploy

```bash
# on the CLBS machine
cd /path/to/repo
make build
sudo cp proxmox-drs /opt/proxmox-drs/proxmox-drs
sudo cp .env /opt/proxmox-drs/.env
sudo systemctl restart proxmox-drs
```

### Checking status

```bash
systemctl status proxmox-drs
journalctl -u proxmox-drs -f
```

---

## Build

```bash
make build    # produces ./proxmox-drs
make tidy     # go mod tidy
make lint     # golangci-lint (must be installed)
make clean    # removes the binary
```

The binary embeds the git version via ldflags (`-X main.version`).

---

## Logs

The service logs to stderr (captured by journald under systemd). Log levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Output is color-coded when running in a terminal.
