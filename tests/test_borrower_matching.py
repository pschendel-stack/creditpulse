import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("analyze", ROOT / "api" / "analyze.py")
analyze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyze)


def inv(name, fv):
    return {"name": name, "fv": fv, "mark": 90.0}


class IndustrySuffixTests(unittest.TestCase):
    def test_fsk_formats_match_across_filings(self):
        # FSK tagged the same position three ways across 2025-2026 filings.
        forms = ["Peraton Corp, Capital Goods 2", "Peraton Corp | Capital Goods 2", "Peraton Corp"]
        self.assertEqual({analyze.normalize_name(f) for f in forms}, {"PERATON"})

    def test_display_name_drops_industry_tail(self):
        self.assertEqual(
            analyze.strip_industry_suffix("Laboratoires Vivacy SAS | Pharmaceuticals, Biotechnology & Life Sciences 1"),
            "Laboratoires Vivacy SAS")

    def test_instrument_detail_is_kept(self):
        # ARCC separates instrument detail with "|"; equity and loans must stay distinct.
        for name in ("Skywalker TopCo, LLC | Common units",
                     "Affordable Care Inc, Class A Common Stock",
                     "Kilter Finance, ABF Equity"):
            self.assertEqual(analyze.strip_industry_suffix(name), name)


class ScheduleFormatRowsTests(unittest.TestCase):
    def test_keeps_piped_rows_when_they_dominate(self):
        rows = [inv("A Inc | Software & Services 1", 10), inv("B LLC | Capital Goods", 20), inv("A Inc", 10)]
        self.assertEqual([r["name"] for r in analyze._schedule_format_rows(rows)],
                         ["A Inc | Software & Services 1", "B LLC | Capital Goods"])

    def test_no_candidate_without_a_mix(self):
        self.assertEqual(analyze._schedule_format_rows([inv("A | X", 1), inv("B | Y", 1)]), [])
        self.assertEqual(analyze._schedule_format_rows([inv("A", 1), inv("B", 1), inv("C | Y", 1)]), [])


if __name__ == "__main__":
    unittest.main()
