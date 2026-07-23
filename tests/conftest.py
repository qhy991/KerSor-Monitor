from __future__ import annotations
import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("FLOTILLA_DB", str(db))
    monkeypatch.setenv("FLOTILLA_WORKSPACES", str(tmp_path / "ws"))
    # reimport settings so env takes effect
    import importlib
    import flotilla.config

    importlib.reload(flotilla.config)
    return str(db)


@pytest.fixture(autouse=True)
def _isolate_actuator_registry():
    # actuator._HANDLES is a process-global bridge from scheduler.tick -> actuator.
    # Clear it around every test so scheduler registrations from one test cannot
    # leak into another (makes test order irrelevant).
    from flotilla import actuator

    actuator._HANDLES.clear()
    yield
    actuator._HANDLES.clear()
