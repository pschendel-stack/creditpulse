import unittest
from unittest.mock import patch

from api.bdc_universe import active_bdc_universe
from api.bdc_value_map import (
    apply_market_data_to_snapshot,
    apply_peer_implied_valuation,
    classify_valuation,
    local_snapshot_store_enabled,
    snapshot_store_configured,
)
from api.valuation_config import VALUATION_MODEL


def row(ticker, peer, score, pnav, market_cap=1_000_000_000, coverage=90):
    return {
        "ticker": ticker,
        "peerGroup": peer,
        "currentCreditPulseScore": score,
        "creditPulseDataCoveragePct": coverage,
        "actualPriceToNav": pnav,
        "equityMarketCapitalization": market_cap,
        "niiReturnOnNavPct": 11.0,
        "navPerShareChange1yPct": 0.0,
        "navPerShareChange3yPct": 0.0,
        "debtToEquity": 1.0,
        "managementStructure": "Externally managed",
        "hasSnapshot": True,
    }


class BdcUniverseTests(unittest.TestCase):
    def test_active_universe_excludes_inactive_mrcc(self):
        tickers = {r["ticker"] for r in active_bdc_universe()}
        self.assertIn("ARCC", tickers)
        self.assertNotIn("MRCC", tickers)
        self.assertGreaterEqual(len(tickers), 40)


class ValuationModelTests(unittest.TestCase):
    def test_classification_thresholds(self):
        base = {"creditPulseDataCoveragePct": 90}
        self.assertEqual(classify_valuation({**base, "valuationGap": 0.101})[0], "Potentially undervalued")
        self.assertEqual(classify_valuation({**base, "valuationGap": 0.075})[0], "Moderately undervalued")
        self.assertEqual(classify_valuation({**base, "valuationGap": 0.0})[0], "About right")
        self.assertEqual(classify_valuation({**base, "valuationGap": -0.075})[0], "Moderately expensive")
        self.assertEqual(classify_valuation({**base, "valuationGap": -0.101})[0], "Potentially expensive")

    def test_insufficient_coverage_blocks_classification(self):
        label, cls = classify_valuation({"creditPulseDataCoveragePct": 50, "valuationGap": 0.25})
        self.assertEqual(label, "Insufficient data")
        self.assertEqual(cls, "insufficient")

    def test_peer_implied_pnav_uses_price_outside_credit_score(self):
        rows = [
            row("LOW", "Diversified middle-market", 50, 0.70, 500_000_000),
            row("MID", "Diversified middle-market", 70, 0.90, 750_000_000),
            row("HIGH", "Diversified middle-market", 90, 1.10, 1_000_000_000),
        ]
        valued = {r["ticker"]: r for r in apply_peer_implied_valuation(rows)}
        self.assertEqual(valued["HIGH"]["currentCreditPulseScore"], 90)
        self.assertGreater(valued["HIGH"]["peerImpliedPriceToNav"], valued["LOW"]["peerImpliedPriceToNav"])
        self.assertIn("valuationGap", valued["LOW"])

    def test_peer_implied_model_excludes_unavailable_cash_nii_coverage(self):
        self.assertNotIn("dividendDurability", VALUATION_MODEL["weights"])
        self.assertNotIn("dividendDurability", VALUATION_MODEL["componentInputs"])
        self.assertAlmostEqual(sum(VALUATION_MODEL["weights"].values()), 1.0)

    def test_market_only_refresh_preserves_credit_metrics(self):
        snapshot = {
            "ticker": "ARCC",
            "currentCreditPulseScore": 73,
            "creditMetricsAsOf": "2026-05-01T00:00:00+00:00",
            "latestNavPerShare": 20.0,
            "sharesOutstanding": 100.0,
            "stockPrice": 18.0,
            "actualPriceToNav": 0.9,
        }
        updated = apply_market_data_to_snapshot(snapshot, {
            "price": 19.0,
            "timestamp": "2026-07-13T21:00:00+00:00",
        })
        self.assertEqual(updated["currentCreditPulseScore"], 73)
        self.assertEqual(updated["creditMetricsAsOf"], "2026-05-01T00:00:00+00:00")
        self.assertEqual(updated["stockPrice"], 19.0)
        self.assertEqual(updated["equityMarketCapitalization"], 1900.0)
        self.assertEqual(updated["actualPriceToNav"], 0.95)
        self.assertAlmostEqual(updated["discountPremiumToNav"], -0.05)


class SnapshotStoreConfigTests(unittest.TestCase):
    def test_vercel_requires_real_snapshot_store_by_default(self):
        with patch.dict("os.environ", {"VERCEL": "1"}, clear=False):
            self.assertFalse(local_snapshot_store_enabled())
            self.assertFalse(snapshot_store_configured("", ""))

    def test_local_development_store_is_enabled_outside_vercel(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertTrue(local_snapshot_store_enabled())
            self.assertTrue(snapshot_store_configured("", ""))

    def test_supabase_counts_as_configured_in_production(self):
        with patch.dict("os.environ", {"VERCEL": "1"}, clear=False):
            self.assertTrue(snapshot_store_configured("https://example.supabase.co", "key"))


if __name__ == "__main__":
    unittest.main()
