"""SQLite connection management and schema.

Design notes
------------
* The *statement* is the atomic claim in the archive.  Every statement is
  linked to a :class:`source` (historical evidence / oral account /
  contemporary tip), versioned, and carries an editorial *confidence*.
* Relations between entities and exact coordinates are never derived from a
  name match; both require an explicitly approved supporting statement.
* The public website is rendered from the *publication* table only, never
  directly from the working tables, so stale/withdrawn claims cannot leak.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- sources
CREATE TABLE IF NOT EXISTS source (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('historical','oral','contemporary')),
    title       TEXT NOT NULL,
    citation    TEXT NOT NULL DEFAULT '',
    authored_at TEXT,            -- ISO date attached to the source itself
    created_at  TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT ''
);

-- ---------------------------------------------------------------- entities
CREATE TABLE IF NOT EXISTS entity (
    id          TEXT PRIMARY KEY,
    etype       TEXT NOT NULL CHECK (etype IN ('well','caravan','dwelling','craft')),
    name        TEXT NOT NULL,
    era         TEXT NOT NULL DEFAULT '',   -- e.g. 清末 / 民国 / 当代
    summary     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

-- ---------------------------------------------------------------- statements
CREATE TABLE IF NOT EXISTS statement (
    id           TEXT PRIMARY KEY,
    entity_id    TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    source_id    TEXT NOT NULL REFERENCES source(id),
    predicate    TEXT NOT NULL,
    value        TEXT NOT NULL DEFAULT '',
    value_ref    TEXT REFERENCES entity(id),  -- relation target for *_ref predicates
    confidence   TEXT NOT NULL DEFAULT 'proposed'
                 CHECK (confidence IN ('proposed','approved','withdrawn')),
    category     TEXT NOT NULL DEFAULT 'evidence'
                 CHECK (category IN ('evidence','speculation','tip')),
    note         TEXT NOT NULL DEFAULT '',
    supersedes   TEXT REFERENCES statement(id),
    created_at   TEXT NOT NULL,
    created_by   TEXT NOT NULL DEFAULT 'editor'
);

CREATE TABLE IF NOT EXISTS statement_version (
    id           TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL REFERENCES statement(id) ON DELETE CASCADE,
    version      INTEGER NOT NULL,
    source_id    TEXT NOT NULL REFERENCES source(id),
    predicate    TEXT NOT NULL,
    value        TEXT NOT NULL DEFAULT '',
    value_ref    TEXT,
    confidence   TEXT NOT NULL,
    category     TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    changed_at   TEXT NOT NULL,
    changed_by   TEXT NOT NULL DEFAULT 'editor'
);

-- ---------------------------------------------------------------- relations
CREATE TABLE IF NOT EXISTS relation (
    id          TEXT PRIMARY KEY,
    from_entity TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    to_entity   TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    rtype       TEXT NOT NULL,
    statement_id TEXT NOT NULL REFERENCES statement(id),
    approved    INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

-- ---------------------------------------------------------------- conflicts
CREATE TABLE IF NOT EXISTS conflict (
    id          TEXT PRIMARY KEY,
    entity_id   TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    statement_a TEXT NOT NULL REFERENCES statement(id) ON DELETE CASCADE,
    statement_b TEXT NOT NULL REFERENCES statement(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open','resolved_a','resolved_b','reconciled')),
    reason      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);

-- ---------------------------------------------------------------- locations
CREATE TABLE IF NOT EXISTS location (
    id           TEXT PRIMARY KEY,
    entity_id    TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    statement_id TEXT NOT NULL REFERENCES statement(id),
    lat          REAL,
    lng          REAL,
    precision    TEXT NOT NULL CHECK (precision IN ('exact','approximate','unlocated')),
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','superseded')),
    created_at   TEXT NOT NULL
);

-- ---------------------------------------------------------------- photos
CREATE TABLE IF NOT EXISTS photo (
    id           TEXT PRIMARY KEY,
    entity_id    TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    caption      TEXT NOT NULL DEFAULT '',
    license      TEXT NOT NULL,
    license_until TEXT,          -- ISO date; NULL = licence does not expire
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','permission_lapsed','retired')),
    created_at   TEXT NOT NULL
);

-- ------------------------------------------------------- supply facilities
CREATE TABLE IF NOT EXISTS supply_point (
    id          TEXT PRIMARY KEY,
    entity_id   TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open','closed','temporarily_closed')),
    services    TEXT NOT NULL DEFAULT '[]',   -- JSON array of strings
    note        TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_window (
    id          TEXT PRIMARY KEY,
    supply_id   TEXT NOT NULL REFERENCES supply_point(id) ON DELETE CASCADE,
    valid_from  TEXT NOT NULL,
    valid_to    TEXT NOT NULL,
    season_note TEXT NOT NULL DEFAULT ''
);

-- ------------------------------------------------------------- itineraries
CREATE TABLE IF NOT EXISTS itinerary (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    reviewed    INTEGER NOT NULL DEFAULT 0,
    valid_from  TEXT NOT NULL,
    valid_to    TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS segment (
    id          TEXT PRIMARY KEY,
    itinerary_id TEXT NOT NULL REFERENCES itinerary(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    label       TEXT NOT NULL,
    entity_id   TEXT REFERENCES entity(id),     -- stop / waypoint
    kind        TEXT NOT NULL DEFAULT 'waypoint'
                CHECK (kind IN ('start','end','waypoint','water_well')),
    note        TEXT NOT NULL DEFAULT ''
);

-- ------------------------------------------------------------- publishing
CREATE TABLE IF NOT EXISTS publish_task (
    id          TEXT PRIMARY KEY,
    entity_id   TEXT NOT NULL REFERENCES entity(id),
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','done','cancelled')),
    created_at  TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS publication (
    id          TEXT PRIMARY KEY,
    entity_id   TEXT NOT NULL,
    revision    INTEGER NOT NULL,
    doc         TEXT NOT NULL,             -- rendered JSON document
    cards       TEXT NOT NULL DEFAULT '{}', -- JSON: print card per style
    published_at TEXT NOT NULL,
    task_id     TEXT REFERENCES publish_task(id),
    UNIQUE (entity_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_publication_entity ON publication(entity_id, revision DESC);

CREATE TABLE IF NOT EXISTS offline_package (
    id           TEXT PRIMARY KEY,
    built_at     TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    payload      TEXT NOT NULL,            -- JSON bundle
    low_bandwidth INTEGER NOT NULL DEFAULT 0
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def jdumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
