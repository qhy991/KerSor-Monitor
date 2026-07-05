from __future__ import annotations

class IllegalTransition(Exception):
    pass

STATES = {"PLANNED", "QUEUED", "RUNNING", "PAUSED", "DONE", "FAILED", "STUCK"}

_ALLOWED = {
    ("PLANNED", "QUEUED"),
    ("QUEUED", "RUNNING"),
    ("RUNNING", "DONE"),
    ("RUNNING", "FAILED"),
    ("RUNNING", "STUCK"),
    ("RUNNING", "PAUSED"),
    ("PAUSED", "RUNNING"),
    ("PAUSED", "DONE"),
    ("STUCK", "RUNNING"),   # nudge
    ("STUCK", "FAILED"),
}

def transition(current: str, target: str) -> str:
    if current not in STATES or target not in STATES:
        raise IllegalTransition(f"unknown state: {current!r} -> {target!r}")
    if current == target:
        return current
    if (current, target) not in _ALLOWED:
        raise IllegalTransition(f"illegal transition: {current} -> {target}")
    return target
