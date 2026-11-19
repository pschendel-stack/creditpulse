import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "refresh_bdc_value_map", ROOT / "scripts" / "refresh_bdc_value_map.py"
)
refresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh)


def _subs(rows):
    forms, accs, periods = zip(*rows) if rows else ((), (), ())
    return {"filings": {"recent": {
        "form": list(forms), "accessionNumber": list(accs), "reportDate": list(periods),
    }}}


class RefreshDecisionTests(unittest.TestCase):
    def test_latest_filing_period_ignores_other_forms(self):
        subs = _subs([  # EDGAR lists newest first
            ("8-K", "a4", "2026-11-05"),
            ("10-Q", "a3", "2026-09-30"),
            ("10-Q", "a2", "2026-06-30"),
        ])
        self.assertEqual(refresh.latest_filing_period(subs), "2026-09-30")
        self.assertIsNone(refresh.latest_filing_period(_subs([("8-K", "a1", "2026-11-05")])))
        self.assertIsNone(refresh.latest_filing_period(None))

    def test_new_quarter_triggers_full_refresh(self):
        prior = {"latestFilingPeriod": "2026-06-30"}
        self.assertTrue(refresh.needs_full_refresh(prior, "2026-09-30", force=False))

    def test_same_quarter_is_market_only(self):
        prior = {"latestFilingPeriod": "2026-09-30"}
        self.assertFalse(refresh.needs_full_refresh(prior, "2026-09-30", force=False))
        self.assertTrue(refresh.needs_full_refresh(prior, "2026-09-30", force=True))

    def test_missing_snapshot_or_period_triggers_full_refresh(self):
        self.assertTrue(refresh.needs_full_refresh(None, "2026-09-30", force=False))
        self.assertTrue(refresh.needs_full_refresh({}, "2026-09-30", force=False))

    def test_sec_lookup_failure_keeps_existing_snapshot(self):
        prior = {"latestFilingPeriod": "2026-09-30"}
        self.assertFalse(refresh.needs_full_refresh(prior, None, force=False))


class SupabaseUrlTests(unittest.TestCase):
    def test_normalizes_common_variants(self):
        expected = "https://abc.supabase.co"
        for raw in [
            "https://abc.supabase.co",
            " https://abc.supabase.co/ ",
            '"https://abc.supabase.co"',
            "https://abc.supabase.co/rest/v1",
            "abc.supabase.co",
            "abc",
        ]:
            self.assertEqual(refresh.normalize_supabase_url(raw), expected, raw)
        self.assertEqual(refresh.normalize_supabase_url(""), "")

    def test_shape_description_hides_value(self):
        shape = refresh.describe_secret_shape("https://secretref.supabase.co")
        self.assertNotIn("secretref", shape)
        self.assertIn("scheme 'https'", shape)


if __name__ == "__main__":
    unittest.main()
