package main

import (
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"
)

// ------------------------------------------------------------
// Metrics store - all values written by the DRS engine,
// read by the HTTP handler on every scrape.
// ------------------------------------------------------------

type Metrics struct {
	mu sync.RWMutex

	// Counters
	CyclesTotal     int64
	MigrationsTotal int64
	CycleErrors     int64

	// Last cycle
	LastCycleDuration time.Duration
	LastCycleAt       time.Time

	// Cluster state (reset each cycle)
	NodeBands     map[string]string  // node -> "heavy"|"moderate"|"light"
	NodeCPU       map[string]float64 // node -> cpu %
	NodeRAM       map[string]float64 // node -> ram %
	BandThreshold float64
	BandLower     float64
	BandUpper     float64

	// Last migration unix timestamp (0 = never)
	LastMigrationAt int64

	// Migration events (loaded from SQLite, last 72h)
	recentMigrations []migrationEvent

	// Safety state
	StormPauseActive   bool
	StormPauseSeconds  float64
	GlobalCooldown     bool
	CooldownSeconds    float64
	MigrationsInWindow int
}

type migrationEvent struct {
	At     time.Time
	VMID   int
	VMName string
	Kind   string // "qemu"|"lxc"
	Src    string
	Dst    string
}

var metrics = &Metrics{
	NodeBands: make(map[string]string),
	NodeCPU:   make(map[string]float64),
	NodeRAM:   make(map[string]float64),
}

// LoadMigrations seeds the in-memory ring from the database on startup.
func (m *Metrics) LoadMigrations(events []migrationEvent) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.recentMigrations = events
	m.MigrationsTotal = int64(len(events))
	if len(events) > 0 {
		m.LastMigrationAt = events[len(events)-1].At.Unix()
	}
}

// Writer methods - called by DRSEngine

func (m *Metrics) SetCycleStart() time.Time {
	return time.Now()
}

func (m *Metrics) FinishCycle(start time.Time, err bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.CyclesTotal++
	m.LastCycleDuration = time.Since(start)
	m.LastCycleAt = time.Now()
	if err {
		m.CycleErrors++
	}
}

func (m *Metrics) SetBands(nodes []*NodeLoad, threshold, lower, upper float64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.NodeBands = make(map[string]string, len(nodes))
	m.NodeCPU = make(map[string]float64, len(nodes))
	m.NodeRAM = make(map[string]float64, len(nodes))
	for _, n := range nodes {
		m.NodeBands[n.Name] = n.Band
		m.NodeCPU[n.Name] = n.CPUPct
		m.NodeRAM[n.Name] = n.RAMPct
	}
	m.BandThreshold = threshold
	m.BandLower = lower
	m.BandUpper = upper
}

func (m *Metrics) SetSafetyState(
	stormActive bool, stormSecs float64,
	cooldown bool, cooldownSecs float64,
	migsInWindow int,
) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.StormPauseActive = stormActive
	m.StormPauseSeconds = stormSecs
	m.GlobalCooldown = cooldown
	m.CooldownSeconds = cooldownSecs
	m.MigrationsInWindow = migsInWindow
}

func (m *Metrics) RecordMigration(ev migrationEvent) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.MigrationsTotal++
	m.LastMigrationAt = ev.At.Unix()
	m.recentMigrations = append(m.recentMigrations, ev)

	// Keep only the last 72 hours (pruning handled by SQLite layer too)
	cutoff := time.Now().Add(-72 * time.Hour)
	i := 0
	for i < len(m.recentMigrations) && m.recentMigrations[i].At.Before(cutoff) {
		i++
	}
	m.recentMigrations = m.recentMigrations[i:]
}

// ------------------------------------------------------------
// Prometheus text exposition
// ------------------------------------------------------------

func boolToFloat(b bool) float64 {
	if b {
		return 1
	}
	return 0
}

func prometheusHandler(w http.ResponseWriter, r *http.Request) {
	metrics.mu.RLock()
	defer metrics.mu.RUnlock()

	m := metrics
	var sb strings.Builder

	// Helper to write a gauge line
	gauge := func(name, help string, val float64, labels ...string) {
		sb.WriteString(fmt.Sprintf("# HELP %s %s\n# TYPE %s gauge\n", name, help, name))
		if len(labels) > 0 {
			sb.WriteString(fmt.Sprintf("%s{%s} %g\n", name, strings.Join(labels, ","), val))
		} else {
			sb.WriteString(fmt.Sprintf("%s %g\n", name, val))
		}
	}

	counter := func(name, help string, val int64, labels ...string) {
		sb.WriteString(fmt.Sprintf("# HELP %s %s\n# TYPE %s counter\n", name, help, name))
		if len(labels) > 0 {
			sb.WriteString(fmt.Sprintf("%s{%s} %d\n", name, strings.Join(labels, ","), val))
		} else {
			sb.WriteString(fmt.Sprintf("%s %d\n", name, val))
		}
	}

	// Cycle metrics
	counter("clbs_cycles_total", "Total number of DRS cycles executed", m.CyclesTotal)
	counter("clbs_cycle_errors_total", "Total number of DRS cycles that panicked or errored", m.CycleErrors)
	gauge("clbs_cycle_duration_seconds", "Duration of the last DRS cycle in seconds",
		m.LastCycleDuration.Seconds())
	gauge("clbs_last_cycle_timestamp", "Unix timestamp of the last completed DRS cycle",
		float64(m.LastCycleAt.Unix()))

	// Migration metrics
	counter("clbs_migrations_total", "Total number of live migrations triggered by CLBS", m.MigrationsTotal)
	gauge("clbs_last_migration_timestamp", "Unix timestamp of the last migration (0 = never since restart)",
		float64(m.LastMigrationAt))
	gauge("clbs_migrations_in_storm_window", "Number of migrations recorded in the current storm detection window",
		float64(m.MigrationsInWindow))

	// CPU Band thresholds
	gauge("clbs_band_threshold_percent", "Current CSLB CPU threshold (mean of all nodes)", m.BandThreshold)
	gauge("clbs_band_lower_percent", "Lower bound of the moderate CPU band", m.BandLower)
	gauge("clbs_band_upper_percent", "Upper bound of the moderate CPU band", m.BandUpper)

	// RAM cap (static from config, exposed so dashboards can draw reference lines)
	gauge("clbs_ram_hard_cap_percent", "Hard RAM cap above which a destination node is rejected", ramHigh)

	// Per-node metrics
	sb.WriteString("# HELP clbs_node_cpu_percent Current CPU usage of each Proxmox node\n")
	sb.WriteString("# TYPE clbs_node_cpu_percent gauge\n")
	for node, cpu := range m.NodeCPU {
		sb.WriteString(fmt.Sprintf(`clbs_node_cpu_percent{node="%s"} %g`+"\n", node, cpu))
	}

	sb.WriteString("# HELP clbs_node_ram_percent Current RAM usage of each Proxmox node\n")
	sb.WriteString("# TYPE clbs_node_ram_percent gauge\n")
	for node, ram := range m.NodeRAM {
		sb.WriteString(fmt.Sprintf(`clbs_node_ram_percent{node="%s"} %g`+"\n", node, ram))
	}

	sb.WriteString("# HELP clbs_node_band Current band classification of each node (0=light, 1=moderate, 2=heavy)\n")
	sb.WriteString("# TYPE clbs_node_band gauge\n")
	for node, band := range m.NodeBands {
		val := map[string]float64{"light": 0, "moderate": 1, "heavy": 2}[band]
		sb.WriteString(fmt.Sprintf(`clbs_node_band{node="%s"} %g`+"\n", node, val))
	}

	// Safety state
	gauge("clbs_storm_pause_active", "1 if the migration storm pause is currently active", boolToFloat(m.StormPauseActive))
	gauge("clbs_storm_pause_remaining_seconds", "Seconds remaining in the storm pause (0 if not active)", m.StormPauseSeconds)
	gauge("clbs_global_cooldown_active", "1 if the global post-migration cooldown is active", boolToFloat(m.GlobalCooldown))
	gauge("clbs_global_cooldown_remaining_seconds", "Seconds remaining in the global cooldown (0 if not active)", m.CooldownSeconds)

	sb.WriteString("# HELP clbs_migration_info Information about recent migrations in the last 72h (value = unix timestamp)\n")
	sb.WriteString("# TYPE clbs_migration_info gauge\n")

	cutoff := time.Now().Add(-72 * time.Hour)
	for len(m.recentMigrations) > 0 && m.recentMigrations[0].At.Before(cutoff) {
		m.recentMigrations = m.recentMigrations[1:]
	}

	for _, ev := range m.recentMigrations {
		sb.WriteString(fmt.Sprintf(
			`clbs_migration_info{vmid="%d",vm="%s",kind="%s",src="%s",dst="%s"} %d`+"\n",
			ev.VMID, ev.VMName, ev.Kind, ev.Src, ev.Dst, ev.At.Unix(),
		))
	}

	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	fmt.Fprint(w, sb.String())
}

func startMetricsServer(addr string) {
	mux := http.NewServeMux()
	mux.HandleFunc("/metrics", prometheusHandler)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "ok")
	})

	logger.Info("Metrics server listening on %s", addr)
	go func() {
		if err := http.ListenAndServe(addr, mux); err != nil {
			logger.Error("Metrics server error: %v", err)
		}
	}()
}
