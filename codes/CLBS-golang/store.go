package main

import (
	"database/sql"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// ------------------------------------------------------------
// SQLite store - persists migration history across restarts.
// Only one table; everything else lives in memory.
// ------------------------------------------------------------

const schema = `
CREATE TABLE IF NOT EXISTS migrations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         INTEGER NOT NULL,          -- unix timestamp
    vmid       INTEGER NOT NULL,
    vm_name    TEXT    NOT NULL,
    kind       TEXT    NOT NULL,          -- "qemu" | "lxc"
    src        TEXT    NOT NULL,
    dst        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_migrations_at ON migrations(at);
`

const retentionHours = 72

type Store struct {
	db *sql.DB
}

func openStore(path string) (*Store, error) {
	db, err := sql.Open("sqlite3", path+"?_journal_mode=WAL&_foreign_keys=on")
	if err != nil {
		return nil, err
	}
	if _, err := db.Exec(schema); err != nil {
		return nil, err
	}
	s := &Store{db: db}
	if err := s.prune(); err != nil {
		logger.Warning("Store: initial prune failed: %v", err)
	}
	return s, nil
}

// InsertMigration writes one event and prunes old rows in the same transaction.
func (s *Store) InsertMigration(ev migrationEvent) error {
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	_, err = tx.Exec(
		`INSERT INTO migrations (at, vmid, vm_name, kind, src, dst) VALUES (?, ?, ?, ?, ?, ?)`,
		ev.At.Unix(), ev.VMID, ev.VMName, ev.Kind, ev.Src, ev.Dst,
	)
	if err != nil {
		return err
	}

	cutoff := time.Now().Add(-retentionHours * time.Hour).Unix()
	if _, err = tx.Exec(`DELETE FROM migrations WHERE at < ?`, cutoff); err != nil {
		return err
	}

	return tx.Commit()
}

// LoadRecent returns all migrations within the retention window, oldest first.
func (s *Store) LoadRecent() ([]migrationEvent, error) {
	cutoff := time.Now().Add(-retentionHours * time.Hour).Unix()
	rows, err := s.db.Query(
		`SELECT at, vmid, vm_name, kind, src, dst FROM migrations WHERE at >= ? ORDER BY at ASC`,
		cutoff,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var events []migrationEvent
	for rows.Next() {
		var ev migrationEvent
		var ts int64
		if err := rows.Scan(&ts, &ev.VMID, &ev.VMName, &ev.Kind, &ev.Src, &ev.Dst); err != nil {
			return nil, err
		}
		ev.At = time.Unix(ts, 0)
		events = append(events, ev)
	}
	return events, rows.Err()
}

// LastMigratedVMID returns the VMID of the most recent migration, or 0 if none.
func (s *Store) LastMigratedVMID() (int, error) {
	var vmid int
	err := s.db.QueryRow(
		`SELECT vmid FROM migrations ORDER BY at DESC LIMIT 1`,
	).Scan(&vmid)
	if err == sql.ErrNoRows {
		return 0, nil
	}
	return vmid, err
}

// LastMigrationTime returns the timestamp of the most recent migration, or zero time if none.
func (s *Store) LastMigrationTime() (time.Time, error) {
	var ts int64
	err := s.db.QueryRow(
		`SELECT at FROM migrations ORDER BY at DESC LIMIT 1`,
	).Scan(&ts)
	if err == sql.ErrNoRows {
		return time.Time{}, nil
	}
	if err != nil {
		return time.Time{}, err
	}
	return time.Unix(ts, 0), nil
}

func (s *Store) prune() error {
	cutoff := time.Now().Add(-retentionHours * time.Hour).Unix()
	_, err := s.db.Exec(`DELETE FROM migrations WHERE at < ?`, cutoff)
	return err
}

func (s *Store) Close() error {
	return s.db.Close()
}
