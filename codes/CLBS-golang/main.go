// Proxmox DRS - Central Scheduler Load Balancing (CSLB)
// Based on: "Dynamic Load Balancing of Virtual Machines using QEMU-KVM"
// Chandak et al., IJCA Vol.46 No.6, May 2012
//
// Five phases:
//   1. Load Evaluation      - compute threshold bands from CPU usage
//   2. Profitability Check  - only migrate if heavy+light bands both exist
//   3. Work Transfer Vector - how much CPU to move off the heavy node
//   4. VM Selection         - pick the VM whose CPU usage is closest to WTV
//   5. VM Migration         - live-migrate via Proxmox API

package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"sort"
	"strings"
	"time"

	influxdb2 "github.com/influxdata/influxdb-client-go/v2"
	"github.com/joho/godotenv"
)

// ------------------------------------------------------------
// ANSI color helpers
// ------------------------------------------------------------

const (
	colorReset   = "\033[0m"
	colorGrey    = "\033[38;5;245m"
	colorGreen   = "\033[32m"
	colorYellow  = "\033[33m"
	colorRed     = "\033[31m"
	colorBoldRed = "\033[1;31m"
	colorCyan    = "\033[36m"
	colorTeal    = "\033[38;5;80m"
	colorOrange  = "\033[38;5;214m"
	colorLime    = "\033[38;5;154m"
	colorPurple  = "\033[38;5;183m"
	colorBlue    = "\033[38;5;117m"
	colorBold    = "\033[1m"
)

func pctColor(pct float64) string {
	if pct >= 80 {
		return colorRed
	}
	if pct >= 50 {
		return colorYellow
	}
	return colorGreen
}

func fmtCPU(v float64) string  { return fmt.Sprintf("%s%.1f%%%s", pctColor(v), v, colorReset) }
func fmtRAM(v float64) string  { return fmt.Sprintf("%s%.1f%%%s", pctColor(v), v, colorReset) }
func fmtDiff(v float64) string { return fmt.Sprintf("%s%.1f%%%s", colorBlue, v, colorReset) }
func fmtWTV(v float64) string  { return fmt.Sprintf("%s%.1f%%%s", colorOrange, v, colorReset) }
func fmtThreshold(v float64) string {
	return fmt.Sprintf("%s%s%.1f%%%s", colorTeal, colorBold, v, colorReset)
}
func fmtVMName(name string) string    { return fmt.Sprintf("%s%s%s", colorPurple, name, colorReset) }
func fmtCandidate(name string) string { return fmt.Sprintf("%s%s%s", colorCyan, name, colorReset) }

func fmtNode(name, band string) string {
	color := colorGrey
	switch band {
	case "heavy":
		color = colorOrange
	case "light":
		color = colorLime
	}
	return fmt.Sprintf("%s%s%s%s", color, colorBold, name, colorReset)
}

// ------------------------------------------------------------
// Colored logger
// ------------------------------------------------------------

type level int

const (
	lvlDebug level = iota
	lvlInfo
	lvlWarning
	lvlError
	lvlCritical
)

type colorLog struct {
	out *os.File
}

// Write directly to stderr with an explicit Sync() so output is immediate
// whether running in a terminal, piped, or under systemd/journald.
var logger = &colorLog{out: os.Stderr}

func (l *colorLog) log(lvl level, msg string) {
	ts := time.Now().Format("2006-01-02 15:04:05")
	var lvlColor, lvlName string
	switch lvl {
	case lvlDebug:
		lvlColor, lvlName = colorCyan, "DEBUG"
	case lvlInfo:
		lvlColor, lvlName = colorGreen, "INFO"
	case lvlWarning:
		lvlColor, lvlName = colorYellow, "WARNING"
	case lvlError:
		lvlColor, lvlName = colorRed, "ERROR"
	case lvlCritical:
		lvlColor, lvlName = colorBoldRed, "CRITICAL"
	}
	line := fmt.Sprintf(
		"%s%s%s [%s%s%s] %s\n",
		colorGrey, ts, colorReset,
		lvlColor, lvlName, colorReset,
		msg,
	)
	fmt.Fprint(l.out, line)
}

func (l *colorLog) Debug(f string, a ...any)    { l.log(lvlDebug, fmt.Sprintf(f, a...)) }
func (l *colorLog) Info(f string, a ...any)     { l.log(lvlInfo, fmt.Sprintf(f, a...)) }
func (l *colorLog) Warning(f string, a ...any)  { l.log(lvlWarning, fmt.Sprintf(f, a...)) }
func (l *colorLog) Error(f string, a ...any)    { l.log(lvlError, fmt.Sprintf(f, a...)) }
func (l *colorLog) Critical(f string, a ...any) { l.log(lvlCritical, fmt.Sprintf(f, a...)) }

// ------------------------------------------------------------
// Config
// ------------------------------------------------------------

const (
	metricsAddr = ":9101" // Prometheus scrape endpoint

	ramHigh                   = 85.0
	checkInterval             = 60 * time.Second
	migrationCooldown         = 300 * time.Second
	minBandWidth              = 10.0
	vmPriorityBonus           = 200.0
	vmDoubleMigrationWindow   = 660.0  // seconds
	vmDoubleMigrationCooldown = 1800.0 // seconds
	stormWindow               = 1860.0 // seconds
	stormThreshold            = 6
	stormPause                = 1800.0 // seconds

	pveNodePrefix = "pve-"
)

var (
	excludedVMs   = map[string]bool{"monitoring": true, "OPNsense": true, "CLBS": true}
	excludedVMIDs = map[int]bool{100: true, 102: true, 106: true}
	excludedCTs   = map[string]bool{"monitoring": true}
	excludedCTIDs = map[int]bool{102: true}
)

var (
	influxURL    string
	influxToken  string
	influxOrg    string
	influxBucket string
	pveHost      string
	pveTokenID   string
	pveTokenSec  string
)

func loadConfig() {
	_ = godotenv.Load()

	influxURL = os.Getenv("INFLUX_URL")
	influxToken = os.Getenv("INFLUX_TOKEN")
	influxOrg = os.Getenv("INFLUX_ORG")
	influxBucket = os.Getenv("INFLUX_BUCKET")
	pveHost = os.Getenv("PVE_HOST")
	pveTokenID = os.Getenv("PVE_TOKEN_ID")
	pveTokenSec = os.Getenv("PVE_TOKEN_SEC")

	required := map[string]string{
		"INFLUX_URL":    influxURL,
		"INFLUX_TOKEN":  influxToken,
		"INFLUX_ORG":    influxOrg,
		"INFLUX_BUCKET": influxBucket,
		"PVE_HOST":      pveHost,
		"PVE_TOKEN_ID":  pveTokenID,
		"PVE_TOKEN_SEC": pveTokenSec,
	}

	var missing []string
	for k, v := range required {
		if v == "" {
			missing = append(missing, k)
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		logger.Critical("Missing required environment variables: %s", strings.Join(missing, ", "))
		os.Exit(1)
	}

	if !strings.Contains(pveTokenID, "!") {
		logger.Critical("PVE_TOKEN_ID must be in format user@realm!tokenname, e.g. root@pam!drs")
		os.Exit(1)
	}
}

// ------------------------------------------------------------
// Data types
// ------------------------------------------------------------

type NodeLoad struct {
	Name       string
	CPUPct     float64
	RAMPct     float64
	RAMTotalMB float64
	VMCount    int
	Band       string // "light" | "moderate" | "heavy"
}

type VMInfo struct {
	VMID     int
	Name     string
	Node     string
	CPUPct   float64
	RAMMaxMB float64
	Kind     string // "qemu" | "lxc"
}

// ------------------------------------------------------------
// InfluxDB reader
// ------------------------------------------------------------

type InfluxReader struct {
	client influxdb2.Client
}

func newInfluxReader() *InfluxReader {
	client := influxdb2.NewClient(influxURL, influxToken)
	return &InfluxReader{client: client}
}

func (ir *InfluxReader) queryHostValues(flux string) map[string]float64 {
	result := make(map[string]float64)
	queryAPI := ir.client.QueryAPI(influxOrg)
	res, err := queryAPI.Query(context.Background(), flux)
	if err != nil {
		logger.Error("InfluxDB query error: %v", err)
		return result
	}
	defer res.Close()

	for res.Next() {
		record := res.Record()
		host, ok := record.ValueByKey("host").(string)
		if !ok || host == "" {
			continue
		}
		val, ok := record.Value().(float64)
		if !ok {
			continue
		}
		result[host] = math.Round(val*100) / 100
	}
	return result
}

func (ir *InfluxReader) GetVMCPU(nodeName string) map[string]float64 {
	flux := fmt.Sprintf(`
from(bucket: "%s")
  |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "system")
  |> filter(fn: (r) => r._field == "cpu")
  |> filter(fn: (r) => r["object"] == "qemu")
  |> filter(fn: (r) => r["_value"] > 0)
  |> filter(fn: (r) => r["nodename"] == "%s")
  |> group(columns: ["host"])
  |> mean()
`, influxBucket, nodeName)

	raw := ir.queryHostValues(flux)
	result := make(map[string]float64, len(raw))
	for host, val := range raw {
		result[host] = math.Round(val*100*100) / 100
	}
	return result
}

func (ir *InfluxReader) GetCTCPU(nodeName string) map[string]float64 {
	flux := fmt.Sprintf(`
from(bucket: "%s")
  |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "system")
  |> filter(fn: (r) => r._field == "cpu")
  |> filter(fn: (r) => r["object"] == "lxc")
  |> filter(fn: (r) => r["_value"] > 0)
  |> filter(fn: (r) => r["nodename"] == "%s")
  |> group(columns: ["host"])
  |> mean()
`, influxBucket, nodeName)

	raw := ir.queryHostValues(flux)
	result := make(map[string]float64, len(raw))
	for host, val := range raw {
		result[host] = math.Round(val*100*100) / 100
	}
	return result
}

// ------------------------------------------------------------
// Proxmox API
// ------------------------------------------------------------

type ProxmoxAPI struct {
	base   string
	client *http.Client
	token  string
}

func newProxmoxAPI() *ProxmoxAPI {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
	}
	return &ProxmoxAPI{
		base:   strings.TrimRight(pveHost, "/"),
		client: &http.Client{Transport: transport, Timeout: 30 * time.Second},
		token:  fmt.Sprintf("PVEAPIToken=%s=%s", pveTokenID, pveTokenSec),
	}
}

func (p *ProxmoxAPI) get(path string) (json.RawMessage, error) {
	req, err := http.NewRequest("GET", p.base+"/api2/json"+path, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", p.token)

	resp, err := p.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(body))
	}

	var envelope struct {
		Data json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(body, &envelope); err != nil {
		return nil, err
	}
	return envelope.Data, nil
}

func (p *ProxmoxAPI) post(path string, payload any) (json.RawMessage, error) {
	b, _ := json.Marshal(payload)
	req, err := http.NewRequest("POST", p.base+"/api2/json"+path, bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", p.token)
	req.Header.Set("Content-Type", "application/json")

	resp, err := p.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(body))
	}

	var envelope struct {
		Data json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(body, &envelope); err != nil {
		return nil, err
	}
	return envelope.Data, nil
}

type pveNode struct {
	Node   string  `json:"node"`
	Status string  `json:"status"`
	CPU    float64 `json:"cpu"`
	MaxCPU int     `json:"maxcpu"`
	Mem    int64   `json:"mem"`
	MaxMem int64   `json:"maxmem"`
}

type pveGuest struct {
	VMID   int    `json:"vmid"`
	Name   string `json:"name"`
	Status string `json:"status"`
}

type pveStatus struct {
	MaxMem int64 `json:"maxmem"`
}

func (p *ProxmoxAPI) GetNodes() ([]pveNode, error) {
	data, err := p.get("/nodes")
	if err != nil {
		return nil, err
	}
	var nodes []pveNode
	return nodes, json.Unmarshal(data, &nodes)
}

func (p *ProxmoxAPI) GetVMsOnNode(node string) []pveGuest {
	data, err := p.get("/nodes/" + node + "/qemu")
	if err != nil {
		logger.Error("Failed to list VMs on %s: %v", node, err)
		return nil
	}
	var guests []pveGuest
	_ = json.Unmarshal(data, &guests)
	return guests
}

func (p *ProxmoxAPI) GetCTsOnNode(node string) []pveGuest {
	data, err := p.get("/nodes/" + node + "/lxc")
	if err != nil {
		logger.Error("Failed to list CTs on %s: %v", node, err)
		return nil
	}
	var guests []pveGuest
	_ = json.Unmarshal(data, &guests)
	return guests
}

func (p *ProxmoxAPI) GetVMStatus(node string, vmid int) (pveStatus, error) {
	data, err := p.get(fmt.Sprintf("/nodes/%s/qemu/%d/status/current", node, vmid))
	if err != nil {
		return pveStatus{}, err
	}
	var s pveStatus
	return s, json.Unmarshal(data, &s)
}

func (p *ProxmoxAPI) GetCTStatus(node string, vmid int) (pveStatus, error) {
	data, err := p.get(fmt.Sprintf("/nodes/%s/lxc/%d/status/current", node, vmid))
	if err != nil {
		return pveStatus{}, err
	}
	var s pveStatus
	return s, json.Unmarshal(data, &s)
}

func (p *ProxmoxAPI) MigrateVM(vmid int, src, dst string) (json.RawMessage, error) {
	logger.Info("Migrating VM %d from %s to %s", vmid, src, dst)
	return p.post(
		fmt.Sprintf("/nodes/%s/qemu/%d/migrate", src, vmid),
		map[string]any{
			"target":           dst,
			"online":           1,
			"with-local-disks": 0,
		},
	)
}

func (p *ProxmoxAPI) MigrateCT(vmid int, src, dst string) (json.RawMessage, error) {
	logger.Info("Migrating CT %d from %s to %s", vmid, src, dst)
	return p.post(
		fmt.Sprintf("/nodes/%s/lxc/%d/migrate", src, vmid),
		map[string]any{
			"target": dst,
			"online": 1,
		},
	)
}

// ------------------------------------------------------------
// Phase 1 - Band Calculator
// ------------------------------------------------------------

// BandCalculator implements:
//
//	threshold  = mean(cpu_i for all nodes)
//	mean       = (cpu_min + cpu_max) / 2
//	diff       = |threshold - mean|
//	half_width = max(diff, MIN_BAND_WIDTH)
//	moderate   = [threshold - half_width, threshold + half_width]
type BandCalculator struct{}

func (bc *BandCalculator) Compute(nodes []*NodeLoad) (threshold, lower, upper float64) {
	if len(nodes) == 0 {
		return
	}

	cpuMin, cpuMax, cpuSum := nodes[0].CPUPct, nodes[0].CPUPct, 0.0
	for _, n := range nodes {
		cpuSum += n.CPUPct
		if n.CPUPct < cpuMin {
			cpuMin = n.CPUPct
		}
		if n.CPUPct > cpuMax {
			cpuMax = n.CPUPct
		}
	}

	threshold = cpuSum / float64(len(nodes))
	meanVal := (cpuMin + cpuMax) / 2.0
	diff := math.Abs(threshold - meanVal)
	halfWidth := math.Max(diff, minBandWidth)

	lower = threshold - halfWidth
	upper = threshold + halfWidth

	logger.Info(
		"Band calc: threshold=%s mean=%s diff=%s moderate=[%s, %s]",
		fmtThreshold(threshold), fmtCPU(meanVal), fmtDiff(diff),
		fmtThreshold(lower), fmtThreshold(upper),
	)

	for _, n := range nodes {
		switch {
		case n.CPUPct > upper:
			n.Band = "heavy"
		case n.CPUPct < lower:
			n.Band = "light"
		default:
			n.Band = "moderate"
		}
		logger.Info(
			"  %s: CPU=%s RAM=%s band=%s guests=%d",
			fmtNode(n.Name, n.Band), fmtCPU(n.CPUPct), fmtRAM(n.RAMPct),
			n.Band, n.VMCount,
		)
	}
	return
}

// ------------------------------------------------------------
// DRS Engine
// ------------------------------------------------------------

type DRSEngine struct {
	influx   *InfluxReader
	pve      *ProxmoxAPI
	bandCalc *BandCalculator
	store    *Store

	lastMigration     time.Time
	lastMigratedVMID  int
	migrationHistory  map[int][]time.Time // per-VM timestamps
	clusterMigrations []time.Time         // all migration timestamps (storm guard)
	stormPauseUntil   time.Time
}

func newDRSEngine() *DRSEngine {
	store, err := openStore("/opt/proxmox-drs/clbs.db")
	if err != nil {
		logger.Critical("Failed to open SQLite store: %v", err)
		os.Exit(1)
	}

	e := &DRSEngine{
		influx:           newInfluxReader(),
		pve:              newProxmoxAPI(),
		bandCalc:         &BandCalculator{},
		store:            store,
		migrationHistory: make(map[int][]time.Time),
	}

	// Restore state from the database so a restart doesn't reset cooldowns
	// or lose migration history visible in the metrics endpoint.
	if events, err := store.LoadRecent(); err != nil {
		logger.Warning("Could not load migration history from DB: %v", err)
	} else {
		metrics.LoadMigrations(events)

		// Rebuild in-memory per-VM history and cluster migration list
		// so cooldown and storm-guard logic work correctly after a restart.
		for _, ev := range events {
			e.migrationHistory[ev.VMID] = append(e.migrationHistory[ev.VMID], ev.At)
			e.clusterMigrations = append(e.clusterMigrations, ev.At)
		}
		logger.Info("Restored %d migrations from DB", len(events))
	}

	if vmid, err := store.LastMigratedVMID(); err != nil {
		logger.Warning("Could not read last migrated VMID from DB: %v", err)
	} else {
		e.lastMigratedVMID = vmid
	}

	if t, err := store.LastMigrationTime(); err != nil {
		logger.Warning("Could not read last migration time from DB: %v", err)
	} else if !t.IsZero() {
		e.lastMigration = t
		logger.Info("Last migration was at %s", t.Format("2006-01-02 15:04:05"))
	}

	return e
}

// Cooldown checks

func (e *DRSEngine) cooldownOK() bool {
	elapsed := time.Since(e.lastMigration)
	if elapsed < migrationCooldown {
		remaining := migrationCooldown - elapsed
		logger.Info("Cooldown active - %.0fs remaining", remaining.Seconds())
		return false
	}
	return true
}

func (e *DRSEngine) vmCooldownOK(vmid int, name string) bool {
	now := time.Now()
	window := vmDoubleMigrationWindow * float64(time.Second)

	var recent []time.Time
	for _, t := range e.migrationHistory[vmid] {
		if now.Sub(t) < time.Duration(window) {
			recent = append(recent, t)
		}
	}

	if len(recent) >= 2 {
		cooldownExpires := recent[0].Add(time.Duration(vmDoubleMigrationCooldown * float64(time.Second)))
		if remaining := time.Until(cooldownExpires); remaining > 0 {
			logger.Warning(
				"%s (%d) migrated %dx in last %.0fmin - personal cooldown %.1fmin remaining",
				fmtVMName(name), vmid, len(recent),
				vmDoubleMigrationWindow/60, remaining.Minutes(),
			)
			return false
		}
	}
	return true
}

func (e *DRSEngine) checkMigrationStorm() bool {
	now := time.Now()

	if now.Before(e.stormPauseUntil) {
		logger.Critical(
			"MIGRATION STORM PAUSE ACTIVE - %.1fmin remaining - manual cluster review required",
			time.Until(e.stormPauseUntil).Minutes(),
		)
		return true
	}

	// Prune old entries
	cutoff := now.Add(-time.Duration(stormWindow * float64(time.Second)))
	var fresh []time.Time
	for _, t := range e.clusterMigrations {
		if t.After(cutoff) {
			fresh = append(fresh, t)
		}
	}
	e.clusterMigrations = fresh

	if len(e.clusterMigrations) >= stormThreshold {
		e.stormPauseUntil = now.Add(time.Duration(stormPause * float64(time.Second)))
		logger.Critical(
			"MIGRATION STORM DETECTED - %d migrations in %.0fmin - pausing DRS for %.0fmin - manual cluster review required",
			len(e.clusterMigrations), stormWindow/60, stormPause/60,
		)
		return true
	}
	return false
}

func (e *DRSEngine) destinationCanHold(dest *NodeLoad, vm *VMInfo, upper float64) bool {
	projCPU := dest.CPUPct + vm.CPUPct
	var vmRAMPct float64
	if dest.RAMTotalMB > 0 {
		vmRAMPct = (vm.RAMMaxMB / dest.RAMTotalMB) * 100.0
	}
	projRAM := dest.RAMPct + vmRAMPct

	if projCPU > upper {
		logger.Info(
			"  Destination %s rejected - projected CPU %s would exceed upper band %s",
			fmtNode(dest.Name, "light"), fmtCPU(projCPU), fmtThreshold(upper),
		)
		return false
	}
	if projRAM >= ramHigh {
		logger.Info(
			"  Destination %s rejected - projected RAM %s would hit limit",
			fmtNode(dest.Name, "light"), fmtRAM(projRAM),
		)
		return false
	}

	logger.Info(
		"  Destination %s accepted - projected CPU=%s RAM=%s",
		fmtNode(dest.Name, "light"), fmtCPU(projCPU), fmtRAM(projRAM),
	)
	return true
}

// Phase 1 helper - build node load list

func (e *DRSEngine) buildNodeLoads() []*NodeLoad {
	rawNodes, err := e.pve.GetNodes()
	if err != nil {
		logger.Error("Failed to get nodes: %v", err)
		return nil
	}

	var nodes []*NodeLoad
	for _, n := range rawNodes {
		if n.Status != "online" {
			continue
		}

		cpuPct := math.Round(n.CPU*100*100) / 100
		var ramPct float64
		if n.MaxMem > 0 {
			ramPct = math.Round((float64(n.Mem)/float64(n.MaxMem))*100*100) / 100
		}
		ramTotalMB := float64(n.MaxMem) / 1024 / 1024

		vms := e.pve.GetVMsOnNode(n.Node)
		cts := e.pve.GetCTsOnNode(n.Node)

		runningCount := 0
		for _, v := range vms {
			if v.Status == "running" {
				runningCount++
			}
		}
		for _, c := range cts {
			if c.Status == "running" {
				runningCount++
			}
		}

		nodes = append(nodes, &NodeLoad{
			Name:       n.Node,
			CPUPct:     cpuPct,
			RAMPct:     ramPct,
			RAMTotalMB: ramTotalMB,
			VMCount:    runningCount,
		})
	}
	return nodes
}

// Phase 2 - Profitability check

func (e *DRSEngine) isProfitable(nodes []*NodeLoad) bool {
	var hasHeavy, hasLight bool
	for _, n := range nodes {
		if n.Band == "heavy" {
			hasHeavy = true
		}
		if n.Band == "light" {
			hasLight = true
		}
	}
	return hasHeavy && hasLight
}

// Phase 3 - Work Transfer Vector

func (e *DRSEngine) workTransferVector(heavy *NodeLoad, threshold float64) float64 {
	return math.Round((heavy.CPUPct-threshold)*100) / 100
}

// Phase 4 combined - VM selection + destination

type scored struct {
	score float64
	diff  float64
	guest pveGuest
	kind  string
	cpu   float64
}

func (e *DRSEngine) selectVMWithDestination(
	nodeName string,
	wtv float64,
	nodes []*NodeLoad,
	upper float64,
) (*VMInfo, *NodeLoad) {
	vmCPU := e.influx.GetVMCPU(nodeName)
	ctCPU := e.influx.GetCTCPU(nodeName)

	vms := e.pve.GetVMsOnNode(nodeName)
	cts := e.pve.GetCTsOnNode(nodeName)

	var candidates []scored

	for _, v := range vms {
		if v.Status != "running" {
			continue
		}
		if excludedVMs[v.Name] || excludedVMIDs[v.VMID] || v.VMID == e.lastMigratedVMID {
			continue
		}
		cpu := vmCPU[v.Name]
		diff := math.Abs(cpu - wtv)
		candidates = append(candidates, scored{
			score: diff,
			diff:  diff,
			guest: v,
			kind:  "qemu",
			cpu:   cpu,
		})
	}

	for _, c := range cts {
		if c.Status != "running" {
			continue
		}
		if excludedCTs[c.Name] || excludedCTIDs[c.VMID] || c.VMID == e.lastMigratedVMID {
			continue
		}
		cpu := ctCPU[c.Name]
		diff := math.Abs(cpu - wtv)
		candidates = append(candidates, scored{
			score: diff + vmPriorityBonus,
			diff:  diff,
			guest: c,
			kind:  "lxc",
			cpu:   cpu,
		})
	}

	if len(candidates) == 0 {
		return nil, nil
	}

	vmCount, ctCount := 0, 0
	for _, c := range candidates {
		if c.kind == "qemu" {
			vmCount++
		} else {
			ctCount++
		}
	}
	logger.Info(
		"Evaluating %d VMs + %d CTs on %s against WTV=%s",
		vmCount, ctCount, fmtNode(nodeName, "heavy"), fmtWTV(wtv),
	)

	for _, c := range candidates {
		kindLabel := "VM"
		if c.kind == "lxc" {
			kindLabel = "CT"
		}
		logger.Info(
			"  [%s] %s (vmid=%d): cpu=%s diff_to_wtv=%s score=%.1f",
			kindLabel, fmtCandidate(c.guest.Name), c.guest.VMID,
			fmtCPU(c.cpu), fmtDiff(c.diff), c.score,
		)
	}

	sort.Slice(candidates, func(i, j int) bool {
		return candidates[i].score < candidates[j].score
	})

	for _, c := range candidates {
		vmid := c.guest.VMID
		name := c.guest.Name
		kindLabel := "VM"
		if c.kind == "lxc" {
			kindLabel = "CT"
		}

		if !e.vmCooldownOK(vmid, name) {
			continue
		}

		var maxMem int64
		if c.kind == "qemu" {
			s, err := e.pve.GetVMStatus(nodeName, vmid)
			if err != nil {
				logger.Error("Failed to get VM status for %d: %v", vmid, err)
				continue
			}
			maxMem = s.MaxMem
		} else {
			s, err := e.pve.GetCTStatus(nodeName, vmid)
			if err != nil {
				logger.Error("Failed to get CT status for %d: %v", vmid, err)
				continue
			}
			maxMem = s.MaxMem
		}

		candidateVM := &VMInfo{
			VMID:     vmid,
			Name:     name,
			Node:     nodeName,
			CPUPct:   c.cpu,
			RAMMaxMB: float64(maxMem) / 1024 / 1024,
			Kind:     c.kind,
		}

		dest := e.findDestination(nodes, nodeName, candidateVM, upper)
		if dest != nil {
			logger.Info(
				"Selected [%s] %s (%d) cpu=%s score=%.1f",
				kindLabel, fmtVMName(name), vmid, fmtCPU(c.cpu), c.score,
			)
			return candidateVM, dest
		}

		logger.Info(
			"  [%s] %s skipped - no destination can hold it",
			kindLabel, fmtCandidate(name),
		)
	}

	return nil, nil
}

func (e *DRSEngine) findDestination(
	nodes []*NodeLoad,
	sourceName string,
	vm *VMInfo,
	upper float64,
) *NodeLoad {
	const cpuWeight = 0.7
	const ramWeight = 0.3

	var candidates []*NodeLoad
	for _, n := range nodes {
		if n.Band == "light" && n.Name != sourceName && n.RAMPct < ramHigh {
			candidates = append(candidates, n)
		}
	}
	if len(candidates) == 0 {
		return nil
	}

	sort.Slice(candidates, func(i, j int) bool {
		si := cpuWeight*candidates[i].CPUPct + ramWeight*candidates[i].RAMPct
		sj := cpuWeight*candidates[j].CPUPct + ramWeight*candidates[j].RAMPct
		return si < sj
	})

	for _, dest := range candidates {
		si := cpuWeight*dest.CPUPct + ramWeight*dest.RAMPct
		if e.destinationCanHold(dest, vm, upper) {
			logger.Info(
				"Destination: %s score=%.2f cpu=%s ram=%s",
				fmtNode(dest.Name, "light"), si, fmtCPU(dest.CPUPct), fmtRAM(dest.RAMPct),
			)
			return dest
		}
	}

	logger.Warning("All light node candidates rejected by capacity check")
	return nil
}

// Migration record keeping

func (e *DRSEngine) recordMigration(vm *VMInfo, src, dst string) {
	now := time.Now()
	e.lastMigration = now
	e.lastMigratedVMID = vm.VMID
	e.clusterMigrations = append(e.clusterMigrations, now)
	e.migrationHistory[vm.VMID] = append(e.migrationHistory[vm.VMID], now)

	ev := migrationEvent{
		At:     now,
		VMID:   vm.VMID,
		VMName: vm.Name,
		Kind:   vm.Kind,
		Src:    src,
		Dst:    dst,
	}

	if err := e.store.InsertMigration(ev); err != nil {
		logger.Error("Failed to persist migration to DB: %v", err)
	}

	metrics.RecordMigration(ev)

	logger.Info(
		"Recorded migration for %s (%d) - total cluster migrations in window: %d",
		fmtVMName(vm.Name), vm.VMID, len(e.clusterMigrations),
	)
}

// Main cycle

func (e *DRSEngine) RunCycle() {
	cycleStart := time.Now()
	logger.Info(strings.Repeat("=", 55))
	logger.Info("DRS cycle starting")

	// Storm guard
	if e.checkMigrationStorm() {
		stormSecs := 0.0
		if time.Now().Before(e.stormPauseUntil) {
			stormSecs = time.Until(e.stormPauseUntil).Seconds()
		}
		metrics.SetSafetyState(true, stormSecs, false, 0, len(e.clusterMigrations))
		metrics.FinishCycle(cycleStart, false)
		return
	}

	// Phase 1 - Load Evaluation
	nodes := e.buildNodeLoads()
	if len(nodes) == 0 {
		logger.Warning("No PVE nodes found, skipping")
		metrics.FinishCycle(cycleStart, true)
		return
	}

	threshold, lower, upper := e.bandCalc.Compute(nodes)
	metrics.SetBands(nodes, threshold, lower, upper)

	// Phase 2 - Profitability
	if !e.isProfitable(nodes) {
		logger.Info("No heavy+light pair found - cluster is balanced, nothing to do")
		metrics.SetSafetyState(false, 0, false, 0, len(e.clusterMigrations))
		metrics.FinishCycle(cycleStart, false)
		return
	}

	if !e.cooldownOK() {
		cooldownSecs := migrationCooldown.Seconds() - time.Since(e.lastMigration).Seconds()
		metrics.SetSafetyState(false, 0, true, cooldownSecs, len(e.clusterMigrations))
		metrics.FinishCycle(cycleStart, false)
		return
	}

	metrics.SetSafetyState(false, 0, false, 0, len(e.clusterMigrations))

	for _, heavy := range nodes {
		if heavy.Band != "heavy" {
			continue
		}

		logger.Warning(
			"Heavy node: %s CPU=%s RAM=%s",
			fmtNode(heavy.Name, "heavy"), fmtCPU(heavy.CPUPct), fmtRAM(heavy.RAMPct),
		)

		if heavy.VMCount <= 1 {
			logger.Info(
				"Skipping %s - only %d guest, nothing to migrate away",
				fmtNode(heavy.Name, "heavy"), heavy.VMCount,
			)
			continue
		}

		// Phase 3 - Work Transfer Vector
		wtv := e.workTransferVector(heavy, threshold)
		logger.Info(
			"Work Transfer Vector for %s: %s",
			fmtNode(heavy.Name, "heavy"), fmtWTV(wtv),
		)

		// Phase 4 - VM + destination selection
		vm, dest := e.selectVMWithDestination(heavy.Name, wtv, nodes, upper)
		if vm == nil {
			logger.Warning("No viable guest+destination pair found for %s, skipping", heavy.Name)
			continue
		}

		logger.Info(
			"Plan: move [%s] %s (%d) from %s to %s",
			strings.ToUpper(vm.Kind),
			fmtVMName(vm.Name), vm.VMID,
			fmtNode(heavy.Name, "heavy"),
			fmtNode(dest.Name, "light"),
		)

		// Phase 5 - Migration
		var taskData json.RawMessage
		var migrateErr error
		if vm.Kind == "qemu" {
			taskData, migrateErr = e.pve.MigrateVM(vm.VMID, heavy.Name, dest.Name)
		} else {
			taskData, migrateErr = e.pve.MigrateCT(vm.VMID, heavy.Name, dest.Name)
		}

		if migrateErr != nil {
			logger.Error("Migration failed: %v", migrateErr)
			continue
		}

		logger.Info("Migration task submitted: %s", string(taskData))
		e.recordMigration(vm, heavy.Name, dest.Name)

		// One migration per cycle - re-evaluate after cooldown
		break
	}
	metrics.FinishCycle(cycleStart, false)
}

func (e *DRSEngine) Run() {
	defer e.store.Close()
	logger.Info(
		"Check interval: %.0fs  Cooldown: %.0fs",
		checkInterval.Seconds(), migrationCooldown.Seconds(),
	)

	for {
		func() {
			defer func() {
				if r := recover(); r != nil {
					logger.Error("DRS cycle panicked: %v", r)
				}
			}()
			e.RunCycle()
		}()
		time.Sleep(checkInterval)
	}
}

// ------------------------------------------------------------
// Entrypoint
// ------------------------------------------------------------

func main() {
	loadConfig()
	startMetricsServer(metricsAddr)
	newDRSEngine().Run()
}
