#!/usr/bin/env python3
"""Remote Generic Monitor runtime.

This script is deployed beside ``generic_control.py`` on the GPU host. It
observes a local tmux-backed Worker, asks the configured verdict runner for a
strict JSON verdict, and optionally nudges the Worker through local tmux.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from generic_control import (
    build_generic_actuation,
    build_generic_verdict_prompt,
    collect_generic_observation_local,
    run_generic_verdict,
    send_generic_actuation,
    utc_now,
    validate_generic_verdict,
    write_json_artifact,
)


def log_line(message: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat()} {message}", flush=True)


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return dict(default or {})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def load_bundle(path: str | Path) -> dict[str, Any]:
    bundle = read_json(Path(path))
    required = {"flows_by_id", "policies_by_id", "workers_by_id", "workers", "remote_monitor"}
    missing = sorted(required.difference(bundle))
    if missing:
        raise ValueError(f"bundle missing required keys: {', '.join(missing)}")
    return bundle


def state_path(config: dict[str, Any]) -> Path:
    monitor = config.get("remote_monitor") or {}
    return Path(monitor.get("state_path") or Path(monitor.get("dir", ".")) / "state.json")


def record_state(config: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    path = state_path(config)
    state = read_json(
        path,
        {
            "schema": "generic-remote-monitor-state/v1",
            "started_at": utc_now(),
            "iterations": 0,
            "last_nudge_at": None,
            "errors": [],
        },
    )
    state.update(updates)
    state["updated_at"] = utc_now()
    write_json(path, state)
    return state


def attach_monitor_state(observation: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    observation = json.loads(json.dumps(observation))
    observation["monitor"] = {
        "last_nudge_at": state.get("last_nudge_at"),
        "iterations": state.get("iterations", 0),
        "state_path": str(state.get("state_path") or ""),
    }
    return observation


def run_once(config: dict[str, Any], worker_id: str, *, send: bool, prompt_only: bool = False) -> int:
    path = state_path(config)
    state = read_json(
        path,
        {
            "schema": "generic-remote-monitor-state/v1",
            "started_at": utc_now(),
            "iterations": 0,
            "last_nudge_at": None,
            "errors": [],
        },
    )
    state["state_path"] = str(path)
    iteration = int(state.get("iterations") or 0) + 1
    log_line(f"{worker_id}: iteration={iteration} observing worker pane")
    observation = collect_generic_observation_local(config, worker_id)
    observation = attach_monitor_state(observation, state)
    observation_path = write_json_artifact(config, worker_id, "observations", observation)
    activity = (observation.get("activity_signals") or {}).get("activity", "unknown")
    pane_id = (observation.get("tmux") or {}).get("pane_id", "")
    log_line(f"{worker_id}: iteration={iteration} observed activity={activity} pane={pane_id}")

    if not observation.get("reachable"):
        record_state(
            config,
            {
                "iterations": iteration,
                "last_observation_at": observation.get("collected_at"),
                "last_observation_path": str(observation_path),
                "last_error": "; ".join(observation.get("errors") or ["observation failed"]),
            },
        )
        print(f"{worker_id}: observation failed: {observation.get('errors')}", file=sys.stderr, flush=True)
        return 1

    if prompt_only:
        prompt = build_generic_verdict_prompt(observation)
        prompt_path = Path(config["output_dir"]) / worker_id / "prompts" / "latest.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt)
        record_state(
            config,
            {
                "iterations": iteration,
                "last_observation_at": observation.get("collected_at"),
                "last_observation_path": str(observation_path),
                "last_prompt_path": str(prompt_path),
            },
        )
        print(prompt, flush=True)
        return 0

    try:
        log_line(f"{worker_id}: iteration={iteration} asking verdict runner")
        verdict = validate_generic_verdict(run_generic_verdict(config, observation))
    except Exception as exc:
        record_state(
            config,
            {
                "iterations": iteration,
                "last_observation_at": observation.get("collected_at"),
                "last_observation_path": str(observation_path),
                "last_error": f"{type(exc).__name__}: {exc}",
            },
        )
        print(f"{worker_id}: verdict failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1

    log_line(
        f"{worker_id}: iteration={iteration} verdict activity={verdict.get('activity')} "
        f"phase={verdict.get('phase')} needs_human={verdict.get('needs_human')}"
    )
    verdict_path = write_json_artifact(config, worker_id, "verdicts", verdict)
    action = build_generic_actuation(config, observation, verdict, send=send, transport="local")
    action_path = write_json_artifact(config, worker_id, "actuations", action)
    rc = send_generic_actuation(action)

    updates = {
        "iterations": iteration,
        "last_observation_at": observation.get("collected_at"),
        "last_observation_path": str(observation_path),
        "last_verdict_path": str(verdict_path),
        "last_action_path": str(action_path),
        "last_activity": verdict.get("activity"),
        "last_phase": verdict.get("phase"),
        "last_required_next_step": verdict.get("required_next_step"),
        "last_action_reason": action.get("reason"),
        "last_error": None if rc == 0 else f"actuation exited {rc}",
    }
    if action.get("will_send") and rc == 0:
        updates["last_nudge_at"] = utc_now()
    record_state(config, updates)
    log_line(f"{worker_id}: iteration={iteration} action={action.get('reason')}")
    return rc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a remote generic Monitor beside a tmux Worker.")
    parser.add_argument("--config", default="bundle.json", help="Remote monitor bundle JSON.")
    parser.add_argument("--worker", help="Worker id. Defaults to remote_monitor.worker_id in the bundle.")
    parser.add_argument("--once", action="store_true", help="Run one monitor iteration and exit.")
    parser.add_argument("--loop", action="store_true", help="Run continuously.")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between loop iterations.")
    parser.add_argument("--send", action="store_true", help="Actually paste eligible nudges into the Worker pane.")
    parser.add_argument("--prompt-only", action="store_true", help="Write/build the verdict prompt without calling the model.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_bundle(args.config)
    worker_id = args.worker or (config.get("remote_monitor") or {}).get("worker_id")
    if not worker_id:
        raise ValueError("worker id required")
    record_state(
        config,
        {
            "worker_id": worker_id,
            "send_enabled": bool(args.send),
            "interval_seconds": int(args.interval),
            "pid": None,
        },
    )
    if args.loop:
        log_line(
            f"{worker_id}: remote monitor loop started interval={args.interval}s "
            f"send={bool(args.send)} config={args.config}"
        )
        while True:
            run_once(config, worker_id, send=args.send, prompt_only=args.prompt_only)
            log_line(f"{worker_id}: sleeping {args.interval}s")
            time.sleep(args.interval)
    return run_once(config, worker_id, send=args.send, prompt_only=args.prompt_only)


if __name__ == "__main__":
    raise SystemExit(main())
