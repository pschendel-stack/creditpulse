"""Market-data provider abstraction for CreditPulse.

The app never scrapes Yahoo Finance HTML. Providers are explicit, configured by
environment variables, and may be swapped without changing page or valuation
logic.
"""

import os
from datetime import datetime, timezone
from typing import Optional

import httpx


class MarketDataProvider:
    name = "unconfigured"

    async def quote(self, client: httpx.AsyncClient, ticker: str) -> Optional[dict]:
        return None

    async def historical_prices(self, client: httpx.AsyncClient, ticker: str, start: str, end: str) -> list:
        return []

    async def dividends(self, client: httpx.AsyncClient, ticker: str) -> list:
        return []


class NasdaqMarketDataProvider(MarketDataProvider):
    name = "nasdaq"

    async def quote(self, client: httpx.AsyncClient, ticker: str) -> Optional[dict]:
        r = await client.get(
            f"https://api.nasdaq.com/api/quote/{ticker}/info",
            params={"assetclass": "stocks"},
            headers={
                "User-Agent": "Mozilla/5.0 CreditPulse Analyzer",
                "Accept": "application/json",
                "Origin": "https://www.nasdaq.com",
                "Referer": f"https://www.nasdaq.com/market-activity/stocks/{ticker.lower()}",
            },
            timeout=20,
        )
        r.raise_for_status()
        data = (r.json().get("data") or {})
        primary = data.get("primaryData") or {}
        price = _money(primary.get("lastSalePrice"))
        trade_date = primary.get("lastTradeTimestamp")
        return {
            "provider": self.name,
            "ticker": ticker,
            "price": price,
            "sharesOutstanding": None,
            "equityMarketCapitalization": None,
            "timestamp": trade_date or datetime.now(timezone.utc).isoformat(),
        }


class FmpMarketDataProvider(MarketDataProvider):
    name = "financialmodelingprep"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def quote(self, client: httpx.AsyncClient, ticker: str) -> Optional[dict]:
        r = await client.get(
            f"https://financialmodelingprep.com/api/v3/quote/{ticker}",
            params={"apikey": self.api_key},
            timeout=20,
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        row = rows[0]
        price = _num(row.get("price"))
        shares = _num(row.get("sharesOutstanding"))
        market_cap = _num(row.get("marketCap"))
        if not market_cap and price and shares:
            market_cap = price * shares
        return {
            "provider": self.name,
            "ticker": ticker,
            "price": price,
            "sharesOutstanding": shares,
            "equityMarketCapitalization": market_cap,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def historical_prices(self, client: httpx.AsyncClient, ticker: str, start: str, end: str) -> list:
        r = await client.get(
            f"https://financialmodelingprep.com/api/v3/historical-price-full/{ticker}",
            params={"from": start, "to": end, "apikey": self.api_key},
            timeout=25,
        )
        r.raise_for_status()
        rows = r.json().get("historical") or []
        return [
            {"date": row.get("date"), "close": _num(row.get("close"))}
            for row in rows
            if row.get("date") and _num(row.get("close")) is not None
        ]

    async def dividends(self, client: httpx.AsyncClient, ticker: str) -> list:
        r = await client.get(
            f"https://financialmodelingprep.com/api/v3/historical-price-full/stock_dividend/{ticker}",
            params={"apikey": self.api_key},
            timeout=25,
        )
        r.raise_for_status()
        rows = r.json().get("historical") or []
        return [
            {"date": row.get("date"), "dividend": _num(row.get("dividend"))}
            for row in rows
            if row.get("date") and _num(row.get("dividend")) is not None
        ]


def get_market_data_provider() -> MarketDataProvider:
    provider = os.environ.get("MARKET_DATA_PROVIDER", "nasdaq").lower().strip()
    if provider in {"nasdaq", "free", "local"}:
        return NasdaqMarketDataProvider()
    if provider in {"fmp", "financialmodelingprep"} and os.environ.get("FMP_API_KEY"):
        return FmpMarketDataProvider(os.environ["FMP_API_KEY"])
    return NasdaqMarketDataProvider()


def _num(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value):
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    return _num(value)
