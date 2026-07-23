from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from flotilla import app as appmod, db, models, store
from flotilla.app import create_app
from flotilla.runtime import tmux_claude


def test_health_endpoints_report_database_and_capabilities(tmp_db):
    client = TestClient(create_app())

    assert client.get("/health/live").json() == {"ok": True}
    ready = client.get("/health/ready")

    assert ready.status_code == 200
    payload = ready.json()
    assert payload["ok"] is True
    assert set(payload["capabilities"]) == {"ssh", "tmux", "claude", "curl", "lark-cli"}


def _active_remote_task(tmp_db):
    db.init(tmp_db)
    s = store.Store(tmp_db)
    s.create_project(models.Project(id="p1", name="demo"))
    s.create_host(models.Host(id="h1", ssh_alias="host", remote_root="/work"))
    s.create_task(
        models.Task(
            id="t1",
            project_id="p1",
            name="active",
            spec="x",
            state="RUNNING",
            runtime="claude_tmux",
            target_host="h1",
            workspace_path="/work/ws_t1",
        )
    )
    s.create_worker(models.Worker(id="w1", task_id="t1"))
    return s


def test_reconcile_marks_known_missing_tmux_worker_lost(tmp_db, monkeypatch):
    s = _active_remote_task(tmp_db)
    monkeypatch.setattr(
        tmux_claude,
        "_ssh",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout="",
            stderr="no such window",
        ),
    )

    assert appmod._reconcile_running(s) == 0
    assert s.get_task("t1").state == "LOST"
    assert s.get_worker("w1").ended_at is not None


def test_reconcile_does_not_guess_when_remote_host_is_unreachable(tmp_db, monkeypatch):
    s = _active_remote_task(tmp_db)
    monkeypatch.setattr(
        tmux_claude,
        "_ssh",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=255,
            stdout="",
            stderr="unreachable",
        ),
    )

    assert appmod._reconcile_running(s) == 0
    assert s.get_task("t1").state == "RUNNING"
    assert s.get_worker("w1").ended_at is None
