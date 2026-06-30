#!/usr/bin/env python3
"""Project-local skill hub utilities for autokaggle workspaces."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml


DEFAULT_SKILLS = ("KernelWiki", "ncu-report-skill")
DEFAULT_TARGETS = ("claude", "codex")
DEFAULT_MANIFEST = Path("skill_hub") / "manifest.yaml"


class SkillHubError(ValueError):
    """Raised when the skill hub manifest or filesystem layout is invalid."""


def default_manifest_data(
    *,
    kernelwiki_source: str = "/workspace/repo/kernel-design-agents/skills/KernelWiki",
    ncu_source: str = "/workspace/repo/kernel-design-agents/skills/ncu-report-skill",
    version: str = "current",
) -> dict[str, Any]:
    return {
        "skills": [
            {
                "name": "KernelWiki",
                "source": kernelwiki_source,
                "version": version,
                "targets": list(DEFAULT_TARGETS),
            },
            {
                "name": "ncu-report-skill",
                "source": ncu_source,
                "version": version,
                "targets": list(DEFAULT_TARGETS),
            },
        ]
    }


def render_manifest(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def load_manifest(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        raise SkillHubError(f"manifest missing: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    return normalize_manifest(data)


def normalize_manifest(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_skills = data.get("skills")
    if not isinstance(raw_skills, list) or not raw_skills:
        raise SkillHubError("manifest must contain a non-empty 'skills' list")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_skills):
        if not isinstance(item, dict):
            raise SkillHubError(f"skills[{index}] must be a mapping")
        name = str(item.get("name") or "").strip()
        source = str(item.get("source") or "").strip()
        version = str(item.get("version") or "").strip()
        if not name:
            raise SkillHubError(f"skills[{index}] is missing name")
        if name in seen:
            raise SkillHubError(f"duplicate skill in manifest: {name}")
        if not source:
            raise SkillHubError(f"{name} is missing source")
        if not version:
            raise SkillHubError(f"{name} is missing version")
        targets = item.get("targets") or list(DEFAULT_TARGETS)
        if not isinstance(targets, list) or not targets:
            raise SkillHubError(f"{name} targets must be a non-empty list")
        clean_targets = []
        for target in targets:
            target = str(target).strip()
            if target not in DEFAULT_TARGETS:
                raise SkillHubError(f"{name} has unsupported target: {target}")
            clean_targets.append(target)
        normalized.append(
            {
                "name": name,
                "source": source,
                "version": version,
                "targets": clean_targets,
            }
        )
        seen.add(name)
    return normalized


def manifest_path_for_root(root: str | Path, manifest_path: str | Path | None = None) -> Path:
    root = Path(root)
    if manifest_path is None:
        return root / DEFAULT_MANIFEST
    manifest = Path(manifest_path)
    return manifest if manifest.is_absolute() else root / manifest


def hub_dir(root: str | Path) -> Path:
    return Path(root) / "skill_hub"


def active_skill_path(root: str | Path, skill_name: str) -> Path:
    return hub_dir(root) / "active" / skill_name


def version_skill_path(root: str | Path, skill_name: str, version: str) -> Path:
    return hub_dir(root) / "versions" / skill_name / version


def ensure_default_manifest(root: str | Path, *, overwrite: bool = False) -> Path:
    manifest = manifest_path_for_root(root)
    if manifest.exists() and not overwrite:
        return manifest
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(render_manifest(default_manifest_data()))
    return manifest


def _replace_symlink(link: Path, target: Path, *, dry_run: bool = False) -> dict[str, Any]:
    rel_target = os.path.relpath(target, start=link.parent)
    if link.is_symlink() and os.readlink(link) == rel_target:
        return {"path": str(link), "target": rel_target, "status": "ok"}
    if dry_run:
        return {"path": str(link), "target": rel_target, "status": "would_link"}
    if link.exists() or link.is_symlink():
        if link.is_dir() and not link.is_symlink():
            shutil.rmtree(link)
        else:
            link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(rel_target)
    return {"path": str(link), "target": rel_target, "status": "linked"}


def sync_skill_hub(
    root: str | Path,
    *,
    manifest_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(root)
    manifest = manifest_path_for_root(root, manifest_path)
    skills = load_manifest(manifest)
    report: dict[str, Any] = {"root": str(root), "manifest": str(manifest), "skills": []}

    for skill in skills:
        name = skill["name"]
        source = Path(skill["source"])
        version = skill["version"]
        if not source.is_dir():
            raise SkillHubError(f"{name} source missing or not a directory: {source}")
        if not (source / "SKILL.md").is_file():
            raise SkillHubError(f"{name} source missing SKILL.md: {source}")

        target = version_skill_path(root, name, version)
        active = active_skill_path(root, name)
        entry = {
            "name": name,
            "source": str(source),
            "version": version,
            "version_path": str(target),
            "active_path": str(active),
        }
        if dry_run:
            entry["status"] = "would_sync"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            if tmp.exists():
                shutil.rmtree(tmp)
            shutil.copytree(source, tmp, symlinks=True)
            if target.exists():
                shutil.rmtree(target)
            tmp.replace(target)
            _replace_symlink(active, target)
            entry["status"] = "synced"
        report["skills"].append(entry)
    return report


def workspace_skill_link(workspace: str | Path, target: str, skill_name: str) -> Path:
    target_dir = ".claude" if target == "claude" else ".codex"
    return Path(workspace) / target_dir / "skills" / skill_name


def link_workspace_skills(
    root: str | Path,
    workspace: str | Path,
    *,
    manifest_path: str | Path | None = None,
    replace: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(root)
    workspace = Path(workspace)
    manifest = manifest_path_for_root(root, manifest_path)
    skills = load_manifest(manifest)
    report: dict[str, Any] = {"workspace": str(workspace), "links": []}

    for skill in skills:
        active = active_skill_path(root, skill["name"])
        if not active.exists():
            raise SkillHubError(f"active skill missing: {active}")
        if not (active / "SKILL.md").is_file():
            raise SkillHubError(f"active skill missing SKILL.md: {active}")
        for target in skill["targets"]:
            link = workspace_skill_link(workspace, target, skill["name"])
            if link.exists() and not link.is_symlink() and not replace:
                report["links"].append(
                    {
                        "path": str(link),
                        "target": str(active),
                        "status": "existing_non_symlink",
                    }
                )
                continue
            report["links"].append(_replace_symlink(link, active, dry_run=dry_run))
    return report


def check_skill_hub(
    root: str | Path,
    *,
    manifest_path: str | Path | None = None,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(root)
    manifest = manifest_path_for_root(root, manifest_path)
    report: dict[str, Any] = {"ok": True, "errors": [], "skills": [], "workspace_links": []}
    try:
        skills = load_manifest(manifest)
    except SkillHubError as exc:
        return {"ok": False, "errors": [str(exc)], "skills": [], "workspace_links": []}

    for skill in skills:
        name = skill["name"]
        active = active_skill_path(root, name)
        entry = {"name": name, "active": str(active), "targets": skill["targets"]}
        if not active.is_symlink():
            report["ok"] = False
            report["errors"].append(f"{name} active link missing or not symlink: {active}")
            entry["status"] = "bad_active_link"
        elif not active.exists():
            report["ok"] = False
            report["errors"].append(f"{name} active target missing: {active}")
            entry["status"] = "missing_active_target"
        elif not (active / "SKILL.md").is_file():
            report["ok"] = False
            report["errors"].append(f"{name} active target missing SKILL.md: {active}")
            entry["status"] = "missing_skill_md"
        else:
            entry["status"] = "ok"
        report["skills"].append(entry)

        if workspace is not None:
            for target in skill["targets"]:
                link = workspace_skill_link(workspace, target, name)
                link_entry = {"path": str(link), "target": str(active)}
                if not link.is_symlink():
                    report["ok"] = False
                    report["errors"].append(f"{name} {target} workspace link missing or not symlink: {link}")
                    link_entry["status"] = "bad_workspace_link"
                elif not link.exists():
                    report["ok"] = False
                    report["errors"].append(f"{name} {target} workspace link target missing: {link}")
                    link_entry["status"] = "missing_workspace_target"
                else:
                    link_entry["status"] = "ok"
                report["workspace_links"].append(link_entry)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage autokaggle project-local skill_hub.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    default_manifest = subparsers.add_parser("default-manifest", help="Print the default skill hub manifest.")
    default_manifest.set_defaults(func=run_default_manifest)

    sync = subparsers.add_parser("sync", help="Copy manifest sources into skill_hub and update active links.")
    sync.add_argument("--root", required=True)
    sync.add_argument("--manifest")
    sync.add_argument("--dry-run", action="store_true")
    sync.set_defaults(func=run_sync)

    link = subparsers.add_parser("link-workspace", help="Link one workspace to skill_hub active skills.")
    link.add_argument("--root", required=True)
    link.add_argument("--workspace", required=True)
    link.add_argument("--manifest")
    link.add_argument("--replace", action="store_true")
    link.add_argument("--dry-run", action="store_true")
    link.set_defaults(func=run_link)

    check = subparsers.add_parser("check", help="Validate skill_hub and optional workspace links.")
    check.add_argument("--root", required=True)
    check.add_argument("--manifest")
    check.add_argument("--workspace")
    check.set_defaults(func=run_check)
    return parser


def run_default_manifest(args: argparse.Namespace) -> int:
    print(render_manifest(default_manifest_data()), end="")
    return 0


def run_sync(args: argparse.Namespace) -> int:
    print(json.dumps(sync_skill_hub(args.root, manifest_path=args.manifest, dry_run=args.dry_run), indent=2))
    return 0


def run_link(args: argparse.Namespace) -> int:
    print(
        json.dumps(
            link_workspace_skills(
                args.root,
                args.workspace,
                manifest_path=args.manifest,
                replace=args.replace,
                dry_run=args.dry_run,
            ),
            indent=2,
        )
    )
    return 0


def run_check(args: argparse.Namespace) -> int:
    report = check_skill_hub(args.root, manifest_path=args.manifest, workspace=args.workspace)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except SkillHubError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
