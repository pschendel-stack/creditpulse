import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("analyze", ROOT / "api" / "analyze.py")
analyze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyze)


class PeriodLabelTests(unittest.TestCase):
    def test_no_collision_labels_unchanged(self):
        # Normal quarterly cadence: no two periods share a calendar quarter.
        periods = ["2025-04-30", "2025-07-31", "2025-10-31", "2026-01-31"]
        self.assertEqual(
            analyze.unique_period_labels(periods),
            ["Q2'25", "Q3'25", "Q4'25", "Q1'26"],
        )

    def test_fiscal_quarter_shift_disambiguates_collision(self):
        # FCRIX/FCREX-like scenario: a fund on an Oct/Jan/Apr/Jul cadence
        # shifts to Dec/Mar/Jun/Sep, so both Oct 31 and Dec 31 fall in the
        # same "Q4'25" calendar bucket. Both must stay distinguishable so
        # neither filing's data silently overwrites the other's.
        periods = ["2025-04-30", "2025-07-31", "2025-10-31", "2025-12-31",
                   "2026-03-31", "2026-06-30"]
        labels = analyze.unique_period_labels(periods)
        self.assertEqual(len(labels), len(set(labels)), "labels must be unique")
        self.assertEqual(
            labels,
            ["Q2'25", "Q3'25", "Q4'25 (Oct)", "Q4'25 (Dec)", "Q1'26", "Q2'26"],
        )

    def test_only_colliding_periods_get_disambiguated(self):
        # A collision in one quarter shouldn't add suffixes to an unrelated,
        # non-colliding quarter earlier in the same list.
        periods = ["2025-01-31", "2025-10-31", "2025-12-31"]
        labels = analyze.unique_period_labels(periods)
        self.assertEqual(labels, ["Q1'25", "Q4'25 (Oct)", "Q4'25 (Dec)"])


if __name__ == "__main__":
    unittest.main()
