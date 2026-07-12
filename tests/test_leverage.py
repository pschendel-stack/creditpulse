import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("analyze", ROOT / "api" / "analyze.py")
analyze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyze)


def bdc_row(quarter, debt, equity):
    return {"quarter": quarter, "debt": debt, "equity": equity}


class CoverageMathTests(unittest.TestCase):
    def test_bdc_coverage_and_cushion_fsk_like(self):
        # FSK 12/31/25: debt $7.62B, net assets $5.849B
        lev = analyze.compute_leverage("bdc", [bdc_row("Q4'25", 7_620e6, 5_849e6)])
        latest = lev["latest"]
        self.assertEqual(latest["deRatio"], 1.30)
        self.assertAlmostEqual(latest["coveragePct"], 176.8, delta=0.1)
        self.assertAlmostEqual(latest["cushionPct"], 34.9, delta=0.1)
        # 176.8% is above the 175% amber threshold — no coverage alert
        self.assertFalse(any(a["code"] == "coverage" for a in lev["alerts"]))
        self.assertEqual(lev["status"], "green")

    def test_bdc_amber_threshold(self):
        lev = analyze.compute_leverage("bdc", [bdc_row("Q4'25", 7_000e6, 5_000e6)])
        self.assertAlmostEqual(lev["latest"]["coveragePct"], 171.4, delta=0.1)
        alerts = [a for a in lev["alerts"] if a["code"] == "coverage"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["level"], "amber")

    def test_bdc_red_threshold(self):
        lev = analyze.compute_leverage("bdc", [bdc_row("Q4'25", 6_600e6, 3_900e6)])
        self.assertLess(lev["latest"]["coveragePct"], 160)
        alerts = [a for a in lev["alerts"] if a["code"] == "coverage"]
        self.assertEqual(alerts[0]["level"], "red")
        self.assertEqual(lev["status"], "red")

    def test_interval_fund_uses_300_pct_requirement(self):
        # CCLFX-like: borrowings $9.8B, net assets $31.5B
        lev = analyze.compute_leverage("interval_fund", [
            {"quarter": "Q3'25", "debt": 9_800e6, "equity": 31_500e6, "assets": 41_300e6},
        ])
        latest = lev["latest"]
        self.assertAlmostEqual(latest["coveragePct"], 421.4, delta=0.1)
        # cushion: (E - 2D)/E = (31.5 - 19.6)/31.5
        self.assertAlmostEqual(latest["cushionPct"], 37.8, delta=0.1)
        self.assertEqual(lev["status"], "green")

    def test_interval_fund_amber(self):
        lev = analyze.compute_leverage("interval_fund", [
            {"quarter": "Q1'26", "debt": 6_500e6, "equity": 15_000e6},
        ])
        self.assertAlmostEqual(lev["latest"]["coveragePct"], 330.8, delta=0.1)
        alerts = [a for a in lev["alerts"] if a["code"] == "coverage"]
        self.assertEqual(alerts[0]["level"], "amber")

    def test_headroom_debt(self):
        # BDC at E=6000, D=4000: capacity to E/k = 12000 → headroom 8000
        lev = analyze.compute_leverage("bdc", [bdc_row("Q1'26", 4_000.0, 6_000.0)])
        self.assertEqual(lev["latest"]["headroomDebt"], 8_000.0)

    def test_no_data_is_unavailable(self):
        lev = analyze.compute_leverage("bdc", [bdc_row("Q1'26", None, 5_000e6)])
        self.assertIsNone(lev["latest"])
        self.assertEqual(lev["status"], "unavailable")
        self.assertFalse(lev["items"][0]["dataAvailable"])


class AlertTests(unittest.TestCase):
    def test_leverage_creep_flagged(self):
        lev = analyze.compute_leverage("bdc", [
            bdc_row("Q3'25", 7_369e6, 6_159e6),   # 1.20x
            bdc_row("Q4'25", 7_620e6, 5_849e6),   # 1.30x, NAV down
        ])
        self.assertTrue(any(a["code"] == "leverage_creep" for a in lev["alerts"]))

    def test_no_creep_when_equity_grows(self):
        lev = analyze.compute_leverage("bdc", [
            bdc_row("Q3'25", 7_000e6, 6_000e6),
            bdc_row("Q4'25", 7_500e6, 6_400e6),
        ])
        self.assertFalse(any(a["code"] == "leverage_creep" for a in lev["alerts"]))

    def test_borrowing_outpacing_asset_growth(self):
        # The CCLFX pattern: borrowings +46%, assets +18.5%
        lev = analyze.compute_leverage("interval_fund", [
            {"quarter": "Q1'25", "debt": 6_700e6, "equity": 27_000e6, "assets": 34_900e6},
            {"quarter": "Q2'25", "debt": 8_200e6, "equity": 29_000e6, "assets": 38_000e6},
            {"quarter": "Q3'25", "debt": 9_800e6, "equity": 31_500e6, "assets": 41_300e6},
        ])
        self.assertTrue(any(a["code"] == "borrowing_outpacing" for a in lev["alerts"]))

    def test_stress_plus_coverage_compound_alert(self):
        lev = analyze.compute_leverage(
            "bdc", [bdc_row("Q4'25", 7_000e6, 5_000e6)], stress_pct=6.2)
        self.assertTrue(any(a["code"] == "stress_plus_coverage" and a["level"] == "red"
                            for a in lev["alerts"]))

    def test_proxy_debt_info_alert(self):
        lev = analyze.compute_leverage("interval_fund", [
            {"quarter": "Q1'26", "debt": 5_000e6, "equity": 20_000e6, "debtIsProxy": True},
        ])
        self.assertTrue(any(a["code"] == "debt_proxy" for a in lev["alerts"]))


NPORT_SAMPLE = """<?xml version="1.0"?>
<edgarSubmission>
  <fundInfo>
    <totAssets>41300000000.00</totAssets>
    <totLiabs>10100000000.00</totLiabs>
    <netAssets>31200000000.00</netAssets>
    <amtPayOneYrBanksBorr>1200000000.00</amtPayOneYrBanksBorr>
    <amtPayOneYrOther>0.00</amtPayOneYrOther>
    <amtPayAftOneYrBanksBorr>4300000000.00</amtPayAftOneYrBanksBorr>
    <amtPayAftOneYrOther>4300000000.00</amtPayAftOneYrOther>
  </fundInfo>
</edgarSubmission>"""

NPORT_NO_BORROWINGS = """<?xml version="1.0"?>
<edgarSubmission>
  <fundInfo>
    <totAssets>1000000.00</totAssets>
    <totLiabs>250000.00</totLiabs>
    <netAssets>750000.00</netAssets>
  </fundInfo>
</edgarSubmission>"""


class NportFundLevelTests(unittest.TestCase):
    def test_borrowing_fields_summed(self):
        fl = analyze.parse_nport_fund_level(NPORT_SAMPLE)
        self.assertEqual(fl["borrowings"], 9_800_000_000.0)
        self.assertFalse(fl["borrowingsIsProxy"])
        self.assertEqual(fl["netAssets"], 31_200_000_000.0)
        self.assertEqual(fl["totAssets"], 41_300_000_000.0)

    def test_total_liabilities_proxy_when_untagged(self):
        fl = analyze.parse_nport_fund_level(NPORT_NO_BORROWINGS)
        self.assertEqual(fl["borrowings"], 250_000.0)
        self.assertTrue(fl["borrowingsIsProxy"])

    def test_empty_xml(self):
        fl = analyze.parse_nport_fund_level("")
        self.assertIsNone(fl["borrowings"])


if __name__ == "__main__":
    unittest.main()
