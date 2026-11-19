import asyncio
import importlib.util
import pathlib
import unittest

import httpx


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("analyze", ROOT / "api" / "analyze.py")
analyze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyze)

from api.bdc_universe import active_bdc_universe


def _client_failing_all_requests():
    """A client whose every request fails, proving no network lookup is needed."""
    def handler(request):
        raise httpx.ConnectError("network disabled in test", request=request)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class CikLookupTests(unittest.TestCase):
    def test_every_universe_bdc_resolves_without_network(self):
        async def run():
            async with _client_failing_all_requests() as client:
                return {
                    row["ticker"]: await analyze.lookup_cik(client, row["ticker"])
                    for row in active_bdc_universe()
                }
        resolved = asyncio.run(run())
        for row in active_bdc_universe():
            self.assertEqual(resolved[row["ticker"]], row["cik"], row["ticker"])

    def test_company_tickers_file_parsing(self):
        payload = {
            "0": {"cik_str": 1487918, "ticker": "OFS", "title": "OFS Capital Corp"},
            "1": {"cik_str": 1143513, "ticker": "GLAD", "title": "GLADSTONE CAPITAL CORP"},
        }
        self.assertEqual(analyze.cik_from_company_tickers(payload, "GLAD"), "0001143513")
        self.assertIsNone(analyze.cik_from_company_tickers(payload, "ZZZZ"))

    def test_company_tickers_used_for_tickers_outside_universe(self):
        def handler(request):
            if request.url.path == "/files/company_tickers.json":
                return httpx.Response(200, json={
                    "0": {"cik_str": 1234567, "ticker": "NEWB", "title": "New BDC"},
                })
            return httpx.Response(400, text="Bad Request")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await analyze.lookup_cik(client, "NEWB")
        self.assertEqual(asyncio.run(run()), "0001234567")


if __name__ == "__main__":
    unittest.main()
