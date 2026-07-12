import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("analyze", ROOT / "api" / "analyze.py")
analyze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyze)


class RealizedLossCalculationTests(unittest.TestCase):
    def test_net_realized_loss_negative_value_displays_positive(self):
        rows = analyze.calculate_quarterly_realized_losses([
            {
                "quarter": "Q1 2025",
                "periodEnd": "2025-03-31",
                "periodStart": "2025-01-01",
                "originalValue": -1_250_000,
                "metricType": "net_realized_loss",
                "source": "10-Q",
                "dataAvailable": True,
            }
        ])
        self.assertEqual(rows[0]["originalValue"], -1_250_000)
        self.assertEqual(rows[0]["realizedLoss"], 1_250_000)

    def test_net_realized_gain_displays_zero_loss(self):
        rows = analyze.calculate_quarterly_realized_losses([
            {
                "quarter": "Q1 2025",
                "periodEnd": "2025-03-31",
                "periodStart": "2025-01-01",
                "originalValue": 800_000,
                "metricType": "net_realized_loss",
                "source": "10-Q",
                "dataAvailable": True,
            }
        ])
        self.assertEqual(rows[0]["originalValue"], 800_000)
        self.assertEqual(rows[0]["realizedLoss"], 0)

    def test_missing_data_remains_unavailable(self):
        rows = analyze.calculate_quarterly_realized_losses([
            {
                "quarter": "Q2 2025",
                "periodEnd": "2025-06-30",
                "originalValue": None,
                "metricType": "unavailable",
                "source": "NPORT-P",
                "dataAvailable": False,
            }
        ])
        self.assertFalse(rows[0]["dataAvailable"])
        self.assertIsNone(rows[0]["realizedLoss"])

    def test_cumulative_ytd_values_are_quarterized(self):
        rows = analyze.calculate_quarterly_realized_losses([
            {
                "quarter": "Q1 2025",
                "periodEnd": "2025-03-31",
                "periodStart": "2025-01-01",
                "originalValue": -1_000_000,
                "metricType": "net_realized_loss",
                "source": "10-Q",
                "dataAvailable": True,
            },
            {
                "quarter": "Q2 2025",
                "periodEnd": "2025-06-30",
                "periodStart": "2025-01-01",
                "originalValue": -3_500_000,
                "metricType": "net_realized_loss",
                "source": "10-Q",
                "dataAvailable": True,
            },
            {
                "quarter": "Q3 2025",
                "periodEnd": "2025-09-30",
                "periodStart": "2025-01-01",
                "originalValue": -2_000_000,
                "metricType": "net_realized_loss",
                "source": "10-Q",
                "dataAvailable": True,
            },
            {
                "quarter": "Q4 2025",
                "periodEnd": "2025-12-31",
                "periodStart": "2025-01-01",
                "originalValue": -5_000_000,
                "metricType": "net_realized_loss",
                "source": "10-K",
                "dataAvailable": True,
            },
        ])
        self.assertEqual([r["originalValue"] for r in rows], [-1_000_000, -2_500_000, 1_500_000, -3_000_000])
        self.assertEqual([r["realizedLoss"] for r in rows], [1_000_000, 2_500_000, 0, 3_000_000])

    def test_gross_realized_loss_type_is_preserved(self):
        rows = analyze.calculate_quarterly_realized_losses([
            {
                "quarter": "Q1 2025",
                "periodEnd": "2025-03-31",
                "periodStart": "2025-01-01",
                "originalValue": -2_000_000,
                "metricType": "gross_realized_loss",
                "source": "10-Q",
                "dataAvailable": True,
            }
        ])
        self.assertEqual(rows[0]["metricType"], "gross_realized_loss")
        self.assertEqual(rows[0]["realizedLoss"], 2_000_000)

    def test_annual_value_without_prior_ytd_quarters_is_unavailable(self):
        rows = analyze.calculate_quarterly_realized_losses([
            {
                "quarter": "Q4 2024",
                "periodEnd": "2024-12-31",
                "periodStart": "2024-01-01",
                "originalValue": -10_000_000,
                "metricType": "net_realized_loss",
                "source": "10-K",
                "dataAvailable": True,
            }
        ])
        self.assertFalse(rows[0]["dataAvailable"])
        self.assertIsNone(rows[0]["realizedLoss"])


class BdcScheduleParserTests(unittest.TestCase):
    def test_trailing_percentage_column_is_not_treated_as_fair_value(self):
        html = """
        <table>
          <tr>
            <th>Investments</th><th>Footnotes</th><th>Investment</th>
            <th>Reference Rate and Spread</th><th>Interest Rate</th>
            <th>Maturity Date</th><th>Par Amount/ Shares</th>
            <th>Cost</th><th>Fair Value</th><th>Percentage of Net Assets</th>
          </tr>
          <tr>
            <td>Example Borrower, LLC</td><td>(6)</td><td>First Lien Debt</td>
            <td>S +</td><td>8.40 %</td><td>11/29/2030</td>
            <td>1,664</td><td>1,648</td><td>1,648</td><td>0.10</td>
          </tr>
        </table>
        """
        rows = analyze._parse_soi_table(html, {"company": None, "type": "Debt"}, 1.0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["par"], 1664)
        self.assertEqual(rows[0]["fv"], 1648)
        self.assertAlmostEqual(rows[0]["mark"], 99.04)


class PositionScatterTests(unittest.TestCase):
    def test_position_scatter_returns_top_25_by_fair_value(self):
        quarters = ["Q1'26"]
        all_data = {
            "Q1'26": [
                {"name": f"Borrower {i}", "type": "First Lien", "currency": "USD",
                 "par": i * 1000, "fv": i * 1000, "mark": 80 + i}
                for i in range(1, 31)
            ]
        }

        scatter = analyze.compute_position_scatter(all_data, quarters)
        items = scatter["items"]

        self.assertEqual(scatter["version"], 2)
        self.assertEqual(scatter["quarter"], "Q1'26")
        self.assertEqual(scatter["total_fv_m"], 465.0)
        self.assertEqual(len(items), 25)
        self.assertEqual(items[0]["name"], "Borrower 30")
        self.assertEqual(items[0]["fv_m"], 30.0)
        self.assertEqual(items[0]["price"], 110)
        self.assertAlmostEqual(items[0]["portfolio_pct"], 6.45)
        self.assertEqual(items[-1]["name"], "Borrower 6")


class CreditPulseScoreTests(unittest.TestCase):
    def test_score_rewards_strong_portfolio_and_positive_loss_trend(self):
        quarters = ["Q1'26", "Q2'26"]
        all_data = {
            "Q1'26": [
                {"name": "A", "fv": 900_000, "mark": 99},
                {"name": "B", "fv": 100_000, "mark": 88},
            ],
            "Q2'26": [
                {"name": "A", "fv": 900_000, "mark": 100},
                {"name": "B", "fv": 100_000, "mark": 92},
            ],
        }
        rollrate = {
            "avg_rates": {
                "forward_roll": [2.0],
                "cure": [8.0],
                "stay": [88.0],
                "exit": [1.0],
            }
        }
        realized = [
            {"quarter": "Q1 2026", "dataAvailable": True, "originalValue": -1_000,
             "unrealizedAvailable": True, "unrealizedChange": -2_000},
            {"quarter": "Q2 2026", "dataAvailable": True, "originalValue": 500,
             "unrealizedAvailable": True, "unrealizedChange": 3_000},
        ]
        score = analyze.compute_creditpulse_score("TEST", all_data, quarters, rollrate, realized, "bdc")
        self.assertGreaterEqual(score["overallScore"], 80)
        self.assertIn(score["category"], ("Strong", "Exceptional"))
        self.assertEqual(score["dataCoveragePct"], 100)
        self.assertTrue(score["positiveDrivers"])

    def test_score_reweights_when_loss_data_is_missing(self):
        quarters = ["Q1'26", "Q2'26"]
        all_data = {
            "Q1'26": [{"name": "A", "fv": 100_000, "mark": 98}],
            "Q2'26": [{"name": "A", "fv": 100_000, "mark": 97}],
        }
        rollrate = {
            "avg_rates": {
                "forward_roll": [4.0],
                "cure": [2.0],
                "stay": [90.0],
                "exit": [1.0],
            }
        }
        realized = [
            {"quarter": "Q1 2026", "dataAvailable": False},
            {"quarter": "Q2 2026", "dataAvailable": False},
        ]
        score = analyze.compute_creditpulse_score("TEST", all_data, quarters, rollrate, realized, "bdc")
        self.assertIsNotNone(score["overallScore"])
        self.assertIsNone(score["componentScores"]["lossesAndRecoveries"])
        self.assertEqual(score["dataCoveragePct"], 80)
        self.assertEqual(score["confidence"], "Medium")


if __name__ == "__main__":
    unittest.main()
