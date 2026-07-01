from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


class LocalMonitorCliTests(unittest.TestCase):
    def test_parser_builds_with_worker_commands(self) -> None:
        scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
        sys.path.insert(0, str(scripts_dir))
        script = scripts_dir / "local-monitor.py"
        spec = importlib.util.spec_from_file_location("local_monitor_cli", script)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        parser = module.build_parser()
        help_text = parser.format_help()

        self.assertIn("legacy-snapshot", help_text)
        self.assertIn("observe-worker", help_text)
        self.assertIn("verdict-prompt", help_text)
        self.assertIn("actuate-worker", help_text)
        self.assertIn("attach-existing-worker", help_text)
        self.assertIn("generic-snapshot", help_text)
        self.assertIn("generic-observe", help_text)
        self.assertIn("generic-judge", help_text)
        self.assertIn("generic-actuate", help_text)
        self.assertIn("generic-remote-deploy", help_text)
        self.assertIn("generic-remote-once", help_text)
        self.assertIn("generic-remote-start", help_text)
        self.assertIn("generic-remote-stop", help_text)
        self.assertIn("generic-remote-status", help_text)


if __name__ == "__main__":
    unittest.main()
