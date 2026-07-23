from __future__ import annotations
import os
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project(
  id TEXT PRIMARY KEY, name TEXT, config TEXT, feishu_base TEXT, feishu_table TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS task(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  name TEXT, spec TEXT,
  state TEXT NOT NULL CHECK(state IN (
    'PLANNED','QUEUED','DISPATCHING','RUNNING','PAUSED',
    'DONE','FAILED','STUCK','CANCELLED','LOST')),
  workspace_path TEXT, runtime TEXT, target_host TEXT, resource_req TEXT, evaluator TEXT,
  owner TEXT, metadata TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS worker(
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  status TEXT, session_handle TEXT,
  session_uuid TEXT, pane_id TEXT, pid INTEGER, resource_lock_id TEXT, started_at TEXT,
  ended_at TEXT, extra TEXT);
CREATE TABLE IF NOT EXISTS resource_lock(
  id TEXT PRIMARY KEY, resource_id TEXT,
  worker_id TEXT REFERENCES worker(id) ON DELETE CASCADE,
  slot INTEGER, acquired_at TEXT);
CREATE TABLE IF NOT EXISTS event(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  type TEXT, payload TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS host(
  id TEXT PRIMARY KEY, ssh_alias TEXT, remote_root TEXT, gpu TEXT, notes TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS template(
  id TEXT PRIMARY KEY, name TEXT, spec TEXT, runtime TEXT, effort TEXT, evaluator TEXT,
  builtin INTEGER DEFAULT 0, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_task_queue_order ON task(state, updated_at, id);
CREATE INDEX IF NOT EXISTS idx_task_project_id ON task(project_id, id);
CREATE INDEX IF NOT EXISTS idx_worker_active_task ON worker(ended_at, task_id, started_at);
CREATE INDEX IF NOT EXISTS idx_event_task_type_id ON event(task_id, type, id);
CREATE INDEX IF NOT EXISTS idx_event_type_task_id ON event(type, task_id, id);
"""


def connect(path: str) -> sqlite3.Connection:
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    existed = database_path.exists()
    conn = sqlite3.connect(path, timeout=5.0)
    if not existed and path != ":memory:":
        os.chmod(database_path, 0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    """Add one legacy column without hiding unrelated migration failures."""
    name = definition.split()[0]
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def init(path: str) -> None:
    conn = connect(path)
    try:
        # Journal mode persists in the database; setting it once avoids a
        # journal-mode negotiation on every short-lived request connection.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        # Lightweight migrations for databases created by older MVP versions.
        _ensure_column(conn, "project", "feishu_base TEXT")
        _ensure_column(conn, "project", "feishu_table TEXT")
        _ensure_column(conn, "task", "owner TEXT")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
