"""Base de datos SQLite: esquema y datos iniciales."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .logic import DEFAULT_SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS bus_types (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    capacity  INTEGER NOT NULL CHECK (capacity > 0)
);
CREATE TABLE IF NOT EXISTS routes (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS buses (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    type_id       INTEGER NOT NULL REFERENCES bus_types(id),
    route_id      INTEGER REFERENCES routes(id),
    last_seen     REAL,
    state         TEXT NOT NULL DEFAULT 'offline',
    rssi          INTEGER,
    fw            TEXT,
    up_ms         INTEGER,
    count         INTEGER NOT NULL DEFAULT 0,
    in_total      INTEGER NOT NULL DEFAULT 0,
    out_total     INTEGER NOT NULL DEFAULT 0,
    last_seq      INTEGER,
    snapshot_seq  INTEGER,
    incomplete    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    bus_id       TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    dir          TEXT NOT NULL CHECK (dir IN ('in', 'out')),
    ts           INTEGER,
    up_ms        INTEGER,
    received_at  REAL NOT NULL,
    PRIMARY KEY (bus_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_received ON events(received_at);
CREATE TABLE IF NOT EXISTS count_samples (
    bus_id  TEXT NOT NULL,
    ts      REAL NOT NULL,
    count   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_samples_bus_ts ON count_samples(bus_id, ts);
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

# Exactamente dos tipos (editables, no se agregan ni borran) y tres recorridos iniciales
DEFAULT_TYPES = [(1, "Grande", 50), (2, "Chico", 25)]
DEFAULT_ROUTES = [(1, "UAI-Grecia"), (2, "UAI-Dominicos"), (3, "UAI-Fernando Castillo Velasco")]


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.executemany("INSERT OR IGNORE INTO bus_types (id, name, capacity) VALUES (?, ?, ?)", DEFAULT_TYPES)
    if conn.execute("SELECT COUNT(*) FROM routes").fetchone()[0] == 0:
        conn.executemany("INSERT INTO routes (id, name) VALUES (?, ?)", DEFAULT_ROUTES)
    conn.executemany("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                     [(k, str(v)) for k, v in DEFAULT_SETTINGS.items()])
    return conn
