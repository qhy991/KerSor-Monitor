from __future__ import annotations


class IllegalTransition(Exception):
    pass


STATES = {
    "PLANNED",
    "QUEUED",
    "DISPATCHING",
    "RUNNING",
    "PAUSED",
    "DONE",
    "FAILED",
    "STUCK",
    "CANCELLED",
    "LOST",
}

_ALLOWED = {
    ("PLANNED", "QUEUED"),
    ("PLANNED", "CANCELLED"),
    ("QUEUED", "DISPATCHING"),
    # Kept for callers created before DISPATCHING was introduced. The scheduler
    # itself always claims QUEUED -> DISPATCHING atomically before starting work.
    ("QUEUED", "RUNNING"),
    ("QUEUED", "CANCELLED"),
    ("DISPATCHING", "RUNNING"),
    ("DISPATCHING", "QUEUED"),  # dispatch failed before a worker became active
    ("DISPATCHING", "FAILED"),  # invalid/non-retryable dispatch configuration
    ("DISPATCHING", "CANCELLED"),
    ("DISPATCHING", "LOST"),  # external side effect may exist, ownership unknown
    ("RUNNING", "DONE"),
    ("RUNNING", "FAILED"),
    ("RUNNING", "STUCK"),
    ("RUNNING", "PAUSED"),
    ("RUNNING", "CANCELLED"),
    ("RUNNING", "LOST"),
    ("PAUSED", "RUNNING"),
    ("PAUSED", "DONE"),
    ("PAUSED", "FAILED"),
    ("PAUSED", "CANCELLED"),
    ("PAUSED", "LOST"),
    ("STUCK", "RUNNING"),  # nudge
    ("STUCK", "DONE"),  # worker can recover and finish before a manual nudge
    ("STUCK", "FAILED"),
    ("STUCK", "CANCELLED"),
    ("STUCK", "LOST"),
    ("LOST", "QUEUED"),  # explicit operator recovery/retry
    ("LOST", "FAILED"),
    ("LOST", "CANCELLED"),
}


def transition(current: str, target: str) -> str:
    if current not in STATES or target not in STATES:
        raise IllegalTransition(f"unknown state: {current!r} -> {target!r}")
    if current == target:
        return current
    if (current, target) not in _ALLOWED:
        raise IllegalTransition(f"illegal transition: {current} -> {target}")
    return target
