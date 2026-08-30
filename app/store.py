"""SQLite storage. The `reports` table is exactly the contract record.

Two columns exist beyond the contract and are not part of it: `bind_candidates`
(the JSON shortlist an operator needs to override a binding) and `bearing`
(EXIF compass heading, kept so a re-bind can reuse it). Neither is returned by
the report endpoints unless asked for.
"""
import json
import os
import sqlite3
import threading

from app import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    image_path      TEXT,
    lat             REAL NOT NULL,
    lon             REAL NOT NULL,
    gps_accuracy_m  REAL,
    asset_type      TEXT NOT NULL CHECK (asset_type IN ('road', 'building')),
    state           TEXT NOT NULL,
    confidence      REAL NOT NULL,
    edge_id         TEXT,
    n_reports       INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'resolved', 'rejected')),
    detection_mode  TEXT NOT NULL CHECK (detection_mode IN ('api', 'model', 'manual')),
    priority_score  REAL,
    priority_reason TEXT,
    created_at      TEXT NOT NULL,
    bearing         REAL,
    bind_candidates TEXT
);
CREATE INDEX IF NOT EXISTS reports_edge   ON reports (edge_id);
CREATE INDEX IF NOT EXISTS reports_status ON reports (status);
"""

CONTRACT_FIELDS = ("id", "image_path", "lat", "lon", "gps_accuracy_m", "asset_type",
                   "state", "confidence", "edge_id", "n_reports", "status",
                   "detection_mode", "priority_score", "priority_reason", "created_at")


def conn():
    """One connection per thread -- sqlite3 objects cannot cross threads and
    FastAPI runs sync endpoints in a worker pool."""
    db = getattr(_local, "db", None)
    if db is None:
        os.makedirs(os.path.dirname(config.DB), exist_ok=True)
        db = sqlite3.connect(config.DB)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(SCHEMA)
        _local.db = db
    return db


def reset():
    """Drop and recreate. Used by the seed script and the tests."""
    conn().executescript("DROP TABLE IF EXISTS reports;" + SCHEMA)
    conn().commit()


def as_dict(row, full=False):
    """A row as the contract record. `full` adds the non-contract columns."""
    out = {k: row[k] for k in CONTRACT_FIELDS}
    if full:
        out["bearing"] = row["bearing"]
        out["bind_candidates"] = json.loads(row["bind_candidates"] or "[]")
    return out


def insert(**fields):
    fields = dict(fields)
    fields["bind_candidates"] = json.dumps(fields.get("bind_candidates") or [])
    cols = ", ".join(fields)
    cur = conn().execute(f"INSERT INTO reports ({cols}) VALUES "
                         f"({', '.join('?' * len(fields))})", tuple(fields.values()))
    conn().commit()
    return cur.lastrowid


def update(report_id, **fields):
    if not fields:
        return
    if "bind_candidates" in fields:
        fields["bind_candidates"] = json.dumps(fields["bind_candidates"])
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn().execute(f"UPDATE reports SET {sets} WHERE id = ?",
                   tuple(fields.values()) + (report_id,))
    conn().commit()


def get(report_id, full=False):
    row = conn().execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    return as_dict(row, full) if row else None


def all_reports(status=None, full=False):
    """Every report, highest priority first. Unscored reports sort last."""
    sql = "SELECT * FROM reports"
    args = ()
    if status:
        sql += " WHERE status = ?"
        args = (status,)
    sql += " ORDER BY priority_score IS NULL, priority_score DESC, created_at DESC"
    return [as_dict(r, full) for r in conn().execute(sql, args)]


def rows_on_edge(edge_id):
    return [as_dict(r, True) for r in
            conn().execute("SELECT * FROM reports WHERE edge_id = ?", (edge_id,))]
