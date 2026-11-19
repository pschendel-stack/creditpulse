"""Scheduled BDC Value Map refresh (run by .github/workflows/bdc-refresh.yml).

For every active BDC in the curated universe:

* If SEC shows a 10-Q/10-K for a period newer than the stored snapshot's
  ``latestFilingPeriod`` (or --force / no snapshot yet), recompute the full
  CreditPulse analysis from SEC filings and store a new snapshot.
* Otherwise, update market-driven fields (price, P/NAV, market cap) only.

Runs in-process, so it has none of the serverless time or memory limits that
stop the largest filers (e.g. ARCC) from finishing on Vercel. Writes go to the
Supabase ``bdc_value_snapshots`` table configured by SUPABASE_URL/SUPABASE_KEY.

Exit status is non-zero when any ticker fails, so the workflow run goes red
and GitHub notifies the repository owner.

    python scripts/refresh_bdc_value_map.py [--force] [--tickers ARCC,OFS] [--dry-run]
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from api import analyze as analyzer  # noqa: E402
from api.bdc_universe import active_bdc_universe  # noqa: E402
from api.bdc_value_map import (  # noqa: E402
    apply_market_data_to_snapshot,
    fetch_sec_company_facts_market_metrics,
    load_latest_snapshots,
    save_value_snapshot,
    value_map_snapshot_from_analysis,
)
from api.market_data import get_market_data_provider  # noqa: E402

PER_TICKER_TIMEOUT_S = 1200


def latest_filing_period(submissions: Optional[dict]) -> Optional[str]:
    """Report period (YYYY-MM-DD) of the newest 10-Q/10-K, or None."""
    filings = analyzer.get_recent_bdc_filings(submissions or {}, n=1)
    return filings[-1]["period"] if filings and filings[-1].get("period") else None


def needs_full_refresh(prior: Optional[dict], sec_period: Optional[str], force: bool) -> bool:
    if force or not prior:
        return True
    stored = prior.get("latestFilingPeriod")
    if not stored:
        return True
    return bool(sec_period and sec_period > stored)


async def _quote(provider, client, ticker: str) -> dict:
    try:
        return await provider.quote(client, ticker) or {}
    except Exception as exc:  # market data is best-effort; filings are the point
        print(f"  ! {ticker}: market quote failed ({exc}); continuing without it")
        return {}


async def refresh_one(client, provider, row: dict, prior: Optional[dict], args, store) -> dict:
    ticker = row["ticker"]
    subs = await analyzer.get_submissions(client, row["cik"])
    sec_period = latest_filing_period(subs)

    if needs_full_refresh(prior, sec_period, args.force):
        stored_period = (prior or {}).get("latestFilingPeriod")
        print(f"- {ticker}: full refresh (SEC latest {sec_period}, stored {stored_period})")
        if args.dry_run:
            return {"ticker": ticker, "mode": "full (dry run)", "period": sec_period}
        market = await _quote(provider, client, ticker)
        market = {**market, **await fetch_sec_company_facts_market_metrics(client, row["cik"])}
        started = time.monotonic()
        analysis = await asyncio.wait_for(
            analyzer.analyze(ticker=ticker, refresh=True), timeout=PER_TICKER_TIMEOUT_S
        )
        snapshot = value_map_snapshot_from_analysis(row, analysis, market)
        snapshot["latestFilingPeriod"] = sec_period
        await store(snapshot)
        return {
            "ticker": ticker, "mode": "full", "period": sec_period,
            "score": snapshot.get("currentCreditPulseScore"),
            "seconds": round(time.monotonic() - started),
        }

    print(f"- {ticker}: no new filing (latest {sec_period}); market-only update")
    if args.dry_run:
        return {"ticker": ticker, "mode": "market (dry run)", "period": sec_period}
    market = await _quote(provider, client, ticker)
    if not market:
        return {"ticker": ticker, "mode": "skipped (no quote)", "period": sec_period}
    snapshot = apply_market_data_to_snapshot(prior, market)
    await store(snapshot)
    return {"ticker": ticker, "mode": "market", "period": sec_period}


def write_summary(results: list, failures: list):
    lines = [
        "## BDC Value Map refresh",
        "",
        f"Full recomputes: **{sum(1 for r in results if r['mode'] == 'full')}** · "
        f"market-only: **{sum(1 for r in results if r['mode'] == 'market')}** · "
        f"failures: **{len(failures)}**",
        "",
        "| Ticker | Mode | Latest filing | Score | Seconds |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['ticker']} | {r['mode']} | {r.get('period') or ''} | "
                     f"{r.get('score') if r.get('score') is not None else ''} | {r.get('seconds', '')} |")
    for f in failures:
        lines.append(f"| {f['ticker']} | **FAILED** | | | {f['error'][:200]} |")
    text = "\n".join(lines) + "\n"
    print(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as fh:
            fh.write(text)


async def main(args) -> int:
    url, key = analyzer.SUPABASE_URL, analyzer.SUPABASE_KEY
    table = analyzer.BDC_VALUE_SNAPSHOT_TABLE
    if not (url and key) and not args.dry_run:
        print("SUPABASE_URL and SUPABASE_KEY must be set (repository secrets).")
        return 2

    selected = {t.strip().upper() for t in (args.tickers or "").split(",") if t.strip()}
    universe = [u for u in active_bdc_universe() if not selected or u["ticker"] in selected]
    provider = get_market_data_provider()
    results, failures = [], []

    async with httpx.AsyncClient(follow_redirects=True) as client:
        previous = {r.get("ticker"): r for r in await load_latest_snapshots(client, url, key, table)}

        async def store(snapshot, error=None):
            return await save_value_snapshot(client, url, key, table, snapshot, error=error)

        for row in universe:
            ticker = row["ticker"]
            prior = previous.get(ticker)
            try:
                results.append(await refresh_one(client, provider, row, prior, args, store))
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                print(f"  ! {ticker}: {error}")
                failures.append({"ticker": ticker, "error": error})
                if prior and not args.dry_run:
                    stale = dict(prior, stale=True, error=error)
                    try:
                        await store(stale, error=error)
                    except Exception as store_exc:
                        print(f"  ! {ticker}: could not mark snapshot stale ({store_exc})")

    write_summary(results, failures)
    return 1 if failures else 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true", help="Recompute every selected BDC from SEC filings")
    p.add_argument("--tickers", default="", help="Comma-separated subset, e.g. ARCC,OFS")
    p.add_argument("--dry-run", action="store_true", help="Report what would happen; write nothing")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
