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
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          INTEGER NOT NULL,          -- unix timestamp (used for pruning/sorting)
    migrated_at TEXT    NOT NULL DEFAULT '',-- human-readable: "2006-01-02 15:04:05"
    vmid        INTEGER NOT NULL,
    vm_name     TEXT    NOT NULL,
    kind        TEXT    NOT NULL,          -- "qemu" | "lxc"
    src         TEXT    NOT NULL,
    dst         TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_migrations_at ON migrations(at);
`

// backfillMigratedAt fills migrated_at for any rows that were inserted before
// the column existed (they land with the DEFAULT empty string).
const backfillMigratedAt = `
UPDATE migrations
SET    migrated_at = datetime(at, 'unixepoch')
WHERE  migrated_at = '';
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

	// For existing databases that pre-date the migrated_at column, add it
	// gracefully. ALTER TABLE in SQLite ignores the statement if the column
	// already exists only from 3.37+, so we probe first.
	var colExists int
	_ = db.QueryRow(`
		SELECT COUNT(*) FROM pragma_table_info('migrations') WHERE name='migrated_at'
	`).Scan(&colExists)
	if colExists == 0 {
		if _, err := db.Exec(`ALTER TABLE migrations ADD COLUMN migrated_at TEXT NOT NULL DEFAULT ''`); err != nil {
			return nil, err
		}
	}

	// Fill migrated_at for any rows that have an empty string (old rows or
	// rows inserted before this version). SQLite's datetime() converts the
	// unix timestamp to a readable UTC string.
	if _, err := db.Exec(backfillMigratedAt); err != nil {
		logger.Warning("Store: backfill migrated_at failed: %v", err)
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
		`INSERT INTO migrations (at, migrated_at, vmid, vm_name, kind, src, dst) VALUES (?, ?, ?, ?, ?, ?, ?)`,
		ev.At.Unix(),
		ev.At.UTC().Format("2006-01-02 15:04:05"),
		ev.VMID, ev.VMName, ev.Kind, ev.Src, ev.Dst,
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
		`SELECT at, migrated_at, vmid, vm_name, kind, src, dst FROM migrations WHERE at >= ? ORDER BY at ASC`,
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
		var migratedAt string
		if err := rows.Scan(&ts, &migratedAt, &ev.VMID, &ev.VMName, &ev.Kind, &ev.Src, &ev.Dst); err != nil {
			return nil, err
		}
		ev.At = time.Unix(ts, 0)
		// Rows backfilled from old data use the sqlite datetime() string;
		// rows inserted by this version use Go's formatted string. Either way
		// we store it on the event so the metrics handler can expose it as a label.
		if migratedAt == "" {
			migratedAt = ev.At.UTC().Format("2006-01-02 15:04:05")
		}
		ev.MigratedAt = migratedAt
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
