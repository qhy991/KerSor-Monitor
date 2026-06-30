from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import skill_hub  # noqa: E402


def make_skill_source(root: Path, name: str) -> Path:
    source = root / "sources" / name
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(f"# {name}\n")
    (source / "README.md").write_text("readme\n")
    return source


class SkillHubTests(unittest.TestCase):
    def make_root(self) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        return Path(tempdir.name)

    def write_manifest(self, root: Path, kernelwiki: Path, ncu: Path) -> Path:
        manifest = root / "skill_hub" / "manifest.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            yaml.safe_dump(
                {
                    "skills": [
                        {"name": "KernelWiki", "source": str(kernelwiki), "version": "v1", "targets": ["claude", "codex"]},
                        {
                            "name": "ncu-report-skill",
                            "source": str(ncu),
                            "version": "v1",
                            "targets": ["claude", "codex"],
                        },
                    ]
                },
                sort_keys=False,
            )
        )
        return manifest

    def test_sync_check_and_link_workspace(self) -> None:
        root = self.make_root()
        kernelwiki = make_skill_source(root, "KernelWiki")
        ncu = make_skill_source(root, "ncu-report-skill")
        self.write_manifest(root, kernelwiki, ncu)

        sync_report = skill_hub.sync_skill_hub(root)
        workspace = root / "workspaces" / "l1_011"
        workspace.mkdir(parents=True)
        link_report = skill_hub.link_workspace_skills(root, workspace)
        check_report = skill_hub.check_skill_hub(root, workspace=workspace)

        self.assertEqual([item["status"] for item in sync_report["skills"]], ["synced", "synced"])
        self.assertTrue((root / "skill_hub" / "active" / "KernelWiki").is_symlink())
        self.assertTrue((workspace / ".claude" / "skills" / "KernelWiki").is_symlink())
        self.assertTrue((workspace / ".codex" / "skills" / "ncu-report-skill" / "SKILL.md").exists())
        self.assertEqual(len(link_report["links"]), 4)
        self.assertTrue(check_report["ok"])

    def test_existing_copied_legacy_skill_is_reported_not_replaced(self) -> None:
        root = self.make_root()
        kernelwiki = make_skill_source(root, "KernelWiki")
        ncu = make_skill_source(root, "ncu-report-skill")
        self.write_manifest(root, kernelwiki, ncu)
        skill_hub.sync_skill_hub(root)
        workspace = root / "tasks" / "legacy"
        copied = workspace / ".claude" / "skills" / "KernelWiki"
        copied.mkdir(parents=True)
        (copied / "SKILL.md").write_text("# copied\n")

        report = skill_hub.link_workspace_skills(root, workspace)

        statuses = {(Path(item["path"]).name, item["status"]) for item in report["links"]}
        self.assertIn(("KernelWiki", "existing_non_symlink"), statuses)
        self.assertFalse(copied.is_symlink())
        self.assertEqual((copied / "SKILL.md").read_text(), "# copied\n")

    def test_manifest_validation_rejects_duplicate_skills(self) -> None:
        with self.assertRaisesRegex(skill_hub.SkillHubError, "duplicate"):
            skill_hub.normalize_manifest(
                {
                    "skills": [
                        {"name": "KernelWiki", "source": "/a", "version": "v1"},
                        {"name": "KernelWiki", "source": "/b", "version": "v2"},
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
