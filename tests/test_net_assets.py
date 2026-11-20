import importlib.util
import pathlib
import unittest

from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("analyze", ROOT / "api" / "analyze.py")
analyze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyze)


def _doc(facts_html: str) -> str:
    """Minimal inline-XBRL document: one whole-fund context, one segmented."""
    return f"""
    <html><body>
    <xbrli:context id="FUND"><xbrli:entity><xbrli:identifier>1</xbrli:identifier></xbrli:entity>
      <xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
    <xbrli:context id="JV"><xbrli:entity><xbrli:identifier>1</xbrli:identifier>
      <xbrli:segment><xbrldi:explicitMember dimension="dei:LegalEntityAxis">x:JointVentureMember</xbrldi:explicitMember></xbrli:segment>
      </xbrli:entity>
      <xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
    {facts_html}
    </body></html>"""


def _net_assets(html: str):
    soup = BeautifulSoup(html, "lxml")
    return analyze._best_ixbrl_instant(
        analyze._index_ixbrl_facts(soup), analyze._context_periods(soup),
        "2026-09-30", analyze.NET_ASSETS_TAGS, fund_level_only=True,
    )


class NetAssetsSelectionTests(unittest.TestCase):
    def test_slrc_style_assetsnet_beats_joint_venture_members_equity(self):
        # SLRC's Q3 2026 10-Q tags fund net assets only as AssetsNet ($972.8M);
        # the only MembersEquity fact is a joint venture's ($94.2M, segmented).
        html = _doc("""
          <ix:nonFraction name="us-gaap:MembersEquity" contextRef="JV" scale="3" decimals="-3">94,163</ix:nonFraction>
          <ix:nonFraction name="us-gaap:AssetsNet" contextRef="FUND" scale="3" decimals="-3">972,779</ix:nonFraction>
        """)
        self.assertEqual(_net_assets(html), 972_779_000)

    def test_whole_fund_lower_ranked_tag_beats_segmented_higher_ranked_tag(self):
        html = _doc("""
          <ix:nonFraction name="us-gaap:StockholdersEquity" contextRef="JV" scale="3" decimals="-3">50,000</ix:nonFraction>
          <ix:nonFraction name="us-gaap:MembersEquity" contextRef="FUND" scale="3" decimals="-3">900,000</ix:nonFraction>
        """)
        self.assertEqual(_net_assets(html), 900_000_000)

    def test_segment_only_net_assets_is_rejected(self):
        html = _doc("""
          <ix:nonFraction name="us-gaap:MembersEquity" contextRef="JV" scale="3" decimals="-3">94,163</ix:nonFraction>
        """)
        self.assertIsNone(_net_assets(html))


if __name__ == "__main__":
    unittest.main()
