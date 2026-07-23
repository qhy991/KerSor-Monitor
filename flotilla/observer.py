from __future__ import annotations
import json
import threading
import time
from pathlib import Path
from typing import cast

from . import models, runtime
from .runtime.base import WorkerHandle
from .store import Store

_SUCCESS_STATUS_STATES = {"promoted", "complete", "archived"}
_STUCK_STATUS_STATES = {"stuck", "stalled"}
_FAILED_STATUS_STATES = {"abandoned", "failed", "crashed"}
_TERMINAL_STATUS_STATES = _SUCCESS_STATUS_STATES | _STUCK_STATUS_STATES | _FAILED_STATUS_STATES
_SESSION_TAIL_MAX_BYTES = 256 * 1024
_SESSION_TAIL_CHUNK_BYTES = 8192
_STATUS_MAX_BYTES = 1024 * 1024


def observe_and_record(
    store: Store,
    worker_id: str,
    handle: WorkerHandle,
    *,
    publish: bool = True,
) -> dict:
    """Read the worker's status, record an event, fan out to sinks.

    Local worker: read status.json directly (worker-written state) + read the
    claude session jsonl (via mined uuid) for rich activity (last message,
    last tool, tokens). Remote worker: use the runtime's observe (ssh tmux
    capture-pane) — screen text is the tracking source there. Both record the
    claude session uuid when mined."""
    h = handle.handle if isinstance(handle.handle, dict) else {}
    host = h.get("host")
    rt = runtime.get(handle.backend)
    exited = False

    if host:
        # ---- remote: tmux screen via ssh (runtime.observe) ----
        try:
            obs = rt.observe(handle)
            state, exited, pane_tail = obs.state, obs.exited, obs.pane_tail
            speedup, rounds, best = obs.speedup, obs.rounds, obs.best_candidate
        except Exception as exc:
            print(
                f"[observer] remote observation failed for {handle.task_id}: {exc}",
                flush=True,
            )
            state, exited, pane_tail = "running", False, ""
            speedup, rounds, best = None, 0, None
        rec = {
            "status_state": state,
            "speedup": speedup,
            "rounds": rounds,
            "candidates": 0,
            "best_candidate": best,
            "timestamp": "",
            "pane_tail": pane_tail[-300:],
        }
        # best-effort uuid mine (for the record)
        if hasattr(rt, "mine_session_uuid"):
            try:
                w = store.get_worker(worker_id)
                if w and not w.session_uuid:
                    uuid = rt.mine_session_uuid(handle)
                    if uuid:
                        store.set_worker_session_uuid(worker_id, uuid)
                        rec["session_uuid"] = uuid
            except Exception:
                pass
    else:
        # ---- local: status.json (worker-written state) ----
        ws = Path(handle.workspace)
        status = {}
        p = ws / "status.json"
        try:
            if p.stat().st_size <= _STATUS_MAX_BYTES:
                loaded = json.loads(p.read_text())
                if isinstance(loaded, dict):
                    status = loaded
        except (json.JSONDecodeError, OSError):
            status = {}
        candidate_count = (
            sum(1 for _ in (ws / "candidates").glob("*.py")) if (ws / "candidates").exists() else 0
        )
        rec = {
            "status_state": status.get("state", "running"),
            "speedup": status.get("speedup"),
            "rounds": status.get("rounds", 0),
            "candidates": candidate_count,
            "timestamp": status.get("timestamp", ""),
            "pane_tail": "",
        }
        # Every local runtime owns its process-exit semantics. status.json keeps
        # authority when it already declares a terminal state; otherwise adopt
        # the adapter's observed state. Shell handles are Popen objects, so this
        # must not be gated on `h`.
        if hasattr(rt, "observe"):
            try:
                obs = rt.observe(handle)
                exited = obs.exited
                rec["pane_tail"] = obs.pane_tail[-300:]
                status_state = str(status.get("state") or "").lower()
                if status_state not in _TERMINAL_STATUS_STATES:
                    rec["status_state"] = obs.state
                if obs.extra:
                    rec.update(obs.extra)
            except Exception:
                pass
        # rich activity from the claude session jsonl (local file, via uuid)
        if hasattr(rt, "mine_session_uuid"):
            try:
                w = store.get_worker(worker_id)
                uuid = (w.session_uuid if w else None) or rt.mine_session_uuid(handle)
                if uuid:
                    if w and not w.session_uuid:
                        store.set_worker_session_uuid(worker_id, uuid)
                    rec["session_uuid"] = uuid
                    activity = _session_activity(handle.workspace, uuid)
                    if activity:
                        rec.update(activity)
            except Exception:
                pass

    rec["exited"] = exited
    from . import sinks

    rec = sinks.normalize_status_record(rec)
    store.append_event(models.Event(task_id=handle.task_id, type="status", payload=rec))
    if publish:
        task = store.get_task(handle.task_id)
        if task is not None:
            sinks.publish_task(store, task, rec)
    return rec


def _tail_lines(
    path: Path,
    n: int,
    *,
    max_bytes: int = _SESSION_TAIL_MAX_BYTES,
) -> list[str]:
    """Read at most ``max_bytes`` from the end of a text file."""
    if n <= 0 or max_bytes <= 0:
        return []
    with path.open("rb") as stream:
        end = stream.seek(0, 2)
        start = max(0, end - max_bytes)
        pos = end
        chunks: list[bytes] = []
        newlines = 0
        while pos > start and newlines <= n:
            size = min(_SESSION_TAIL_CHUNK_BYTES, pos - start)
            pos -= size
            stream.seek(pos)
            chunk = stream.read(size)
            chunks.append(chunk)
            newlines += chunk.count(b"\n")
    data = b"".join(reversed(chunks))
    return data.decode(errors="replace").splitlines()[-n:]


def _session_activity(cwd: str, uuid: str, n: int = 20) -> dict | None:
    """Tail the claude session jsonl; extract last assistant text, last tool_use
    name, and total token usage. Returns {last_activity, last_tool, tokens} or None."""
    enc = cwd.replace("/", "-").replace("_", "-")
    path = Path.home() / ".claude" / "projects" / enc / f"{uuid}.jsonl"
    if not path.exists():
        return None
    try:
        lines = _tail_lines(path, n)
    except Exception:
        return None
    last_text = ""
    last_tool = None
    tokens = 0
    for line in lines:
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "assistant":
            content = (ev.get("message") or {}).get("content") or []
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and block.get("text"):
                        last_text = block["text"].strip().split("\n")[0][:140]
                    elif block.get("type") == "tool_use":
                        last_tool = block.get("name")
        u = (ev.get("message") or {}).get("usage") or {}
        tokens += int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
    return {"last_activity": last_text, "last_tool": last_tool, "tokens": tokens}


def _map_terminal(status_state: str | None, exited: bool, pane_tail: str = "") -> str | None:
    """Map worker status.json state + exit signal to a task state (or None=still running)."""
    if status_state in _SUCCESS_STATUS_STATES:
        return "DONE"
    if status_state in _STUCK_STATUS_STATES:
        return "STUCK"
    if status_state in _FAILED_STATUS_STATES:
        return "FAILED"
    if exited:
        # Pane shows "Worker exited" but status.json never reached a success/terminal
        # state (checked above). An unexpected exit is a failure, not a completion —
        # a legit finish sets promoted/complete first and is caught above.
        return "FAILED"
    # Detect finished-but-no-terminal-state: the optimize loop completed but didn't
    # write promoted/complete to status.json. Claude shows these end-of-session markers.
    if pane_tail and any(
        s in pane_tail for s in ("clear to save", "100% context used", "Final session result")
    ):
        return "DONE"
    return None


def observe_running(store: Store) -> int:
    """Re-observe all RUNNING workers (from the actuator handle registry), update
    the dashboard via SSE, and transition terminal workers to DONE/STUCK/FAILED.
    Returns the number of workers still running."""
    from . import actuator

    still_running = 0
    for task_id, (worker_id, raw_handle) in actuator.handles_snapshot():
        try:
            handle = cast(WorkerHandle, raw_handle)
            # Delay publication until terminal handling has persisted the
            # authoritative task state.
            rec = observe_and_record(store, worker_id, handle, publish=False)
            terminal = _map_terminal(
                rec.get("status_state"), rec.get("exited", False), rec.get("pane_tail", "")
            )
            if terminal:
                # retire() releases the resource lock, marks the task terminal,
                # closes the worker row, and unregisters the handle.
                committed_state = actuator.retire(store, task_id, worker_id, terminal)
                if committed_state:
                    store.append_event(
                        models.Event(
                            task_id=task_id,
                            type="terminal",
                            payload={"state": committed_state},
                        )
                    )
            else:
                still_running += 1
            task = store.get_task(task_id)
            if task is not None:
                from . import sinks

                sinks.publish_task(store, task, rec)
        except Exception as e:
            print(f"[observer] error on {task_id}: {e}", flush=True)
    return still_running


def loop(store: Store, interval: float | None = None):
    """Background daemon thread: re-observe RUNNING workers every `interval` seconds.
    Defaults to config.SETTINGS.observer_interval (env FLOTILLA_OBSERVER_INTERVAL)."""
    if interval is None:
        from . import config

        interval = config.SETTINGS.observer_interval

    def _run():
        while True:
            try:
                observe_running(store)
            except Exception as exc:
                print(f"[observer] patrol failed: {exc}", flush=True)
            time.sleep(interval)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
