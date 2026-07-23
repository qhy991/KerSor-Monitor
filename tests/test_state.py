from __future__ import annotations
import pytest
from flotilla import state


def test_valid_transitions():
    assert state.transition("PLANNED", "QUEUED") == "QUEUED"
    assert state.transition("QUEUED", "DISPATCHING") == "DISPATCHING"
    assert state.transition("DISPATCHING", "RUNNING") == "RUNNING"
    assert state.transition("QUEUED", "RUNNING") == "RUNNING"
    assert state.transition("RUNNING", "DONE") == "DONE"
    assert state.transition("RUNNING", "PAUSED") == "PAUSED"
    assert state.transition("PAUSED", "RUNNING") == "RUNNING"
    assert state.transition("RUNNING", "STUCK") == "STUCK"
    assert state.transition("STUCK", "RUNNING") == "RUNNING"  # nudge
    assert state.transition("STUCK", "DONE") == "DONE"
    assert state.transition("RUNNING", "CANCELLED") == "CANCELLED"
    assert state.transition("DISPATCHING", "LOST") == "LOST"
    assert state.transition("LOST", "QUEUED") == "QUEUED"


def test_invalid_transition_raises():
    with pytest.raises(state.IllegalTransition):
        state.transition("DONE", "RUNNING")
    with pytest.raises(state.IllegalTransition):
        state.transition("PLANNED", "RUNNING")


def test_unknown_state_raises():
    with pytest.raises(state.IllegalTransition):
        state.transition("BOGUS", "QUEUED")
