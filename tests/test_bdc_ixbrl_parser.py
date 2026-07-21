import unittest

from api.analyze import (
    _extract_bdc_investments_from_ixbrl_facts,
    _parse_bdc_ixbrl_investment_domain,
)


class BdcIxbrlParserTests(unittest.TestCase):
    def test_domain_parser_preserves_company_suffixes(self):
        parsed = _parse_bdc_ixbrl_investment_domain(
            "Debt Investments, Pharmaceuticals, Phathom Pharmaceuticals, Inc., "
            "Senior Secured, February 2029"
        )

        self.assertEqual(parsed["name"], "Phathom Pharmaceuticals, Inc.")
        self.assertIn("Senior Secured", parsed["type"])

    def test_domain_parser_drops_other_prefix_before_borrower(self):
        parsed = _parse_bdc_ixbrl_investment_domain(
            "Debt Investments, Healthcare Services, Other, Marathon Health, LLC, "
            "Senior Secured, February 2029"
        )

        self.assertEqual(parsed["name"], "Marathon Health, LLC")

    def test_domain_parser_handles_gsbd_reference_rate_and_spread(self):
        parsed = _parse_bdc_ixbrl_investment_domain(
            "Investment Debt Investments - 233.2% United States - 220.5% "
            "1st Lien/Senior Secured Debt - 206.5% QBS Parent, Inc. "
            "(dba Quorum Software) Industry IT Services Reference Rate and Spread "
            "S + 4.50% Maturity 06/03/32"
        )

        self.assertEqual(parsed["name"], "QBS Parent, Inc. (dba Quorum Software)")
        self.assertIn("Debt Investments", parsed["type"])
        self.assertIn("1st Lien/Senior Secured Debt", parsed["type"])

    def test_domain_parser_handles_gsbd_equity_with_industry_label(self):
        parsed = _parse_bdc_ixbrl_investment_domain(
            "Investment Equity Securities - 2.5% United States - 0.1% Common Stock "
            "- 0.1% Social Media Holdings, Inc. Industry Interactive Media & Services "
            "Initial Acquisition Date 05/31/23"
        )

        self.assertEqual(parsed["name"], "Social Media Holdings, Inc.")
        self.assertIn("Equity Securities", parsed["type"])
        self.assertIn("Common Stock", parsed["type"])

    def test_extracts_period_facts_and_skips_totals(self):
        html = """
        <html><body>
          <xbrli:context id="c1">
            <xbrli:entity><xbrli:identifier>0000000000</xbrli:identifier>
              <xbrli:segment>
                <xbrldi:typedMember dimension="us-gaap:InvestmentIdentifierAxis">
                  <us-gaap:investmentidentifieraxis.domain>
                    Debt Investments, Software, Borrower, Inc., Senior Secured, May 2028
                  </us-gaap:investmentidentifieraxis.domain>
                </xbrldi:typedMember>
              </xbrli:segment>
            </xbrli:entity>
            <xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period>
          </xbrli:context>
          <xbrli:context id="c2">
            <xbrli:entity><xbrli:identifier>0000000000</xbrli:identifier>
              <xbrli:segment>
                <xbrldi:typedMember dimension="us-gaap:InvestmentIdentifierAxis">
                  <us-gaap:investmentidentifieraxis.domain>
                    Warrant Investments, Healthcare Services, Other, Curana Health Holdings, LLC.,
                    Warrant, Common Units
                  </us-gaap:investmentidentifieraxis.domain>
                </xbrldi:typedMember>
              </xbrli:segment>
            </xbrli:entity>
            <xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period>
          </xbrli:context>
          <xbrli:context id="c3">
            <xbrli:entity><xbrli:identifier>0000000000</xbrli:identifier>
              <xbrli:segment>
                <xbrldi:typedMember dimension="us-gaap:InvestmentIdentifierAxis">
                  <us-gaap:investmentidentifieraxis.domain>
                    Debt Investments, Software, Total Borrower, Inc.
                  </us-gaap:investmentidentifieraxis.domain>
                </xbrldi:typedMember>
              </xbrli:segment>
            </xbrli:entity>
            <xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period>
          </xbrli:context>
          <xbrli:context id="c4">
            <xbrli:entity><xbrli:identifier>0000000000</xbrli:identifier>
              <xbrli:segment>
                <xbrldi:typedMember dimension="us-gaap:InvestmentIdentifierAxis">
                  <us-gaap:investmentidentifieraxis.domain>
                    Debt Investments, Software, Prior Period LLC, Senior Secured
                  </us-gaap:investmentidentifieraxis.domain>
                </xbrldi:typedMember>
              </xbrli:segment>
            </xbrli:entity>
            <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period>
          </xbrli:context>

          <ix:nonFraction name="us-gaap:InvestmentOwnedBalancePrincipalAmount" contextRef="c1" scale="3">2,000</ix:nonFraction>
          <ix:nonFraction name="us-gaap:InvestmentOwnedAtFairValue" contextRef="c1" scale="3">1,850</ix:nonFraction>
          <ix:nonFraction name="us-gaap:InvestmentOwnedAtCost" contextRef="c2" scale="3">100</ix:nonFraction>
          <ix:nonFraction name="us-gaap:InvestmentOwnedAtFairValue" contextRef="c2" scale="3">400</ix:nonFraction>
          <ix:nonFraction name="us-gaap:InvestmentOwnedAtFairValue" contextRef="c3" scale="3">9,999</ix:nonFraction>
          <ix:nonFraction name="us-gaap:InvestmentOwnedAtFairValue" contextRef="c4" scale="3">8,888</ix:nonFraction>
        </body></html>
        """

        invs = _extract_bdc_investments_from_ixbrl_facts(html, "2026-03-31")

        self.assertEqual([i["name"] for i in invs], ["Borrower, Inc.", "Curana Health Holdings, LLC."])
        self.assertEqual([i["fv"] for i in invs], [1850.0, 400.0])
        self.assertEqual(invs[0]["mark"], 92.5)
        self.assertEqual(invs[1]["mark"], 400.0)


if __name__ == "__main__":
    unittest.main()
