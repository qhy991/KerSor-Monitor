"""Offline tests for the B200 leaderboard snapshot parser.

Exercises the pure parsing functions (no network): reference-row filtering,
--exclude-user handling, numeric parsing, and median computation. These guard
the community sol_score axis that feeds the harvester.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_b200_leaderboard_snapshot as snap  # noqa: E402


class B200SnapshotParseTests(unittest.TestCase):
    def test_reference_usernames_excluded_from_community(self) -> None:
        refs = snap.collect_reference_names({})
        self.assertEqual(refs, snap.REFERENCE_USERNAMES)
        for name in ("SOL Bound", "Scoring Baseline", "Reference Implementation"):
            self.assertFalse(
                snap.is_community_entry({"username": name}, refs, set()),
                f"{name!r} should be filtered as a reference row",
            )

    def test_exclude_user_filters_named_accounts(self) -> None:
        refs = snap.collect_reference_names({})
        excluded = {"kersor", "kda"}
        self.assertFalse(snap.is_community_entry({"username": "KerSor"}, refs, excluded))
        self.assertFalse(snap.is_community_entry({"username": "KDA"}, refs, excluded))
        # A real community submitter is kept
        self.assertTrue(snap.is_community_entry({"username": "doubleAI"}, refs, excluded))

    def test_is_reference_flag_and_empty_username_excluded(self) -> None:
        refs = snap.collect_reference_names({})
        self.assertFalse(snap.is_community_entry({"username": "someone", "is_reference": True}, refs, set()))
        self.assertFalse(snap.is_community_entry({"username": ""}, refs, set()))
        self.assertFalse(snap.is_community_entry({}, refs, set()))

    def test_collect_reference_names_picks_up_dynamic_entries(self) -> None:
        kernel_data = {
            "reference_entries": [{"username": "Custom Baseline"}],
            "sol_entry": {"username": "SOL Bound"},
        }
        refs = snap.collect_reference_names(kernel_data)
        self.assertIn("custom baseline", refs)   # from reference_entries
        self.assertIn("sol bound", refs)         # from sol_entry
        self.assertIn("scoring baseline", refs)  # built-in

    def test_as_float_handles_none_invalid_and_numeric_strings(self) -> None:
        self.assertIsNone(snap.as_float({}, "sol_score"))
        self.assertIsNone(snap.as_float({"sol_score": "n/a"}, "sol_score"))
        self.assertEqual(snap.as_float({"sol_score": "0.83"}, "sol_score"), 0.83)
        self.assertEqual(snap.as_float({"sol_score": 0.91}, "sol_score"), 0.91)

    def test_median_numeric_skips_none_and_invalid(self) -> None:
        entries = [
            {"sol_score": 0.5},
            {"sol_score": None},
            {"sol_score": "0.7"},
            {"sol_score": "n/a"},
            {"sol_score": 0.9},
        ]
        # valid values [0.5, 0.7, 0.9] -> median 0.7
        self.assertEqual(snap.median_numeric(entries, "sol_score"), 0.7)
        self.assertIsNone(snap.median_numeric([{"sol_score": "x"}, {"sol_score": None}], "sol_score"))


if __name__ == "__main__":
    unittest.main()
