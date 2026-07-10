from __future__ import annotations
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project(
  id TEXT PRIMARY KEY, name TEXT, config TEXT, feishu_base TEXT, feishu_table TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS task(
  id TEXT PRIMARY KEY, project_id TEXT, name TEXT, spec TEXT, state TEXT,
  workspace_path TEXT, runtime TEXT, target_host TEXT, resource_req TEXT, evaluator TEXT,
  owner TEXT, metadata TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS worker(
  id TEXT PRIMARY KEY, task_id TEXT, status TEXT, session_handle TEXT,
  session_uuid TEXT, pane_id TEXT, pid INTEGER, resource_lock_id TEXT, started_at TEXT,
  ended_at TEXT, extra TEXT);
CREATE TABLE IF NOT EXISTS resource_lock(
  id TEXT PRIMARY KEY, resource_id TEXT, worker_id TEXT, slot INTEGER, acquired_at TEXT);
CREATE TABLE IF NOT EXISTS event(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, type TEXT, payload TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS host(
  id TEXT PRIMARY KEY, ssh_alias TEXT, remote_root TEXT, gpu TEXT, notes TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS template(
  id TEXT PRIMARY KEY, name TEXT, spec TEXT, runtime TEXT, effort TEXT, evaluator TEXT,
  builtin INTEGER DEFAULT 0, created_at TEXT);
"""

def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def init(path: str) -> None:
    conn = connect(path)
    conn.executescript(_SCHEMA)
    # Migration: add feishu columns to existing project table.
    try:
        conn.execute("ALTER TABLE project ADD COLUMN feishu_base TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE project ADD COLUMN feishu_table TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE task ADD COLUMN owner TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()
