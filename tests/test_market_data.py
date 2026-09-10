import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("market_data", ROOT / "api" / "market_data.py")
market_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(market_data)


class NasdaqTimestampTests(unittest.TestCase):
    def test_parses_nasdaq_format(self):
        iso = market_data._parse_nasdaq_timestamp("Sep 10, 2026 12:22 PM ET")
        self.assertTrue(iso.startswith("2026-09-10T12:22:00"))

    def test_parses_with_seconds(self):
        iso = market_data._parse_nasdaq_timestamp("Sep 10, 2026 12:22:33 PM ET")
        self.assertTrue(iso.startswith("2026-09-10T12:22:33"))

    def test_falls_back_to_now_on_unparseable_input(self):
        # Must never propagate a string Postgres' timestamptz column can't
        # parse (this was silently corrupting every BDC Value Map refresh).
        iso = market_data._parse_nasdaq_timestamp("not a real timestamp")
        self.assertRegex(iso, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_falls_back_to_now_on_missing_input(self):
        iso = market_data._parse_nasdaq_timestamp(None)
        self.assertRegex(iso, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


if __name__ == "__main__":
    unittest.main()
