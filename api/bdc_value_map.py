"""Snapshot assembly and valuation model for the BDC Value Map."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Optional

import httpx

from api.bdc_universe import PEER_GROUPS, active_bdc_universe, universe_by_ticker
from api.valuation_config import VALUATION_MODEL


SNAPSHOT_SELECT = "ticker,snapshot_date,as_of,snapshot_json,error,stale"
LOCAL_SNAPSHOT_FILE = Path(os.environ.get(
    "BDC_VALUE_LOCAL_SNAPSHOT_FILE",
    ".cache/bdc_value_snapshots.json",
))
DATA_UA = {"User-Agent": "BDC Analyzer pschendel@gmail.com", "Accept": "application/json"}
MARKET_REFRESHED_BY = "market-only refresh + existing CreditPulse snapshot"


def local_snapshot_store_enabled() -> bool:
    return os.environ.get("BDC_VALUE_USE_LOCAL_STORE", "").lower() in {"1", "true", "yes"} or not os.environ.get("VERCEL")


def snapshot_store_configured(supabase_url: str, supabase_key: str) -> bool:
    return bool(supabase_url and supabase_key) or local_snapshot_store_enabled()


def value_map_snapshot_from_analysis(universe_row: dict, analysis: dict, market: Optional[dict] = None) -> dict:
    """Build a dated value-map snapshot from existing analyzer output."""
    market = market or {}
    now = datetime.now(timezone.utc).isoformat()
    score = analysis.get("credit_score") or {}
    summary = analysis.get("summary") or {}
    leverage = analysis.get("leverage") or {}
    obs = score.get("observations") or {}
    latest_leverage = leverage.get("latest") or {}
    nav_per_share = _first_number(
        (analysis.get("valuation_metrics") or {}).get("latestNavPerShare"),
        market.get("navPerShare"),
    )
    shares_outstanding = _first_number(
        market.get("sharesOutstanding"),
        (analysis.get("valuation_metrics") or {}).get("sharesOutstanding"),
    )
    net_assets = _first_number(summary.get("net_assets"), market.get("netAssets"))
    if nav_per_share is None and net_assets and shares_outstanding:
        nav_per_share = net_assets / shares_outstanding
    price = _first_number(market.get("price"), market.get("previousClose"))
    price_to_nav = price / nav_per_share if price and nav_per_share else None
    discount_premium = price_to_nav - 1 if price_to_nav is not None else None
    market_cap = _first_number(market.get("equityMarketCapitalization"), market.get("marketCap"))
    if market_cap is None and price and shares_outstanding:
        market_cap = price * shares_outstanding

    return {
        "ticker": universe_row["ticker"],
        "legalName": universe_row["legalName"],
        "cik": universe_row["cik"],
        "exchange": universe_row["exchange"],
        "peerGroup": universe_row["peerGroup"],
        "managementStructure": universe_row["managementStructure"],
        "managerName": universe_row.get("managerName"),
        "specialty": universe_row.get("specialty"),
        "currentCreditPulseScore": score.get("overallScore"),
        "previousQuarterCreditPulseScore": score.get("priorPeriodScore"),
        "scoreChange": score.get("scoreChange"),
        "scoreTrend": score.get("trend"),
        "creditPulseConfidence": score.get("confidence"),
        "creditPulseDataCoveragePct": score.get("dataCoveragePct"),
        "stockPrice": price,
        "stockPriceTimestamp": market.get("timestamp"),
        "marketDataAsOf": market.get("timestamp"),
        "marketRefreshedAt": now,
        "latestNavPerShare": nav_per_share,
        "navQuarterEndDate": (analysis.get("valuation_metrics") or {}).get("navQuarterEndDate"),
        "actualPriceToNav": price_to_nav,
        "discountPremiumToNav": discount_premium,
        "equityMarketCapitalization": market_cap,
        "sharesOutstanding": shares_outstanding,
        "navPerShareChange1yPct": (analysis.get("valuation_metrics") or {}).get("navPerShareChange1yPct"),
        "navPerShareChange3yPct": (analysis.get("valuation_metrics") or {}).get("navPerShareChange3yPct"),
        "regularDividendYield": (analysis.get("valuation_metrics") or {}).get("regularDividendYield"),
        "cashNiiDividendCoverage": (analysis.get("valuation_metrics") or {}).get("cashNiiDividendCoverage"),
        "cashNiiCoverageReasonUnavailable": (analysis.get("valuation_metrics") or {}).get("cashNiiCoverageReasonUnavailable")
            or "Cash NII coverage is unavailable until NII, PIK income, and regular dividend tags are mapped for this filer.",
        "reportedNiiDividendCoverage": (analysis.get("valuation_metrics") or {}).get("reportedNiiDividendCoverage"),
        "niiReturnOnNavPct": (analysis.get("valuation_metrics") or {}).get("niiReturnOnNavPct"),
        "pikIncomePctOfTotalInvestmentIncome": (analysis.get("valuation_metrics") or {}).get("pikIncomePctOfTotalInvestmentIncome"),
        "nonAccrualFairValuePct": (analysis.get("valuation_metrics") or {}).get("nonAccrualFairValuePct"),
        "nonAccrualCostPct": None,
        "realizedLossesLtmPctOfAverageNav": (analysis.get("valuation_metrics") or {}).get("realizedLossesLtmPctOfAverageNav"),
        "realizedLossesWindowPctOfAvgPortfolio": (analysis.get("realized_losses") or {}).get("gains_summary", {}).get("cum_realized_pct"),
        "debtToEquity": latest_leverage.get("deRatio"),
        "firstLienPct": (analysis.get("valuation_metrics") or {}).get("firstLienPct"),
        "equityExposurePct": summary.get("equity_pct"),
        "currentPNav": price_to_nav,
        "threeYearMedianPNav": None,
        "threeYearPNav25th": None,
        "threeYearPNav75th": None,
        "currentDeviationFromThreeYearMedian": None,
        "analysisAsOf": now,
        "creditMetricsAsOf": now,
        "source": "market provider + CreditPulse analyzer cache",
        "sourceNotes": [
            "CreditPulse Score is unchanged and excludes price, market cap, dividend yield, and P/NAV.",
            "NAV/share may be calculated as latest parsed net assets divided by SEC-reported shares outstanding.",
            "Unavailable fields remain null rather than substituted from weaker proxies.",
        ],
    }


def apply_market_data_to_snapshot(snapshot: dict, market: Optional[dict] = None) -> dict:
    """Update only market-driven fields on an existing value-map snapshot."""
    market = market or {}
    out = dict(snapshot or {})
    now = datetime.now(timezone.utc).isoformat()
    price = _first_number(market.get("price"), market.get("previousClose"), out.get("stockPrice"))
    nav_per_share = _first_number(out.get("latestNavPerShare"), market.get("navPerShare"))
    shares_outstanding = _first_number(
        market.get("sharesOutstanding"),
        out.get("sharesOutstanding"),
    )
    market_cap = _first_number(
        market.get("equityMarketCapitalization"),
        market.get("marketCap"),
    )
    if market_cap is None and price and shares_outstanding:
        market_cap = price * shares_outstanding
    price_to_nav = price / nav_per_share if price and nav_per_share else None

    out["stockPrice"] = price
    out["stockPriceTimestamp"] = market.get("timestamp") or now
    out["marketDataAsOf"] = market.get("timestamp") or now
    out["marketRefreshedAt"] = now
    out["sharesOutstanding"] = shares_outstanding
    out["equityMarketCapitalization"] = market_cap
    out["actualPriceToNav"] = price_to_nav
    out["currentPNav"] = price_to_nav
    out["discountPremiumToNav"] = price_to_nav - 1 if price_to_nav is not None else None
    out["creditMetricsAsOf"] = out.get("creditMetricsAsOf") or out.get("analysisAsOf") or out.get("snapshotDate")
    out["source"] = MARKET_REFRESHED_BY
    notes = list(out.get("sourceNotes") or [])
    note = "Market-only refresh updates price, market cap, P/NAV, discount/premium, valuation gap, and labels without recomputing CreditPulse Scores."
    if note not in notes:
        notes.append(note)
    out["sourceNotes"] = notes
    return out


async def fetch_sec_company_facts_market_metrics(client: httpx.AsyncClient, cik: str) -> dict:
    try:
        r = await client.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{str(cik).zfill(10)}.json",
            headers=DATA_UA,
            timeout=20,
        )
        r.raise_for_status()
        facts = r.json().get("facts") or {}
        shares = _latest_fact_value(
            facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}),
            "shares",
        )
        public_float = _latest_fact_value(
            facts.get("dei", {}).get("EntityPublicFloat", {}),
            "USD",
        )
        return {
            "sharesOutstanding": shares,
            "secPublicFloat": public_float,
        }
    except Exception:
        return {}


async def load_value_map_response(
    client: httpx.AsyncClient,
    supabase_url: str,
    supabase_key: str,
    peer_group: Optional[str] = None,
    snapshot_table: str = "bdc_value_snapshots",
) -> dict:
    universe = active_bdc_universe()
    rows = await load_latest_snapshots(client, supabase_url, supabase_key, snapshot_table)
    rows = attach_missing_universe_rows(rows, universe)
    rows = apply_peer_implied_valuation(rows)

    if peer_group:
        rows = [r for r in rows if r.get("peerGroup") == peer_group]

    rows.sort(key=lambda r: (
        r.get("equityMarketCapitalization") is None,
        -(r.get("equityMarketCapitalization") or 0),
        r.get("ticker") or "",
    ))
    rows = rows[:50]
    storage_configured = snapshot_store_configured(supabase_url, supabase_key)
    return {
        "asOf": _max_as_of(rows),
        "marketDataAsOf": _max_field(rows, "marketDataAsOf", "stockPriceTimestamp"),
        "marketRefreshedAt": _max_field(rows, "marketRefreshedAt", "snapshotDate"),
        "creditMetricsAsOf": _max_field(rows, "creditMetricsAsOf", "analysisAsOf"),
        "dataSource": "Supabase bdc_value_snapshots" if supabase_url and supabase_key
            else "Local development snapshot file" if local_snapshot_store_enabled()
            else "No production snapshot store configured",
        "status": "ok" if any(r.get("hasSnapshot") for r in rows) else "no_snapshots",
        "message": None if any(r.get("hasSnapshot") for r in rows) else
            "No BDC Value Map snapshots are available yet." if storage_configured else
            "Production snapshot storage is not configured. Set up Supabase before publishing live Value Map data.",
        "capabilities": {
            "refreshEnabled": storage_configured,
            "usesLocalSnapshotStore": bool(not supabase_url and not supabase_key and local_snapshot_store_enabled()),
        },
        "peerGroups": PEER_GROUPS,
        "valuationModel": VALUATION_MODEL,
        "summary": summarize_value_map(rows),
        "rows": rows,
        "universeCount": len(universe),
    }


async def load_latest_snapshots(client, supabase_url: str, supabase_key: str, snapshot_table: str) -> list:
    if not supabase_url or not supabase_key:
        return load_local_snapshots() if local_snapshot_store_enabled() else []
    try:
        r = await client.get(
            f"{supabase_url}/rest/v1/{snapshot_table}",
            params={"select": SNAPSHOT_SELECT, "order": "snapshot_date.desc", "limit": "500"},
            headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"},
            timeout=12,
        )
        if r.status_code >= 400:
            return []
        latest = {}
        for row in r.json():
            ticker = row.get("ticker")
            payload = row.get("snapshot_json") or {}
            if ticker and ticker not in latest:
                payload["ticker"] = ticker
                payload["snapshotDate"] = row.get("snapshot_date")
                payload["asOf"] = row.get("as_of") or row.get("snapshot_date")
                payload["hasSnapshot"] = True
                payload["stale"] = bool(row.get("stale"))
                payload["error"] = row.get("error")
                latest[ticker] = payload
        return list(latest.values())
    except Exception:
        return []


async def save_value_snapshot(client, supabase_url: str, supabase_key: str, snapshot_table: str, snapshot: dict, error: Optional[str] = None):
    if not supabase_url or not supabase_key:
        return save_local_snapshot(snapshot, error) if local_snapshot_store_enabled() else False
    now = datetime.now(timezone.utc).isoformat()
    r = await client.post(
        f"{supabase_url}/rest/v1/{snapshot_table}",
        json={
            "ticker": snapshot.get("ticker"),
            "cik": snapshot.get("cik"),
            "snapshot_date": now,
            "as_of": snapshot.get("stockPriceTimestamp") or snapshot.get("analysisAsOf") or now,
            "snapshot_json": snapshot,
            "stale": bool(error),
            "error": error,
        },
        headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
        },
        timeout=12,
    )
    # A non-2xx here (e.g. Postgres rejecting a malformed field) previously
    # went unnoticed: this always returned True regardless of the response,
    # so callers reported "stored" even when nothing was written.
    r.raise_for_status()
    return True


def load_local_snapshots(path: Path = LOCAL_SNAPSHOT_FILE) -> list:
    try:
        if not path.exists():
            return []
        rows = json.loads(path.read_text())
        latest = {}
        for row in sorted(rows, key=lambda r: r.get("snapshot_date") or "", reverse=True):
            payload = row.get("snapshot_json") or {}
            ticker = row.get("ticker") or payload.get("ticker")
            if ticker and ticker not in latest:
                payload["ticker"] = ticker
                payload["snapshotDate"] = row.get("snapshot_date")
                payload["asOf"] = row.get("as_of") or row.get("snapshot_date")
                payload["hasSnapshot"] = True
                payload["stale"] = bool(row.get("stale"))
                payload["error"] = row.get("error")
                latest[ticker] = payload
        return list(latest.values())
    except Exception:
        return []


def save_local_snapshot(snapshot: dict, error: Optional[str] = None, path: Path = LOCAL_SNAPSHOT_FILE):
    now = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if path.exists():
        try:
            rows = json.loads(path.read_text())
        except Exception:
            rows = []
    rows.append({
        "ticker": snapshot.get("ticker"),
        "cik": snapshot.get("cik"),
        "snapshot_date": now,
        "as_of": snapshot.get("stockPriceTimestamp") or snapshot.get("analysisAsOf") or now,
        "snapshot_json": snapshot,
        "stale": bool(error),
        "error": error,
    })
    path.write_text(json.dumps(rows, indent=2, sort_keys=True))
    return True


def attach_missing_universe_rows(snapshot_rows: list, universe: list) -> list:
    by_ticker = {r.get("ticker"): dict(r) for r in snapshot_rows}
    out = []
    for u in universe:
        row = by_ticker.get(u["ticker"], {})
        merged = {**u, **row}
        merged.setdefault("hasSnapshot", False)
        merged.setdefault("classification", "Insufficient data")
        merged.setdefault("classificationClass", "insufficient")
        out.append(merged)
    return out


def apply_peer_implied_valuation(rows: list) -> list:
    model = VALUATION_MODEL
    rows = [dict(r) for r in rows]
    for row in rows:
        row["_componentPercentiles"] = {}

    groups = {}
    for row in rows:
        groups.setdefault(row.get("peerGroup"), []).append(row)
    global_rows = [r for r in rows if _num(r.get("actualPriceToNav")) is not None]

    for group_name, group_rows in groups.items():
        norm_base = group_rows if len(group_rows) >= model["minimumPeerCount"] else global_rows
        _assign_percentile(group_rows, norm_base, "currentCreditPulseScore", high_is_good=True)
        _assign_percentile(group_rows, norm_base, "niiReturnOnNavPct", high_is_good=True)
        _assign_percentile(group_rows, norm_base, "navPerShareChange1yPct", high_is_good=True)
        _assign_percentile(group_rows, norm_base, "navPerShareChange3yPct", high_is_good=True)
        _assign_percentile(group_rows, norm_base, "debtToEquity", high_is_good=False)
        _assign_percentile(group_rows, norm_base, "equityMarketCapitalization", high_is_good=True)

    for row in rows:
        p = row["_componentPercentiles"]
        components = {
            "creditPulseScore": p.get("currentCreditPulseScore"),
            "earningsPower": p.get("niiReturnOnNavPct"),
            "navPreservation": _mean_present([p.get("navPerShareChange1yPct"), p.get("navPerShareChange3yPct")]),
            "structuralQuality": _mean_present([
                p.get("debtToEquity"),
                p.get("equityMarketCapitalization"),
                1.0 if row.get("managementStructure") == "Internally managed" else 0.45 if row.get("managementStructure") else None,
            ]),
        }
        available_weight = sum(model["weights"][k] for k, v in components.items() if v is not None)
        composite = None
        if available_weight:
            composite = sum(components[k] * model["weights"][k] for k, v in components.items() if v is not None) / available_weight
        row["valuationQualityComposite"] = round(composite * 100, 1) if composite is not None else None
        row["valuationComponents"] = {k: (round(v * 100, 1) if v is not None else None) for k, v in components.items()}
        peer_rows = groups.get(row.get("peerGroup"), [])
        pnav_values = sorted(_num(r.get("actualPriceToNav")) for r in peer_rows if _num(r.get("actualPriceToNav")) is not None)
        if len(pnav_values) < model["minimumPeerCount"]:
            pnav_values = sorted(_num(r.get("actualPriceToNav")) for r in global_rows if _num(r.get("actualPriceToNav")) is not None)
        row["peerImpliedPriceToNav"] = _quantile(pnav_values, composite) if composite is not None else None
        if row["peerImpliedPriceToNav"] is not None:
            row["peerImpliedPriceToNav"] = round(row["peerImpliedPriceToNav"], 3)
        actual = _num(row.get("actualPriceToNav"))
        row["valuationGap"] = round(row["peerImpliedPriceToNav"] - actual, 3) if actual is not None and row["peerImpliedPriceToNav"] is not None else None
        row["classification"], row["classificationClass"] = classify_valuation(row)
        row["historicalValuationConclusion"] = classify_historical_valuation(row)
        row.pop("_componentPercentiles", None)
    return rows


def classify_valuation(row: dict):
    coverage = _num(row.get("creditPulseDataCoveragePct")) or 0
    gap = _num(row.get("valuationGap"))
    if coverage < VALUATION_MODEL["minimumCoveragePct"] or gap is None:
        return "Insufficient data", "insufficient"
    for rule in VALUATION_MODEL["classificationThresholds"]:
        lo = rule.get("min")
        hi = rule.get("max")
        if (lo is None or gap >= lo) and (hi is None or gap <= hi):
            return rule["label"], rule["className"]
    return "Insufficient data", "insufficient"


def classify_historical_valuation(row: dict) -> str:
    deviation = _num(row.get("currentDeviationFromThreeYearMedian"))
    if deviation is None:
        return "Insufficient data"
    if deviation <= -0.10:
        return "Below own 3-year median"
    if deviation >= 0.10:
        return "Above own 3-year median"
    return "Near own 3-year median"


def summarize_value_map(rows: list) -> dict:
    snap_rows = [r for r in rows if r.get("hasSnapshot")]
    scores = [_num(r.get("currentCreditPulseScore")) for r in snap_rows if _num(r.get("currentCreditPulseScore")) is not None]
    pnavs = [_num(r.get("actualPriceToNav")) for r in snap_rows if _num(r.get("actualPriceToNav")) is not None]
    discounts = [_num(r.get("discountPremiumToNav")) for r in snap_rows if _num(r.get("discountPremiumToNav")) is not None]
    current_q = [r for r in snap_rows if (_num(r.get("creditPulseDataCoveragePct")) or 0) >= VALUATION_MODEL["minimumCoveragePct"]]
    return {
        "medianCreditPulseScore": round(median(scores), 1) if scores else None,
        "medianPriceToNav": round(median(pnavs), 3) if pnavs else None,
        "medianDiscountPremium": round(median(discounts), 3) if discounts else None,
        "potentiallyUndervalued": sum(1 for r in snap_rows if r.get("classification") == "Potentially undervalued"),
        "potentiallyExpensive": sum(1 for r in snap_rows if r.get("classification") == "Potentially expensive"),
        "decliningCreditPulseScores": sum(1 for r in snap_rows if (_num(r.get("scoreChange")) or 0) < 0),
        "currentQuarterDataPct": round(len(current_q) / len(rows) * 100, 1) if rows else 0,
        "snapshotCount": len(snap_rows),
    }


def _assign_percentile(target_rows: list, base_rows: list, key: str, high_is_good: bool):
    values = sorted(_num(r.get(key)) for r in base_rows if _num(r.get(key)) is not None)
    if not values:
        return
    for row in target_rows:
        value = _num(row.get(key))
        if value is None:
            continue
        pct = _percentile_rank(values, value)
        row["_componentPercentiles"][key] = pct if high_is_good else 1 - pct


def _percentile_rank(values: list, value: float) -> float:
    if len(values) == 1:
        return 0.5
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return max(0.0, min(1.0, (below + 0.5 * equal) / len(values)))


def _quantile(values: list, q: Optional[float]):
    if not values or q is None:
        return None
    q = max(0.0, min(1.0, q))
    if len(values) == 1:
        return values[0]
    pos = q * (len(values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    frac = pos - lo
    return values[lo] * (1 - frac) + values[hi] * frac


def _mean_present(values: list):
    vals = [_num(v) for v in values if _num(v) is not None]
    return sum(vals) / len(vals) if vals else None


def _first_number(*values):
    for value in values:
        n = _num(value)
        if n is not None:
            return n
    return None


def _latest_fact_value(fact: dict, unit: str):
    rows = ((fact.get("units") or {}).get(unit) or [])
    cleaned = [
        r for r in rows
        if _num(r.get("val")) is not None and r.get("end") and r.get("filed")
    ]
    if not cleaned:
        return None
    cleaned.sort(key=lambda r: (r.get("filed") or "", r.get("end") or ""))
    return _num(cleaned[-1].get("val"))


def _num(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _max_as_of(rows: list):
    vals = [r.get("asOf") or r.get("analysisAsOf") or r.get("stockPriceTimestamp") for r in rows if r.get("hasSnapshot")]
    return max(vals) if vals else None


def _max_field(rows: list, *fields: str):
    vals = []
    for row in rows:
        if not row.get("hasSnapshot"):
            continue
        for field in fields:
            value = row.get(field)
            if value:
                vals.append(value)
                break
    return max(vals) if vals else None
