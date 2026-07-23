from __future__ import annotations

import math
from typing import Any, NotRequired, TypedDict, cast

from .base import StateSink, ProjectSnapshot
from . import web as web
from .web import WebSink
from .feishu import FeishuSink


class TaskView(TypedDict):
    """Stable task summary shared by list responses and sink events.

    The potentially large spec is included only by the single-task detail route.
    """

    id: str
    project_id: str
    name: str
    spec: NotRequired[str]
    state: str
    workspace_path: str | None
    runtime: str
    target_host: str | None
    resource_req: dict[str, Any]
    evaluator: str | None
    owner: str | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    status_state: str | None
    speedup: float | None
    rounds: int
    candidates: int
    best_candidate: str | None
    timestamp: str
    pane_tail: str
    session_uuid: str | None
    last_activity: str
    last_tool: str | None
    tokens: int
    exited: bool
    source: str | None
    deleted: bool


_STATUS_DEFAULTS = {
    "status_state": None,
    "speedup": None,
    "rounds": 0,
    "candidates": 0,
    "best_candidate": None,
    "timestamp": "",
    "pane_tail": "",
    "session_uuid": None,
    "last_activity": "",
    "last_tool": None,
    "tokens": 0,
    "exited": False,
    "source": None,
}

_PRIVATE_METADATA_KEYS = {
    "command",
    "boot_command",
}
_PRIVATE_METADATA_FRAGMENTS = ("secret", "token", "password", "api_key", "private_key")


def normalize_status_record(status: dict | None) -> dict[str, Any]:
    """Normalize worker-authored telemetry before storage or JSON publication."""
    source = status or {}
    result = dict(source)

    speedup = source.get("speedup")
    normalized_speedup = None
    if isinstance(speedup, (int, float)) and not isinstance(speedup, bool):
        try:
            candidate = float(speedup)
            if math.isfinite(candidate):
                normalized_speedup = candidate
        except (OverflowError, ValueError):
            pass
    result["speedup"] = normalized_speedup
    for key in ("rounds", "candidates", "tokens"):
        if key not in source:
            continue
        value = source.get(key, 0)
        maximum = 10_000_000 if key != "tokens" else 10**15
        result[key] = (
            value
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum
            else 0
        )

    string_limits = {
        "status_state": 64,
        "best_candidate": 1024,
        "timestamp": 128,
        "pane_tail": 16_384,
        "session_uuid": 256,
        "last_activity": 2048,
        "last_tool": 256,
        "source": 128,
    }
    nullable = {"status_state", "best_candidate", "session_uuid", "last_tool", "source"}
    for key, limit in string_limits.items():
        value = source.get(key)
        if isinstance(value, str):
            result[key] = value[:limit]
        else:
            result[key] = None if key in nullable else ""
    result["exited"] = bool(source.get("exited", False))
    return result


def _public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Keep operational metadata useful without echoing executable or secret data."""
    return {
        key: value
        for key, value in metadata.items()
        if key.lower() not in _PRIVATE_METADATA_KEYS
        and not any(fragment in key.lower() for fragment in _PRIVATE_METADATA_FRAGMENTS)
    }


def build_task_view(
    store,
    task,
    status: dict | None = None,
    *,
    deleted: bool = False,
    include_spec: bool = True,
) -> TaskView:
    """Build one canonical task payload.

    The database state is authoritative. Worker status augments the task with
    telemetry, but can never overwrite identity or lifecycle fields.
    """
    if status is None:
        events = store.status_events(task.id, 1)
        status = events[-1].payload if events else {}

    normalized = normalize_status_record(status)
    telemetry = {key: normalized.get(key, default) for key, default in _STATUS_DEFAULTS.items()}
    persisted = task.model_dump()
    persisted["metadata"] = _public_metadata(persisted.get("metadata") or {})
    if not include_spec:
        persisted.pop("spec", None)
    view = cast(
        TaskView,
        {
            **persisted,
            **telemetry,
            "deleted": deleted,
        },
    )
    return view


def publish_view(
    view: TaskView,
    *,
    feishu_base: str | None = None,
    feishu_table: str | None = None,
) -> None:
    """Publish one task update on its project's shared stream."""
    _fan_out(
        ProjectSnapshot(tasks=[dict(view)], project_id=view["project_id"]),
        feishu_base=feishu_base,
        feishu_table=feishu_table,
    )


def publish_task(store, task, status: dict | None = None, *, deleted: bool = False) -> TaskView:
    view = build_task_view(
        store,
        task,
        status,
        deleted=deleted,
        include_spec=False,
    )
    project = store.get_project(task.project_id)
    publish_view(
        view,
        feishu_base=project.feishu_base if project else None,
        feishu_table=project.feishu_table if project else None,
    )
    return view


REGISTRY: dict[str, StateSink] = {"web": WebSink(), "feishu": FeishuSink()}


def _fan_out(
    snapshot: ProjectSnapshot,
    *,
    feishu_base: str | None = None,
    feishu_table: str | None = None,
) -> None:
    for sink in REGISTRY.values():
        target = snapshot
        if sink.name != "web":
            tasks = [task for task in snapshot.tasks if not task.get("deleted")]
            if not tasks:
                continue
            if feishu_base or feishu_table:
                tasks = [
                    {
                        **task,
                        "feishu_base": feishu_base,
                        "feishu_table": feishu_table,
                    }
                    for task in tasks
                ]
            target = ProjectSnapshot(tasks=tasks, project_id=snapshot.project_id)
        try:
            sink.render(target)
        except Exception as exc:
            # One sink failing must not break others, but silent loss makes
            # operational diagnosis needlessly hard.
            print(f"[sinks] {sink.name} render failed: {exc}", flush=True)


def fan_out(snapshot: ProjectSnapshot) -> None:
    _fan_out(snapshot)
