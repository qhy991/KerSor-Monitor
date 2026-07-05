from __future__ import annotations
import os
import tempfile
import pytest

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("FLOTILLA_DB", str(db))
    monkeypatch.setenv("FLOTILLA_WORKSPACES", str(tmp_path / "ws"))
    # reimport settings so env takes effect
    import importlib, flotilla.config
    importlib.reload(flotilla.config)
    return str(db)
