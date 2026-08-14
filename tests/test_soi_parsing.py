import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("analyze", ROOT / "api" / "analyze.py")
analyze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyze)


def soi_row(cells):
    tds = "".join(f"<td>{c}</td>" for c in cells)
    return f"<tr>{tds}</tr>"


def soi_table(rows):
    # _parse_soi_table only looks at <tr>/<td>/<th>; padding keeps the table
    # above the 1500-char section-extraction threshold used elsewhere, but
    # _parse_soi_table itself has no such minimum.
    return "<table>" + "".join(rows) + "</table>"


class SoiTableParsingTests(unittest.TestCase):
    def test_fully_funded_term_loan_prices_off_par(self):
        # BLP Buyer, Inc.: Principal 49,125 / Amortized Cost 48,451 / Fair Value 49,125
        chunk = soi_table([soi_row([
            "BLP Buyer, Inc.", "SF", "6.50%", "10.17%",
            "12/22/2023", "12/21/2029",
            "49,125", "48,451", "49,125", "1.8", "%",
        ])])
        state = {"company": None, "type": "Debt"}
        rows = analyze._parse_soi_table(chunk, state, mult=1.0)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["mark"], 49125 / 49125 * 100, places=2)

    def test_revolver_prices_off_amortized_cost_not_total_commitment(self):
        # Nellson Nutraceutical, LLC (Revolver): Principal 5,078 (total
        # commitment) / Amortized Cost 1,797 (funded) / Fair Value 1,797.
        # The funded piece is marked at cost, i.e. par (100), not 35.
        chunk = soi_table([soi_row([
            "Nellson Nutraceutical, LLC (Revolver)", "SF", "5.75%", "9.45%",
            "4/17/2025", "4/17/2031",
            "5,078", "1,797", "1,797", "0.1", "%",
        ])])
        state = {"company": None, "type": "Debt"}
        rows = analyze._parse_soi_table(chunk, state, mult=1.0)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["mark"], 100.0, places=2)
        self.assertAlmostEqual(rows[0]["par"], 5078.0, places=2)  # commitment size preserved

    def test_delayed_draw_prices_off_amortized_cost(self):
        # Automated Industrial Robotics US HoldCo Inc. (Delayed Draw):
        # Principal 11,111 / Amortized Cost 4,004 / Fair Value 3,982.
        # "delayed draw" is itself a TYPE_KEYWORDS entry, so this row is only
        # parsed as a same-company continuation tranche (as it always is in
        # the actual filing) when a base row for the company precedes it.
        chunk = soi_table([
            soi_row([
                "Automated Industrial Robotics US HoldCo Inc.", "EU", "5.00%", "7.06%",
                "8/5/2025", "8/5/2032",
                "2,880", "2,857", "2,906", "0.1", "%",
            ]),
            soi_row([
                "Automated Industrial Robotics US HoldCo Inc. (Delayed Draw)", "EU", "5.00%", "7.13%",
                "8/5/2025", "8/5/2032",
                "11,111", "4,004", "3,982", "0.1", "%",
            ]),
        ])
        state = {"company": None, "type": "Debt"}
        rows = analyze._parse_soi_table(chunk, state, mult=1.0)
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[1]["mark"], 3982 / 4004 * 100, places=2)


if __name__ == "__main__":
    unittest.main()
