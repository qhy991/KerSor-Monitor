#!/usr/bin/env python3
"""Freeze the official B200 FlashInfer-Bench leaderboard snapshot.

The KerSor paper needs community-best and community-median baselines that are
reproducible. This script downloads the official collection metadata, collection
leaderboard, and each per-kernel leaderboard, then derives CSV rows for the
external community baselines.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = "https://research.nvidia.com/benchmarks/sol-execbench/api"
REFERENCE_USERNAMES = {
    "sol bound",
    "scoring baseline",
    "reference implementation",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Snapshot official SoL-ExecBench FlashInfer-Bench B200 leaderboards."
    )
    parser.add_argument("--collection-id", type=int, default=4)
    parser.add_argument("--gpu", default="B200")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("snapshots") / "b200-leaderboard",
        help="Directory where the timestamped snapshot folder is written.",
    )
    parser.add_argument(
        "--exclude-user",
        action="append",
        default=[],
        help="Exact username to exclude from community rows. Repeat as needed.",
    )
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--sleep", type=float, default=0.15)
    return parser.parse_args()


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "kda-monitor-paper-snapshot/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == 2:
                break
            time.sleep(1.0 + attempt)
    raise RuntimeError(f"failed to fetch {url}: {last_error}")


def payload_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"expected payload['data'] to be an object, got {type(data).__name__}")
    return data


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value.strip("_") or "unnamed"


def clean_username(entry: dict[str, Any]) -> str:
    return str(entry.get("username", "")).strip()


def collect_reference_names(kernel_data: dict[str, Any]) -> set[str]:
    names = set(REFERENCE_USERNAMES)
    for entry in kernel_data.get("reference_entries", []) or []:
        username = clean_username(entry).lower()
        if username:
            names.add(username)
    sol_entry = kernel_data.get("sol_entry")
    if isinstance(sol_entry, dict):
        username = clean_username(sol_entry).lower()
        if username:
            names.add(username)
    return names


def is_community_entry(
    entry: dict[str, Any],
    reference_names: set[str],
    excluded_users: set[str],
) -> bool:
    username = clean_username(entry).lower()
    if not username:
        return False
    if entry.get("is_reference"):
        return False
    if username in reference_names:
        return False
    if username in excluded_users:
        return False
    return True


def ranking_sort_key(entry: dict[str, Any]) -> tuple[int, float]:
    rank = entry.get("rank")
    if isinstance(rank, int):
        return (0, float(rank))
    score = entry.get("sol_score")
    try:
        return (1, -float(score))
    except (TypeError, ValueError):
        return (2, 0.0)


def as_float(entry: dict[str, Any], key: str) -> float | None:
    value = entry.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def median_numeric(entries: list[dict[str, Any]], key: str) -> float | None:
    values = [as_float(entry, key) for entry in entries]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return float(statistics.median(values))


def format_value(value: Any) -> Any:
    if value is None:
        return ""
    return value


def ref_entry_by_name(kernel_data: dict[str, Any], name: str) -> dict[str, Any]:
    wanted = name.lower()
    for entry in kernel_data.get("reference_entries", []) or []:
        if clean_username(entry).lower() == wanted:
            return entry
    return {}


def entry_fields(prefix: str, entry: dict[str, Any]) -> dict[str, Any]:
    return {
        f"{prefix}_user": clean_username(entry),
        f"{prefix}_rank": format_value(entry.get("rank")),
        f"{prefix}_sol_score": format_value(entry.get("sol_score")),
        f"{prefix}_latency_ms": format_value(entry.get("latency_ms")),
        f"{prefix}_fast_1_count": format_value(entry.get("fast_1_count")),
        f"{prefix}_fast_1_total": format_value(entry.get("fast_1_total")),
        f"{prefix}_avg_speedup": format_value(entry.get("avg_speedup")),
        f"{prefix}_submitted_at": format_value(entry.get("submitted_at")),
    }


def derive_kernel_row(
    kernel_data: dict[str, Any],
    excluded_users: set[str],
    snapshot_utc: str,
    collection_id: int,
    gpu: str,
) -> dict[str, Any]:
    reference_names = collect_reference_names(kernel_data)
    rankings = kernel_data.get("rankings", []) or []
    community = [
        entry
        for entry in rankings
        if isinstance(entry, dict) and is_community_entry(entry, reference_names, excluded_users)
    ]
    community_sorted = sorted(community, key=ranking_sort_key)

    best = community_sorted[0] if community_sorted else {}
    median_entry = community_sorted[len(community_sorted) // 2] if community_sorted else {}
    top3 = community_sorted[:3]
    top3_cutoff = top3[-1].get("sol_score") if top3 else None

    sol_bound = ref_entry_by_name(kernel_data, "SOL Bound")
    scoring_baseline = ref_entry_by_name(kernel_data, "Scoring Baseline")
    reference_impl = ref_entry_by_name(kernel_data, "Reference Implementation")

    row: dict[str, Any] = {
        "snapshot_utc": snapshot_utc,
        "collection_id": collection_id,
        "gpu_type": gpu,
        "kernel_id": kernel_data.get("kernel_id"),
        "kernel_name": kernel_data.get("kernel_name"),
        "community_count": len(community_sorted),
        "top3_cutoff_sol_score": format_value(top3_cutoff),
        "community_median_sol_score_numeric": format_value(
            median_numeric(community_sorted, "sol_score")
        ),
        "community_median_latency_ms_numeric": format_value(
            median_numeric(community_sorted, "latency_ms")
        ),
        "community_median_avg_speedup_numeric": format_value(
            median_numeric(community_sorted, "avg_speedup")
        ),
        "sol_bound_latency_ms": format_value(sol_bound.get("latency_ms")),
        "scoring_baseline_latency_ms": format_value(scoring_baseline.get("latency_ms")),
        "reference_latency_ms": format_value(reference_impl.get("latency_ms")),
        "fast_1_total": format_value(kernel_data.get("sol_entry", {}).get("fast_1_total")),
    }
    row.update(entry_fields("community_best", best))
    row.update(entry_fields("community_median_entry", median_entry))
    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def collection_rank_rows(
    collection_data: dict[str, Any],
    excluded_users: set[str],
    snapshot_utc: str,
    collection_id: int,
    gpu: str,
) -> list[dict[str, Any]]:
    reference_names = set(REFERENCE_USERNAMES)
    for entry in collection_data.get("reference_entries", []) or []:
        username = clean_username(entry).lower()
        if username:
            reference_names.add(username)

    rows = []
    for entry in collection_data.get("rankings", []) or []:
        if not isinstance(entry, dict):
            continue
        rows.append(
            {
                "snapshot_utc": snapshot_utc,
                "collection_id": collection_id,
                "gpu_type": gpu,
                "is_community": is_community_entry(entry, reference_names, excluded_users),
                **entry_fields("entry", entry),
            }
        )
    return rows


def main() -> None:
    args = parse_args()
    snapshot_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out_dir / f"collection_{args.collection_id}_{args.gpu}_{snapshot_utc}"
    raw_dir = out_dir / "raw"
    kernel_dir = raw_dir / "kernels"

    base = args.base_url.rstrip("/")
    excluded_users = {user.strip().lower() for user in args.exclude_user if user.strip()}

    collection_url = f"{base}/collections/{args.collection_id}"
    collection_leaderboard_url = (
        f"{base}/leaderboard/collection/{args.collection_id}/{args.gpu}"
    )

    collection_payload = fetch_json(collection_url, args.timeout)
    collection_leaderboard_payload = fetch_json(collection_leaderboard_url, args.timeout)
    write_json(raw_dir / "collection.json", collection_payload)
    write_json(raw_dir / "collection_leaderboard.json", collection_leaderboard_payload)

    collection_data = payload_data(collection_payload)
    kernels = collection_data.get("kernels") or []
    if not kernels:
        raise RuntimeError(f"collection {args.collection_id} has no kernels")

    kernel_rows: list[dict[str, Any]] = []
    kernel_urls: dict[str, str] = {}
    for kernel in kernels:
        kernel_id = kernel["id"]
        kernel_name = kernel.get("name") or str(kernel_id)
        url = f"{base}/leaderboard/kernel/{kernel_id}/{args.gpu}"
        kernel_urls[str(kernel_id)] = url
        payload = fetch_json(url, args.timeout)
        kernel_data = payload_data(payload)
        write_json(kernel_dir / f"{kernel_id}_{safe_name(kernel_name)}.json", payload)
        kernel_rows.append(
            derive_kernel_row(
                kernel_data,
                excluded_users=excluded_users,
                snapshot_utc=snapshot_utc,
                collection_id=args.collection_id,
                gpu=args.gpu,
            )
        )
        time.sleep(args.sleep)

    collection_rows = collection_rank_rows(
        payload_data(collection_leaderboard_payload),
        excluded_users=excluded_users,
        snapshot_utc=snapshot_utc,
        collection_id=args.collection_id,
        gpu=args.gpu,
    )

    write_csv(out_dir / "community_baselines.csv", kernel_rows)
    write_csv(out_dir / "collection_rankings.csv", collection_rows)

    manifest = {
        "snapshot_utc": snapshot_utc,
        "collection_id": args.collection_id,
        "gpu_type": args.gpu,
        "kernel_count": len(kernel_rows),
        "base_url": base,
        "exclude_users": sorted(excluded_users),
        "reference_usernames": sorted(REFERENCE_USERNAMES),
        "collection_url": collection_url,
        "collection_leaderboard_url": collection_leaderboard_url,
        "kernel_urls": kernel_urls,
        "outputs": {
            "community_baselines_csv": str(out_dir / "community_baselines.csv"),
            "collection_rankings_csv": str(out_dir / "collection_rankings.csv"),
            "raw_dir": str(raw_dir),
        },
        "median_note": (
            "community_median_entry_* is the middle ranked community entry after "
            "reference/excluded users are removed. *_numeric fields are statistical "
            "medians over the filtered community entries."
        ),
    }
    write_json(out_dir / "manifest.json", manifest)

    print(f"Wrote snapshot: {out_dir}")
    print(f"  kernels: {len(kernel_rows)}")
    print(f"  community baselines: {out_dir / 'community_baselines.csv'}")
    print(f"  collection rankings: {out_dir / 'collection_rankings.csv'}")


if __name__ == "__main__":
    main()
