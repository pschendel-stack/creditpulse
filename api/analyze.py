"""
BDC / Interval Fund Analyzer — Vercel serverless API
GET /api/analyze?ticker=CCFLX[&refresh=true]

Supports:
  - Interval funds (NPORT-P filings): CCFLX, CLOA, etc.
  - BDCs (10-K / 10-Q filings): ARCC, GSBD, ORCC, etc.

Returns structured JSON with bucket analysis, roll-rate matrices, and stressed positions.
Results are cached in Supabase for 7 days.
"""

import os, re, json, asyncio
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional
import xml.etree.ElementTree as ET

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from lxml import etree
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ─── APP ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="BDC / Interval Fund Analyzer")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── CONFIG ────────────────────────────────────────────────────────────────────

# Supabase cache is optional. Configure these in Vercel/local env; when absent,
# the analyzer skips cache reads/writes and fetches fresh SEC data.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
CACHE_TTL_DAYS = 7

EDGAR_UA = {"User-Agent": "BDC Analyzer pschendel@gmail.com"}
DATA_UA  = {"User-Agent": "BDC Analyzer pschendel@gmail.com", "Accept": "application/json"}

# Mark buckets — worst (index 0) to best (index 6)
BUCKET_DEFS = [
    ("< 50",    0,   50),
    ("50–60",  50,   60),
    ("60–70",  60,   70),
    ("70–80",  70,   80),
    ("80–90",  80,   90),
    ("90–100", 90,  100),
    ("100+",  100,  999),
]
BUCKET_NAMES = [b[0] for b in BUCKET_DEFS]

ASSET_CAT_MAP = {
    "LON": "Term Loan", "DBT": "Corporate Debt", "EC": "Equity",
    "RF": "Registered Fund", "ABS": "ABS", "MBS": "MBS", "OTH": "Other",
}

NET_REALIZED_LOSS_TAGS = [
    "us-gaap:RealizedInvestmentGainsLosses",
    "us-gaap:DebtAndEquitySecuritiesRealizedGainLoss",
    "us-gaap:GainLossOnInvestments",
    "us-gaap:GainLossOnSaleOfInvestments",
    "us-gaap:RealizedGainLossInvestmentDerivativeAndForeignCurrencyTransactionPriceChangeOperatingBeforeTax",
    "us-gaap:RealizedGainLossInvestmentDerivativeAndForeignCurrencyTransactionPriceChangeOperatingAfterTax",
]

# "Net change in unrealized appreciation (depreciation)" from the statement of
# operations. GSBD and ARCC both tag it as DebtAndEquitySecuritiesUnrealizedGainLoss.
NET_UNREALIZED_TAGS = [
    "us-gaap:DebtAndEquitySecuritiesUnrealizedGainLoss",
    "us-gaap:UnrealizedGainLossOnInvestments",
    "us-gaap:UnrealizedGainLossInvestmentDerivativeAndForeignCurrencyTransactionPriceChangeOperatingBeforeTax",
    "us-gaap:UnrealizedGainLossInvestmentDerivativeAndForeignCurrencyTransactionPriceChangeOperatingAfterTax",
]

# Total net assets (equity) from the balance sheet — an instant value at the
# period end, tagged in the same 10-K/10-Q iXBRL we already download.
NET_ASSETS_TAGS = [
    "us-gaap:StockholdersEquity",
    "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "us-gaap:MembersEquity",
]

# Total borrowings (carrying amount) from the balance sheet. Ordered: prefer
# tags that represent the full debt stack. Partial tags (LineOfCredit,
# SeniorNotes) are intentionally excluded — missing data beats understated data.
TOTAL_DEBT_TAGS = [
    "us-gaap:LongTermDebt",
    "us-gaap:DebtLongtermAndShorttermCombinedAmount",
    "us-gaap:LongTermDebtNoncurrent",
]

# 1940 Act asset coverage requirements. BDCs covered here have all elected
# reduced 150% coverage (max ~2.0x debt/equity). Closed-end interval funds are
# subject to 300% coverage on borrowings (max 0.5x debt/equity), measured at
# the time indebtedness is incurred.
COVERAGE_REQUIREMENTS = {
    "bdc":           {"limit_pct": 150.0, "min_equity_per_debt": 0.5,
                      "amber_pct": 175.0, "red_pct": 160.0},
    "interval_fund": {"limit_pct": 300.0, "min_equity_per_debt": 2.0,
                      "amber_pct": 350.0, "red_pct": 320.0},
}

# SEC's company ticker endpoints are reliable for public BDC tickers but spotty
# for unlisted closed-end / interval fund share classes. These are common
# interval-fund aliases where the ticker either is absent from the ticker file
# or the company browser routes to an unrelated series trust.
INTERVAL_TICKER_CIK = {
    # Non-traded/private BDCs can be marketed under ticker-like symbols that
    # are not present in SEC's public company ticker files.
    "BCRED": "0001803498",
    "CELFX": "0001842754",
    "PFLEX": "0001688554",
    "PFFLX": "0001688554",
    "LCRDX": "0001753712",
    "LARAX": "0001753712",
    "PMFLX": "0001723701",
    "PMFAX": "0001723701",
    "OWLCX": "0002059436",
    "CAPIX": "0001937073",
    "CRDEX": "0002006100",
    "FCRIX": "0001688897",
    "FCREX": "0001688897",
    "BMACX": "0002032432",
}

# ─── UTILITY ───────────────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    n = name.upper()
    n = re.sub(
        r'\b(LLC|INC\.?|CORP\.?|LTD\.?|L\.P\.?|LP|CO\.?|THE|HOLDINGS?|GROUP|MIDCO|'
        r'ACQUISITION|ACQUCO|PRODUCTS?|ENGINEERED|TECHNOLOGIES?|SYSTEMS?|SERVICES?|'
        r'SOLUTIONS?|NETWORKS?|MANAGEMENT|PARTNERS?|CAPITAL|INTERMEDIATE|PARENT|'
        r'BUYER|MERGERSUB|ACQUIROR)\b', '', n)
    n = re.sub(r'[^A-Z0-9]', ' ', n)
    tokens = [t for t in n.split() if len(t) > 2]
    return ' '.join(tokens[:3])

def bucket_for(mark: float) -> int:
    for i, (_, lo, hi) in enumerate(BUCKET_DEFS):
        if lo <= mark < hi or (hi == 999 and mark >= lo):
            return i
    return len(BUCKET_DEFS) - 1

def period_to_label(period: str) -> str:
    """'2025-03-31' → \"Q1'25\" """
    try:
        dt = datetime.strptime(period, "%Y-%m-%d")
        q = (dt.month - 1) // 3 + 1
        return f"Q{q}'{dt.strftime('%y')}"
    except Exception:
        return period

def is_quarter_end(date_str: str) -> bool:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.month in (3, 6, 9, 12)
    except Exception:
        return False

# ─── SUPABASE CACHE ────────────────────────────────────────────────────────────

async def cache_get(client: httpx.AsyncClient, ticker: str) -> Optional[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/analysis_cache",
            params={"ticker": f"eq.{ticker}", "select": "result_json,computed_at", "limit": "1"},
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=10,
        )
        rows = r.json()
        if rows and isinstance(rows, list) and rows[0].get("result_json"):
            row = rows[0]
            computed_at = datetime.fromisoformat(row["computed_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - computed_at < timedelta(days=CACHE_TTL_DAYS):
                result = row["result_json"]
                # Bump when the realized_losses payload shape changes so stale
                # cache entries recompute instead of rendering without new fields
                if (result.get("realized_losses") or {}).get("version") != 3:
                    return None
                if (result.get("credit_score") or {}).get("version") != 1:
                    return None
                if (result.get("position_scatter") or {}).get("version") != 2:
                    return None
                if (result.get("leverage") or {}).get("version") != 1:
                    return None
                return result
    except Exception:
        pass
    return None

async def cache_set(client: httpx.AsyncClient, ticker: str, result: dict):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        await client.post(
            f"{SUPABASE_URL}/rest/v1/analysis_cache",
            json={
                "ticker": ticker,
                "cik": result.get("cik", ""),
                "fund_name": result.get("fund_name", ""),
                "fund_type": result.get("fund_type", ""),
                "quarters": result.get("quarters", []),
                "summary": result.get("summary", {}),
                "buckets": result.get("buckets", {}),
                "rollrate": result.get("rollrate", {}),
                "stress_pos": result.get("stress_positions", []),
                "realized_losses": result.get("realized_losses", {}),
                "credit_score": result.get("credit_score", {}),
                "position_scatter": result.get("position_scatter", {}),
                "result_json": result,
                "computed_at": datetime.now(timezone.utc).isoformat(),
                "error": None,
            },
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            },
            timeout=10,
        )
    except Exception:
        pass

# ─── EDGAR LOOKUPS ─────────────────────────────────────────────────────────────

async def lookup_cik(client: httpx.AsyncClient, ticker: str) -> Optional[str]:
    """Return zero-padded 10-digit CIK for a ticker, or None."""
    if ticker in INTERVAL_TICKER_CIK:
        return INTERVAL_TICKER_CIK[ticker]

    # Closed-end interval-fund tickers are usually 5 characters ending in X.
    # Prefer EDGAR full-text entity matches before the company browser, which
    # often resolves these share classes to an unrelated registrant trust.
    if len(ticker) == 5 and ticker.endswith("X"):
        cik = await lookup_cik_fulltext(client, ticker)
        if cik:
            return cik

    url = (f"https://www.sec.gov/cgi-bin/browse-edgar"
           f"?action=getcompany&company=&CIK={ticker}&type=&dateb=&owner=include&count=5")
    try:
        r = await client.get(url, headers=EDGAR_UA, timeout=20, follow_redirects=True)
        # The redirect URL or page content contains the CIK
        m = re.search(r'CIK=(\d+)', r.text)
        if m:
            return m.group(1).zfill(10)
        # Try the final URL
        m2 = re.search(r'/cgi-bin/browse-edgar\?action=getcompany&CIK=(\d+)', str(r.url))
        if m2:
            return m2.group(1).zfill(10)
    except Exception:
        pass

    # Fallback 1: open-end fund share-class tickers via the SEC ticker file
    try:
        r = await client.get("https://www.sec.gov/files/company_tickers_mf.json",
                             headers=DATA_UA, timeout=30)
        j = r.json()
        fields = j.get("fields", [])
        ci, si = fields.index("cik"), fields.index("symbol")
        for row in j.get("data", []):
            if str(row[si]).upper() == ticker:
                return str(row[ci]).zfill(10)
    except Exception:
        pass

    # Fallback 2: closed-end/interval fund tickers (e.g. CCLFX) via EDGAR
    cik = await lookup_cik_fulltext(client, ticker)
    if cik:
        return cik
    return None

async def lookup_cik_fulltext(client: httpx.AsyncClient, ticker: str) -> Optional[str]:
    """Resolve closed-end/interval fund share-class tickers via EDGAR search."""
    try:
        r = await client.get("https://efts.sec.gov/LATEST/search-index",
                             params={"q": f'"{ticker}"'}, headers=DATA_UA, timeout=20)
        j = r.json()
        buckets = j.get("aggregations", {}).get("entity_filter", {}).get("buckets", [])
        for b in buckets:
            m = re.search(r'\(' + re.escape(ticker) + r'\)\s*\(CIK (\d{10})\)', b.get("key", ""))
            if m:
                return m.group(1)
        # Some class tickers are never registered with EDGAR, so no entity name
        # carries "(TICKER)". If one entity clearly dominates the mentions,
        # that's usually the fund (renamed funds keep the same CIK).
        by_cik, total = {}, 0
        for b in buckets:
            m = re.search(r'\(CIK (\d{10})\)', b.get("key", ""))
            if m:
                n = b.get("doc_count", 0)
                by_cik[m.group(1)] = by_cik.get(m.group(1), 0) + n
                total += n
        if by_cik and total >= 5:
            best_cik, best_n = max(by_cik.items(), key=lambda kv: kv[1])
            if best_n / total >= 0.5:
                return best_cik
    except Exception:
        pass
    return None

async def get_submissions(client: httpx.AsyncClient, cik: str) -> Optional[dict]:
    """Fetch EDGAR submissions JSON — full filing history."""
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    try:
        r = await client.get(url, headers=DATA_UA, timeout=20)
        return r.json()
    except Exception:
        return None

def detect_fund_type(submissions: dict) -> str:
    forms = submissions.get("filings", {}).get("recent", {}).get("form", [])
    return "interval_fund" if "NPORT-P" in forms else "bdc"

def get_fund_name(submissions: dict) -> str:
    return submissions.get("name", "Unknown Fund")

def get_recent_nport_filings(submissions: dict, n: int = 6) -> list:
    """Return last N public NPORT-P filings, oldest first.

    NPORT-P is public quarterly, but many interval funds have fiscal quarters
    ending in Jan/Apr/Jul/Oct or Feb/May/Aug/Nov rather than calendar quarter
    ends. Some series trusts also file multiple NPORT-P accessions for the same
    report date; keep one accession per period to avoid duplicate quarters.
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms   = recent.get("form", [])
    accs    = recent.get("accessionNumber", [])
    dates   = recent.get("filingDate", [])
    periods = recent.get("reportDate", [])

    results = []
    seen_periods = set()
    for i, form in enumerate(forms):
        if form == "NPORT-P" and i < len(accs):
            period = periods[i] if i < len(periods) else (dates[i] if i < len(dates) else "")
            if period and period not in seen_periods:
                results.append({"acc": accs[i], "period": period})
                seen_periods.add(period)

    # Submissions API returns newest-first; reverse and take last N
    results.reverse()  # now oldest-first
    return results[-n:]  # take the N most recent (still oldest-first)

def get_recent_bdc_filings(submissions: dict, n: int = 6) -> list:
    """Return last N 10-K/10-Q filings, oldest first."""
    recent = submissions.get("filings", {}).get("recent", {})
    forms   = recent.get("form", [])
    accs    = recent.get("accessionNumber", [])
    periods = recent.get("reportDate", [])

    results = []
    for i, form in enumerate(forms):
        if form in ("10-K", "10-Q") and i < len(accs):
            period = periods[i] if i < len(periods) else ""
            results.append({"form": form, "acc": accs[i], "period": period})

    # Submissions newest-first; we want oldest-first
    results.reverse()
    return results[-n:]

# ─── NPORT-P PARSER ────────────────────────────────────────────────────────────

async def _get_retry(client: httpx.AsyncClient, url: str, timeout: float,
                     attempts: int = 3) -> Optional[httpx.Response]:
    """GET with retries — SEC throttles bursts of concurrent large downloads."""
    for i in range(attempts):
        try:
            r = await client.get(url, headers=EDGAR_UA, timeout=timeout)
            if r.status_code == 200:
                return r
        except Exception:
            pass
        if i < attempts - 1:
            await asyncio.sleep(1.5 * (i + 1))
    return None

async def fetch_nport_xml(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, cik: str, acc: str
) -> str:
    cik_plain = str(int(cik))
    acc_path  = acc.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_plain}/{acc_path}/primary_doc.xml"
    async with sem:
        r = await _get_retry(client, url, timeout=60)
        return r.text if r else ""

def parse_nport_xml(xml_text: str) -> list:
    if not xml_text:
        return []

    # Strip all XML namespaces so ElementTree can parse cleanly
    xml_clean = re.sub(r'\s+xmlns(?::\w+)?="[^"]+"', '', xml_text)
    # Prefixed attributes (e.g. xsi:schemaLocation) would leave an undefined
    # prefix behind and make ElementTree reject the whole document
    xml_clean  = re.sub(r'\s+\w+:\w+="[^"]*"', '', xml_clean)
    xml_clean  = re.sub(r'<(\w+):(\w+)', r'<\2', xml_clean)
    xml_clean  = re.sub(r'</(\w+):(\w+)', r'</\2', xml_clean)

    try:
        root = ET.fromstring(xml_clean)
    except ET.ParseError:
        return []

    investments = []
    for inv in root.iter('invstOrSec'):
        name_el   = inv.find('name')
        units_el  = inv.find('units')
        bal_el    = inv.find('balance')
        val_el    = inv.find('valUSD')
        cat_el    = inv.find('assetCat')
        fxcond_el = inv.find('currencyConditional')
        cur_el    = inv.find('curCd')

        if name_el is None or bal_el is None or val_el is None:
            continue

        name  = re.sub(r'\s+', ' ', (name_el.text or '').strip()).rstrip('/')
        units = (units_el.text or '').strip() if units_el is not None else ''
        cat   = (cat_el.text or 'OTH').strip() if cat_el is not None else 'OTH'
        itype = ASSET_CAT_MAP.get(cat, cat)

        try:
            balance = float(bal_el.text)
            val_usd = float(val_el.text)
        except (TypeError, ValueError):
            continue

        # Only principal-amount (debt) instruments
        if units != 'PA':
            continue
        # Skip liabilities (fund's own borrowings)
        if balance <= 0 or val_usd <= 0 or val_usd < 1000:
            continue

        # Determine currency and compute mark
        if fxcond_el is not None:
            cur = fxcond_el.get('curCd', 'FX')
            try:
                exchange_rt = float(fxcond_el.get('exchangeRt', '1'))
            except (TypeError, ValueError):
                exchange_rt = 1.0
            balance_usd = balance / exchange_rt if exchange_rt else balance
            mark = val_usd / balance_usd * 100 if balance_usd > 0 else None
        else:
            cur  = (cur_el.text or 'USD').strip() if cur_el is not None else 'USD'
            mark = val_usd / balance * 100

        if mark is None or mark <= 0 or mark > 200:
            continue

        investments.append({
            "name":     name,
            "type":     itype,
            "currency": cur,
            "par":      round(balance / 1000, 2),
            "fv":       round(val_usd / 1000, 2),
            "mark":     round(mark, 2),
        })

    return investments

# ─── BDC 10-K/10-Q PARSER ──────────────────────────────────────────────────────

def _plausible_portfolio(invs: list) -> bool:
    """Sanity check for a parsed schedule: enough positions and most fair
    value marked near par. A garbled column mapping scatters the marks."""
    if len(invs) < 25:
        return False
    total = sum(i["fv"] for i in invs)
    if total <= 0:
        return False
    near_par = sum(i["fv"] for i in invs if 85 <= i["mark"] <= 110)
    return near_par / total >= 0.6


def _has_bdc_aggregate_parse_rows(invs: list) -> bool:
    """Detect SOI parses that accidentally captured rollup rows as positions."""
    aggregate_patterns = (
        r'^investments?\s*(?:\(|$)',
        r'^investments?\s+(?:before|after|in)\b',
        r'^investment\s+fund\s+after\b',
        r'^cash\s*&\s*cash\s+equivalents\b',
        r'^total\s+(?:assets|investments|portfolio)\b',
    )
    for inv in invs[:25]:
        name = re.sub(r'\s+', ' ', inv.get("name", "")).strip().lower()
        if any(re.search(p, name, re.IGNORECASE) for p in aggregate_patterns):
            return True
    return False


IXBRL_BDC_INVESTMENT_TAGS = {
    "fv": "us-gaap:InvestmentOwnedAtFairValue",
    "cost": "us-gaap:InvestmentOwnedAtCost",
    "par": "us-gaap:InvestmentOwnedBalancePrincipalAmount",
    "shares": "us-gaap:InvestmentOwnedBalanceShares",
}

IXBRL_BDC_INVESTMENT_TAG_TO_KEY = {v: k for k, v in IXBRL_BDC_INVESTMENT_TAGS.items()}

IXBRL_SECURITY_DESCRIPTOR_RE = re.compile(
    r'^(?:senior secured|senior unsecured|subordinated|unsecured|term loan|'
    r'revolving|delayed draw|first lien|second lien|1st lien|2nd lien|'
    r'common stock|preferred stock|warrant|membership interest|equity|loan|note|other)\b',
    re.IGNORECASE,
)

IXBRL_SECURITY_DESCRIPTOR_ANY_RE = re.compile(
    r'\b(?:senior secured|senior unsecured|subordinated|unsecured|term loan|'
    r'revolving|delayed draw|first lien|second lien|1st lien|2nd lien|'
    r'common stock|preferred stock|warrant|membership interest|equity|loan|note|other)\b',
    re.IGNORECASE,
)


def _parse_bdc_ixbrl_investment_domain(domain: str) -> dict:
    """Parse InvestmentIdentifierAxis text into a borrower name and type.

    HTGC-style schedules expose investment rows as inline-XBRL facts instead
    of ordinary row-oriented tables. The typed member usually looks like:
    "Debt Investments, Industry, Borrower, Senior Secured, May 2028, ...".
    Borrower names can contain commas (", Inc."), so collect name fragments
    until a security descriptor starts.
    """
    raw = re.sub(r'\s+', ' ', domain or '').strip()
    raw = re.sub(r'^Investment, Identifier \[Domain\]:\s*', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\s*\(\d+\)\s*$', '', raw).strip()

    if re.search(r'\s+and\s+', raw, re.IGNORECASE):
        prefix, after_and = re.split(r'\s+and\s+', raw, maxsplit=1, flags=re.IGNORECASE)
        name_parts = []
        rest = []
        for part in [p.strip() for p in after_and.split(',') if p.strip()]:
            descriptor_m = IXBRL_SECURITY_DESCRIPTOR_ANY_RE.search(part)
            if descriptor_m:
                borrower_part = part[:descriptor_m.start()].strip()
                if borrower_part:
                    name_parts.append(borrower_part)
                rest = [part[descriptor_m.start():].strip()]
                break
            if re.search(r'\bmaturity\s+date\b', part, re.IGNORECASE):
                rest = [part]
                break
            name_parts.append(part)
        category_m = re.match(r'^((?:debt|equity|warrant|preferred|common|investment fund)\s+investments?)\b',
                              prefix, re.IGNORECASE)
        category = category_m.group(1) if category_m else prefix.strip()
        borrower = ', '.join(name_parts).strip() or after_and.strip()
        borrower = re.sub(r'\s+and$', '', borrower, flags=re.IGNORECASE).strip()
        itype = ', '.join([category] + rest[:2]) if rest else category
        parts = [p.strip() for p in raw.split(',') if p.strip()]
        return {"name": borrower, "type": itype, "raw": raw, "parts": parts}

    parts = [p.strip() for p in raw.split(',') if p.strip()]
    while parts and re.match(r'^\(\d+\)$', parts[-1]):
        parts.pop()

    category = parts[0] if parts else "Investment"
    start = 2 if len(parts) >= 3 and re.search(r'investments?|warrants?|debt|equity|preferred|common',
                                                category, re.IGNORECASE) else 0
    if len(parts) > start + 1 and parts[start].lower() == "other":
        start += 1

    name_parts = []
    rest = []
    for idx in range(start, len(parts)):
        part = parts[idx]
        if IXBRL_SECURITY_DESCRIPTOR_RE.search(part):
            rest = parts[idx:]
            break
        name_parts.append(part)

    borrower = ', '.join(name_parts).strip()
    borrower = re.sub(r'\s+and$', '', borrower, flags=re.IGNORECASE).strip()
    if not borrower and len(parts) > start:
        borrower = parts[start]
    if not borrower:
        borrower = raw or "Unknown"

    itype = ', '.join([category] + rest[:2]) if rest else category
    return {"name": borrower, "type": itype, "raw": raw, "parts": parts}


def _is_bdc_ixbrl_aggregate_domain(parsed: dict) -> bool:
    raw = re.sub(r'\s+', ' ', parsed.get("raw", "")).strip()
    name = re.sub(r'\s+', ' ', parsed.get("name", "")).strip()
    if not raw:
        return True
    if re.search(r'\b(?:total|subtotal)\b', raw, re.IGNORECASE):
        return True
    if re.match(r'^(?:investments?|investment fund)(?:\s|\(|$)', name, re.IGNORECASE):
        return True
    if re.match(r'^(?:debt|equity|warrant)\s+investments?\b', name, re.IGNORECASE):
        return True
    if name.lower() in {"senior secured", "senior unsecured", "unsecured", "subordinated", "llc", "llc.", "inc", "inc."}:
        return True
    if re.search(r'\([\d.]+\s*%\)\s*$', raw) and not IXBRL_SECURITY_DESCRIPTOR_RE.search(raw):
        return True
    return False


def _ixbrl_fact_number(tag) -> Optional[float]:
    return _parse_ixbrl_number(tag)


def _extract_bdc_investments_from_ixbrl_facts(html_text: str, period: str) -> list:
    """Extract BDC investments from inline-XBRL investment facts.

    Some BDCs, including HTGC, publish Schedule of Investments R-files as
    dimensional XBRL facts rather than normal borrower rows. We group facts by
    context, require the filing-period InvestmentIdentifierAxis, and exclude
    contexts that are subtotal/total rows embedded in the schedule.
    """
    if "InvestmentOwnedAtFairValue" not in html_text or "InvestmentIdentifierAxis" not in html_text:
        return []

    soup = BeautifulSoup(html_text, "lxml")
    contexts = {}
    for ctx in soup.find_all(re.compile(r'(?:^|:)context$', re.IGNORECASE)):
        cid = ctx.get("id")
        if not cid:
            continue
        instant = ctx.find(re.compile(r'(?:^|:)instant$', re.IGNORECASE))
        end_date = ctx.find(re.compile(r'(?:^|:)enddate$', re.IGNORECASE))
        ctx_period = (instant or end_date).get_text(strip=True) if (instant or end_date) else None
        if ctx_period != period:
            continue

        investment_domain = None
        for member in ctx.find_all(re.compile(r'(?:typedmember|typedMember)$', re.IGNORECASE)):
            if "InvestmentIdentifierAxis" in (member.get("dimension") or ""):
                investment_domain = member.get_text(" ", strip=True)
                break
        if investment_domain:
            contexts[cid] = investment_domain

    if not contexts:
        return []

    facts = defaultdict(dict)
    for tag in soup.find_all(re.compile(r'(?:nonfraction|nonnumeric)$', re.IGNORECASE)):
        fact_name = tag.get("name")
        key = IXBRL_BDC_INVESTMENT_TAG_TO_KEY.get(fact_name)
        if not key:
            continue
        context_ref = tag.get("contextref") or tag.get("contextRef")
        if context_ref not in contexts:
            continue
        val = _ixbrl_fact_number(tag)
        if val is None:
            continue
        # Convert inline-XBRL dollars to the app's BDC convention: $K.
        val_k = val / 1000.0
        if key not in facts[context_ref] or abs(val_k) > abs(facts[context_ref][key]):
            facts[context_ref][key] = val_k

    investments = []
    for context_ref, vals in facts.items():
        fv = vals.get("fv")
        if fv is None or fv <= 0:
            continue

        parsed = _parse_bdc_ixbrl_investment_domain(contexts[context_ref])
        parts = parsed["parts"]
        if (_is_bdc_ixbrl_aggregate_domain(parsed) or
                re.match(r'^(?:total|subtotal)\b', parsed["name"], re.IGNORECASE) or
                any(re.match(r'^(?:total|subtotal)\b', p, re.IGNORECASE) for p in parts)):
            continue

        par = vals.get("par")
        cost = vals.get("cost")
        denom = par if (par and par > 0) else cost if (cost and cost > 0) else fv
        if denom <= 0:
            continue
        mark = fv / denom * 100
        if mark <= 0 or mark > 20000:
            continue

        investments.append({
            "name":     parsed["name"],
            "type":     parsed["type"],
            "currency": "USD",
            "par":      round(denom, 2),
            "fv":       round(fv, 2),
            "mark":     round(mark, 2),
        })

    return investments


def _parse_ixbrl_number(tag) -> Optional[float]:
    txt = tag.get_text("", strip=True)
    txt = txt.replace("\u2014", "").replace("\u2013", "").replace("—", "").replace("–", "")
    txt = txt.replace("$", "").replace(",", "").strip()
    if not txt:
        return None
    negative = False
    if txt.startswith("(") and txt.endswith(")"):
        negative = True
        txt = txt[1:-1]
    try:
        value = float(txt)
    except ValueError:
        return None
    if tag.get("sign") == "-":
        negative = not negative
    if negative:
        value = -value
    try:
        scale = int(tag.get("scale", "0"))
    except (TypeError, ValueError):
        scale = 0
    return value * (10 ** scale)


def _context_periods(soup: BeautifulSoup) -> dict:
    contexts = {}
    for ctx in soup.find_all(lambda tag: tag.name and tag.name.lower().endswith("context")):
        cid = ctx.get("id")
        if not cid:
            continue
        start = ctx.find(lambda tag: tag.name and tag.name.lower().endswith("startdate"))
        end = ctx.find(lambda tag: tag.name and tag.name.lower().endswith("enddate"))
        instant = ctx.find(lambda tag: tag.name and tag.name.lower().endswith("instant"))
        has_segment = ctx.find(lambda tag: tag.name and "segment" in tag.name.lower()) is not None
        if start and end:
            contexts[cid] = {
                "start": start.get_text(strip=True),
                "end": end.get_text(strip=True),
                "instant": None,
                "has_segment": has_segment,
            }
        elif instant:
            contexts[cid] = {
                "start": None,
                "end": None,
                "instant": instant.get_text(strip=True),
                "has_segment": has_segment,
            }
    return contexts


def _duration_days(start: str, end: str) -> Optional[int]:
    try:
        return (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days + 1
    except Exception:
        return None


def _quarter_label_long(period: str) -> str:
    try:
        dt = datetime.strptime(period, "%Y-%m-%d")
        q = (dt.month - 1) // 3 + 1
        return f"Q{q} {dt.year}"
    except Exception:
        return period


def _compact_dollars(value: float) -> str:
    value = float(value or 0)
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1_000_000_000:
        return f"{sign}${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"{sign}${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{sign}${v / 1_000:.1f}K"
    return f"{sign}${v:.0f}"


def _best_ixbrl_metric(soup, contexts: dict, period: str, tag_list: list) -> Optional[dict]:
    """Best fund-level value for any tag in tag_list with a duration context
    ending at the filing period. Prefers earlier-ranked tags, contexts without
    segments (fund-level rather than per-industry), then longer durations
    (YTD values, which the quarterly de-cumulation logic expects)."""
    period_year = period[:4] if period else ""
    candidates = []
    for tag_name in tag_list:
        for tag in soup.find_all(attrs={"name": tag_name}):
            value = _parse_ixbrl_number(tag)
            if value is None:
                continue
            cid = tag.get("contextref") or tag.get("contextRef")
            ctx = contexts.get(cid)
            if not ctx or ctx.get("end") != period or not ctx.get("start"):
                continue
            if period_year and not ctx["start"].startswith(period_year):
                continue
            days = _duration_days(ctx["start"], ctx["end"])
            if not days or days < 25 or days > 370:
                continue
            try:
                tag_rank = tag_list.index(tag_name)
            except ValueError:
                tag_rank = 99
            candidates.append((
                tag_rank,
                1 if ctx.get("has_segment") else 0,
                -days,
                {"value": value, "start": ctx["start"], "tag": tag_name},
            ))
    if not candidates:
        return None
    return sorted(candidates, key=lambda c: c[:3])[0][3]


def _best_ixbrl_instant(soup, contexts: dict, period: str, tag_list: list) -> Optional[float]:
    """Best fund-level instant value at the period end for any tag in tag_list.
    Prefers earlier-ranked tags and contexts without segments (whole-fund
    balance-sheet values rather than share-class or rollforward columns)."""
    candidates = []
    for tag_name in tag_list:
        for tag in soup.find_all(attrs={"name": tag_name}):
            value = _parse_ixbrl_number(tag)
            if value is None:
                continue
            cid = tag.get("contextref") or tag.get("contextRef")
            ctx = contexts.get(cid)
            if not ctx or ctx.get("instant") != period:
                continue
            try:
                tag_rank = tag_list.index(tag_name)
            except ValueError:
                tag_rank = 99
            candidates.append((tag_rank, 1 if ctx.get("has_segment") else 0, value))
    if not candidates:
        return None
    return sorted(candidates, key=lambda c: c[:2])[0][2]


def parse_nport_net_assets(xml_text: str) -> Optional[float]:
    """Fund-level net assets from an NPORT-P primary_doc.xml."""
    if not xml_text:
        return None
    m = re.search(r'<netAssets>\s*(-?[\d.]+)\s*</netAssets>', xml_text)
    if not m:
        return None
    try:
        value = float(m.group(1))
        return value if value > 0 else None
    except ValueError:
        return None


# NPORT-P Part B Item B.4: amounts payable for borrowings, split by tenor
# (within / after one year) and counterparty class.
NPORT_BORROWING_TAGS = [
    "amtPayOneYrBanksBorr", "amtPayOneYrCtrldComp",
    "amtPayOneYrOthAffil",  "amtPayOneYrOther",
    "amtPayAftOneYrBanksBorr", "amtPayAftOneYrCtrldComp",
    "amtPayAftOneYrOthAffil",  "amtPayAftOneYrOther",
]


def _nport_float(xml_text: str, tag: str) -> Optional[float]:
    m = re.search(rf'<{tag}>\s*(-?[\d.]+)\s*</{tag}>', xml_text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def parse_nport_fund_level(xml_text: str) -> dict:
    """Fund-level balance sheet items from an NPORT-P primary_doc.xml.

    Borrowings come from Part B Item B.4 when tagged; otherwise total
    liabilities serve as a conservative proxy (flagged, since payables for
    securities purchased etc. are not senior-securities debt).
    """
    out = {"netAssets": None, "totAssets": None, "totLiabs": None,
           "borrowings": None, "borrowingsIsProxy": False}
    if not xml_text:
        return out
    out["netAssets"] = _nport_float(xml_text, "netAssets")
    out["totAssets"] = _nport_float(xml_text, "totAssets")
    out["totLiabs"]  = _nport_float(xml_text, "totLiabs")

    parts = [_nport_float(xml_text, t) for t in NPORT_BORROWING_TAGS]
    present = [p for p in parts if p is not None]
    if present:
        total = sum(present)
        if total > 0:
            out["borrowings"] = total
            return out
    if out["totLiabs"] and out["totLiabs"] > 0:
        out["borrowings"] = out["totLiabs"]
        out["borrowingsIsProxy"] = True
    return out


def extract_realized_loss_metric(html_text: str, period: str, source: str) -> dict:
    """Extract filing-level realized and unrealized gain/loss metrics from
    inline XBRL (the statement of operations).

    The SEC tags represent net signed values. Quarterly display logic later
    de-cumulates YTD figures and converts negative realized values into
    positive realized-loss bars; the unrealized net change stays signed.
    """
    result = {
        "periodEnd": period,
        "quarter": _quarter_label_long(period),
        "periodStart": None,
        "originalValue": None,
        "metricType": "unavailable",
        "source": source,
        "sourceTag": None,
        "dataAvailable": False,
        "unrealizedValue": None,
        "unrealizedStart": None,
        "unrealizedTag": None,
        "netAssets": None,
        "totalDebt": None,
    }
    if not html_text:
        return result

    soup = BeautifulSoup(html_text, "lxml")
    contexts = _context_periods(soup)

    net_assets = _best_ixbrl_instant(soup, contexts, period, NET_ASSETS_TAGS)
    if net_assets and net_assets > 0:
        result["netAssets"] = net_assets

    total_debt = _best_ixbrl_instant(soup, contexts, period, TOTAL_DEBT_TAGS)
    if total_debt and total_debt > 0:
        result["totalDebt"] = total_debt

    best_r = _best_ixbrl_metric(soup, contexts, period, NET_REALIZED_LOSS_TAGS)
    if best_r:
        result.update({
            "periodStart":   best_r["start"],
            "originalValue": best_r["value"],
            "metricType":    "net_realized_loss",
            "sourceTag":     best_r["tag"],
            "dataAvailable": True,
        })

    best_u = _best_ixbrl_metric(soup, contexts, period, NET_UNREALIZED_TAGS)
    if best_u:
        result["unrealizedValue"] = best_u["value"]
        result["unrealizedStart"] = best_u["start"]
        result["unrealizedTag"]   = best_u["tag"]

    return result


def calculate_quarterly_realized_losses(filings_or_metrics: list) -> list:
    """Normalize filing-level realized gain/loss values into quarterly losses.

    BDC statements often tag cumulative year-to-date net realized gain/loss:
    Q1 is used as reported, Q2 equals six-month YTD minus Q1, Q3 equals
    nine-month YTD minus six-month YTD, and Q4 equals annual value minus the
    prior three quarterized amounts. Direct single-quarter values also pass
    through unchanged. The signed value is preserved in `originalValue`, while
    `realizedLoss` is displayed as a positive dollar amount only when the
    signed value represents a loss.
    """
    rows = []
    by_year = defaultdict(list)
    for metric in filings_or_metrics:
        end = metric.get("periodEnd")
        row = {
            "quarter": metric.get("quarter") or _quarter_label_long(end or ""),
            "periodEnd": end,
            "realizedLoss": None,
            "originalValue": metric.get("originalValue"),
            "metricType": metric.get("metricType", "unavailable"),
            "source": metric.get("source"),
            "sourceTag": metric.get("sourceTag"),
            "dataAvailable": bool(metric.get("dataAvailable") and metric.get("originalValue") is not None),
            "unrealizedChange": None,
            "unrealizedAvailable": metric.get("unrealizedValue") is not None,
        }
        if row["dataAvailable"]:
            start = metric.get("periodStart")
            row["_periodStart"] = start
            row["_durationDays"] = _duration_days(start, end) if start and end else None
        if row["unrealizedAvailable"]:
            u_start = metric.get("unrealizedStart")
            row["_uvalue"] = metric.get("unrealizedValue")
            row["_udays"] = _duration_days(u_start, end) if u_start and end else None
        if row["dataAvailable"] or row["unrealizedAvailable"]:
            try:
                row["_year"] = datetime.strptime(end, "%Y-%m-%d").year
            except Exception:
                row["_year"] = None
            if row["_year"] is not None:
                by_year[row["_year"]].append(row)
        rows.append(row)

    for year_rows in by_year.values():
        year_rows.sort(key=lambda r: r.get("periodEnd") or "")
        prior_ytd = None      # realized YTD chain
        prior_u_ytd = None    # unrealized YTD chain
        for row in year_rows:
            try:
                qnum = (datetime.strptime(row.get("periodEnd"), "%Y-%m-%d").month - 1) // 3 + 1
            except Exception:
                qnum = 1

            if row["dataAvailable"]:
                value = row["originalValue"]
                days = row.get("_durationDays") or 0
                if days > 110:
                    if prior_ytd is None and qnum > 1:
                        row["dataAvailable"] = False
                        row["originalValue"] = None
                        row["realizedLoss"] = None
                        prior_ytd = value   # YTD baseline still anchors later quarters
                    else:
                        quarter_value = value - (prior_ytd or 0.0)
                        prior_ytd = value
                        row["originalValue"] = round(quarter_value, 2)
                        row["realizedLoss"] = round(abs(quarter_value) if quarter_value < 0 else 0.0, 2)
                else:
                    quarter_value = value
                    prior_ytd = (prior_ytd or 0.0) + quarter_value
                    row["originalValue"] = round(quarter_value, 2)
                    row["realizedLoss"] = round(abs(quarter_value) if quarter_value < 0 else 0.0, 2)

            if row["unrealizedAvailable"]:
                uvalue = row.get("_uvalue")
                udays = row.get("_udays") or 0
                if udays > 110:
                    if prior_u_ytd is None and qnum > 1:
                        row["unrealizedAvailable"] = False
                        row["unrealizedChange"] = None
                        prior_u_ytd = uvalue   # YTD baseline still anchors later quarters
                    else:
                        u_quarter = uvalue - (prior_u_ytd or 0.0)
                        prior_u_ytd = uvalue
                        row["unrealizedChange"] = round(u_quarter, 2)
                else:
                    prior_u_ytd = (prior_u_ytd or 0.0) + uvalue
                    row["unrealizedChange"] = round(uvalue, 2)

    for row in rows:
        for k in ("_periodStart", "_durationDays", "_year", "_uvalue", "_udays"):
            row.pop(k, None)
        if row["dataAvailable"] and row["realizedLoss"] is None:
            value = row["originalValue"]
            row["realizedLoss"] = round(abs(value) if value < 0 else 0.0, 2)

    return rows


calculateQuarterlyRealizedLosses = calculate_quarterly_realized_losses


def generate_realized_loss_insight(rows: list) -> str:
    available = [r for r in rows if r.get("dataAvailable")]
    missing = len(rows) - len(available)
    if not available:
        return "Realized loss data was not available in the parsed SEC filings for these quarters."

    peak = max(available, key=lambda r: r.get("realizedLoss") or 0)
    recent = available[-3:]
    trend = "flat"
    if len(recent) >= 2:
        first = recent[0].get("realizedLoss") or 0
        last = recent[-1].get("realizedLoss") or 0
        threshold = max(1_000_000, abs(first) * 0.1)
        if last - first > threshold:
            trend = "increased"
        elif first - last > threshold:
            trend = "declined"

    if trend == "increased":
        trend_text = "Recent realized losses have increased across the latest reported quarters."
    elif trend == "declined":
        trend_text = "Recent realized losses have declined across the latest reported quarters."
    else:
        trend_text = "Recent realized losses appear broadly flat across the latest reported quarters."

    metric_label = "gross realized losses" if any(r.get("metricType") == "gross_realized_loss" for r in available) else "net realized losses"
    incomplete = " Data is incomplete for one or more quarters." if missing else ""

    latest_u = next((r.get("unrealizedChange") for r in reversed(rows)
                     if r.get("unrealizedAvailable")), None)
    unreal_text = ""
    if latest_u is not None:
        direction = "appreciation" if latest_u >= 0 else "depreciation"
        unreal_text = (f" Latest quarter net change in unrealized {direction}: "
                       f"{_compact_dollars(latest_u)}.")

    return (
        f"Realized losses peaked in {peak.get('quarter')} at {_compact_dollars(peak.get('realizedLoss') or 0)}. "
        f"{trend_text} Metric shown: {metric_label}.{unreal_text}{incomplete}"
    )


async def fetch_bdc_filing(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, cik: str, acc: str, period: str
) -> list:
    cik_plain = str(int(cik))
    acc_path  = acc.replace("-", "")
    dir_url   = f"https://www.sec.gov/Archives/edgar/data/{cik_plain}/{acc_path}/"

    async with sem:
        investments = []

        # ── Method 1: FilingSummary.xml → XBRL R-file (fast path) ────────────
        # Some R-files are "Not available" stubs; others are huge two-period
        # pivot tables without maturity dates. Judge by extraction output, not
        # by content heuristics — fall through to Method 2 if nothing parses.
        try:
            fs_r = await _get_retry(client, dir_url + "FilingSummary.xml", timeout=25)
            if fs_r is not None:
                fs_soup = BeautifulSoup(fs_r.text, "lxml")
                for rep in fs_soup.find_all("report"):
                    sn = rep.find("shortname") or rep.find("longname")
                    fn_el = rep.find("htmlfilename")
                    if sn and fn_el and re.search(r'schedules?\s+of\s+investments',
                                                  sn.get_text(), re.IGNORECASE):
                        fname = fn_el.get_text().strip()
                        if fname:
                            rr = await _get_retry(client, dir_url + fname, timeout=30)
                            if rr is not None and len(rr.text) > 5000:
                                investments = _extract_bdc_investments(rr.text, period)
                            break
        except Exception:
            pass

        # ── Method 2: Filing index HTM → primary 10-Q/10-K document ──────────
        # Runs when Method 1 found nothing OR produced an implausible parse
        # (e.g. Blue Owl R-files yield garbled rows); keep the better result.
        if not _plausible_portfolio(investments):
            try:
                idx_r = await _get_retry(client, f"{dir_url}{acc}-index.htm", timeout=20)
                if idx_r is not None:
                    idx_soup = BeautifulSoup(idx_r.text, "lxml")
                    for row in idx_soup.find_all("tr"):
                        cells = [c.get_text().strip() for c in row.find_all(["td", "th"])]
                        if len(cells) > 3 and cells[3] in ("10-Q", "10-K"):
                            link = row.find("a", href=True)
                            if link:
                                fname = link["href"].rstrip("/").split("/")[-1]
                                if fname.lower().endswith(".htm"):
                                    doc_r = await _get_retry(client, dir_url + fname, timeout=120)
                                    if doc_r is not None:
                                        invs2 = _extract_bdc_investments(doc_r.text, period)
                                        # Prefer a plausible parse; row count breaks ties
                                        if ((_plausible_portfolio(invs2), len(invs2)) >
                                            (_plausible_portfolio(investments), len(investments))):
                                            investments = invs2
                                    break
            except Exception:
                pass

    return investments


async def fetch_bdc_realized_loss_metric(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, cik: str, acc: str, period: str, form: str
) -> dict:
    cik_plain = str(int(cik))
    acc_path  = acc.replace("-", "")
    dir_url   = f"https://www.sec.gov/Archives/edgar/data/{cik_plain}/{acc_path}/"

    async with sem:
        try:
            idx_r = await _get_retry(client, f"{dir_url}{acc}-index.htm", timeout=20)
            if idx_r is not None:
                idx_soup = BeautifulSoup(idx_r.text, "lxml")
                for row in idx_soup.find_all("tr"):
                    cells = [c.get_text().strip() for c in row.find_all(["td", "th"])]
                    if len(cells) > 3 and cells[3] in ("10-Q", "10-K"):
                        link = row.find("a", href=True)
                        if link:
                            fname = link["href"].rstrip("/").split("/")[-1]
                            if fname.lower().endswith(".htm"):
                                doc_r = await _get_retry(client, dir_url + fname, timeout=120)
                                if doc_r is not None:
                                    return extract_realized_loss_metric(doc_r.text, period, form)
                        break
        except Exception:
            pass

    return {
        "periodEnd": period,
        "quarter": _quarter_label_long(period),
        "periodStart": None,
        "originalValue": None,
        "metricType": "unavailable",
        "source": form,
        "sourceTag": None,
        "dataAvailable": False,
        "netAssets": None,
        "totalDebt": None,
    }


MONTH_NUM = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}

# Maturity dates appear as MM/DD/YYYY, MM/DD/YY (GSBD), MM/YYYY (ARCC), or
# month-name dates such as "July 1, 2026" in newer inline-XBRL filings.
MONTH_RE = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
DATE_CELL_RE = re.compile(
    r'^(?:\d{1,2}/(?:\d{1,2}/)?\d{2,4}|'
    + MONTH_RE + r'\s+\d{1,2},?\s+\d{2,4})(?:\s*\(\d+\))*$',
    re.IGNORECASE,
)
DATE_ANY_RE  = re.compile(
    r'(?:\d{1,2}/(?:\d{1,2}/)?\d{2,4}|'
    + MONTH_RE + r'\s+\d{1,2},?\s+\d{2,4})',
    re.IGNORECASE,
)

TYPE_KEYWORDS = [
    "first lien", "1st lien", "second lien", "2nd lien", "senior secured",
    "subordinated", "unsecured", "unitranche", "mezzanine", "revolving loan",
    "delayed draw", "term loan", "senior note",
]

CURRENCY_CODES = ("CAD", "GBP", "EUR", "AUD", "SEK", "NOK", "DKK", "CHF", "NZD", "JPY")


def _soi_headers(html_text: str) -> list:
    """All 'Schedule of Investments' headings as (position, (y, m, d) or None)."""
    hdr_re = re.compile(
        r'Schedules?(?:\s|&#160;|&nbsp;)+of(?:\s|&#160;|&nbsp;)+Investments', re.IGNORECASE)
    date_re = re.compile(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+(\d{1,2}),?\s+(\d{4})')
    headers = []
    for m in hdr_re.finditer(html_text):
        ctx = re.sub(r'<[^>]+>', ' ', html_text[m.end():m.end() + 15000])
        ctx = ctx.replace('&#160;', ' ').replace('&nbsp;', ' ')
        ctx = re.sub(r'\s+', ' ', ctx)
        dm = date_re.search(ctx)
        d = (int(dm.group(3)), MONTH_NUM[dm.group(1)], int(dm.group(2))) if dm else None
        headers.append((m.start(), d))
    return headers


def _soi_bounds(html_text: str, period: str) -> Optional[tuple]:
    """[start, end) of the SOI section for the filing period.

    Filings contain the current-period schedule followed by a prior-period one;
    only the current section may be parsed or quarters get cross-contaminated.
    TOC links also mention the schedule, so a real section start additionally
    requires maturity-date-like cells shortly after the heading.
    """
    target = None
    try:
        dt = datetime.strptime(period, "%Y-%m-%d")
        target = (dt.year, dt.month, dt.day)
    except Exception:
        pass

    headers = _soi_headers(html_text)
    if not headers:
        return None

    def is_real_section(pos):
        segment = html_text[pos:pos + 300000]
        table_m = re.search(r'<table[^>]*>', segment, re.IGNORECASE)
        if not table_m or table_m.start() > 20000:
            return False
        table_end = segment.find('</table>', table_m.start())
        if table_end == -1:
            return False
        first_table = segment[table_m.start():table_end + 8]
        plain = re.sub(r'<[^>]+>', ' ', first_table)
        plain = plain.replace('&#160;', ' ').replace('&nbsp;', ' ')
        plain = re.sub(r'\s+', ' ', plain)
        return (
            re.search(r'fair\s+value', plain, re.IGNORECASE)
            and DATE_ANY_RE.search(plain) is not None
        )

    start = None
    if target:
        for pos, d in headers:
            if d == target and is_real_section(pos):
                start = pos
                break
    if start is None:
        for pos, d in headers:
            if d is not None and is_real_section(pos):
                start, target = pos, d
                break
    if start is None:
        return None

    end = len(html_text)
    for pos, d in headers:
        if pos > start and d is not None and d != target:
            end = pos
            break
    return start, end


def _soi_candidate_bounds(html_text: str, period: str) -> list:
    """All plausible SOI sections for a filing period.

    Inline-XBRL filings often mention the schedule in the table of contents,
    notes, and continued page headers. Try each plausible section and let the
    parser choose the one that produces the strongest portfolio.
    """
    target = None
    try:
        dt = datetime.strptime(period, "%Y-%m-%d")
        target = (dt.year, dt.month, dt.day)
    except Exception:
        pass

    headers = _soi_headers(html_text)
    if not headers:
        return []

    def bounds_from(pos, d):
        end = len(html_text)
        for pos2, d2 in headers:
            if pos2 > pos and d2 is not None and d2 != d:
                end = pos2
                break
        return pos, end

    candidates = []
    for pos, d in headers:
        if target and d not in (target, None):
            continue
        candidates.append(bounds_from(pos, target if target else d))

    # Preserve order while dropping duplicate bounds.
    seen, unique = set(), []
    for b in candidates:
        if b not in seen:
            unique.append(b)
            seen.add(b)
    return unique


def _parse_soi_table(chunk: str, state: dict, mult: float) -> list:
    """Parse one SOI page table. `state` carries the current company and
    investment type across page-break tables (continuation rows omit both)."""
    try:
        tree = etree.HTML(chunk)
    except Exception:
        return []
    if tree is None:
        return []

    out = []
    for tr in tree.iter('tr'):
        cells = []
        for c in tr.iter():
            if c.tag in ('td', 'th'):
                txt = re.sub(r'\s+', ' ', ' '.join(c.itertext())).strip()
                if txt:
                    cells.append(txt)
        if not cells:
            continue

        row_text = " ".join(cells).lower()
        date_idxs = [i for i, c in enumerate(cells) if DATE_CELL_RE.match(c)]

        if not date_idxs:
            # Section headings like "1st Lien/Senior Secured Debt - 4.5 %"
            if len(cells) <= 4:
                for kw in TYPE_KEYWORDS:
                    if kw in row_text:
                        state["type"] = re.sub(
                            r'\s*[-–—]\s*[\d.]+\s*%\s*$', '', cells[0]).strip()
                        break
            continue

        # Column-header rows occasionally contain a date-like token
        if 'fair value' in row_text and 'maturity' in row_text:
            continue

        mat_idx = date_idxs[-1]   # acquisition date may precede maturity date
        if mat_idx < 1:
            continue

        first = cells[0]
        fl = first.lower()
        if any(x in fl for x in ('total', 'subtotal', 'schedule of investments', '% of net')):
            continue

        if any(kw in fl for kw in TYPE_KEYWORDS) and len(first) < 60:
            # Continuation row: another tranche of the previous company (ARCC style)
            name = state.get("company")
            itype = first
            if not name:
                continue
        else:
            name = re.sub(r'(\s*\(\d+\))+\s*$', '', first).strip()  # strip footnote refs
            if len(name) < 3:
                continue
            state["company"] = name
            itype = state.get("type", "Debt")
            for c in cells[1:mat_idx]:
                if len(c) < 60 and any(kw in c.lower() for kw in TYPE_KEYWORDS):
                    itype = c
                    break

        currency = "USD"
        for c in cells[:mat_idx + 3]:
            if c in CURRENCY_CODES:
                currency = c
                break

        post = cells[mat_idx + 1:]
        nums = []
        for j, p in enumerate(post):
            # Percent columns (e.g. "% of Net Assets": cells '0.4', '%') are
            # not dollar amounts — including them shifts par/cost/fv mapping
            if '%' in p:
                continue
            if j + 1 < len(post) and post[j + 1].strip() == '%':
                continue
            p_clean = (p.replace(",", "").replace("$", "")
                        .replace("(", "-").replace(")", "")
                        .strip())
            if not p_clean or p_clean in ("-", "—", "–"):
                continue
            try:
                nums.append(float(p_clean))
            except ValueError:
                pass

        if len(nums) < 2:
            continue

        par = cost = fv = None
        # Wide SEC schedule tables sometimes include a trailing "% of net
        # assets" value without a literal percent-sign cell. Do not blindly
        # take the last three numbers; instead choose the rightmost adjacent
        # par/cost/fair-value triple that produces a plausible mark.
        if len(nums) >= 3:
            for i in range(len(nums) - 3, -1, -1):
                cand_par, cand_cost, cand_fv = nums[i], nums[i + 1], nums[i + 2]
                if cand_fv < 10:
                    continue
                test_par = cand_par if cand_par >= 0.01 else cand_cost
                if test_par <= 0:
                    continue
                test_mark = cand_fv / test_par * 100
                if 0.5 <= test_mark <= 200:
                    par, cost, fv = cand_par, cand_cost, cand_fv
                    break
        if par is None:
            par, fv = nums[-2], nums[-1]
            cost = par

        # Normalize to $K regardless of the filing's reporting units
        par_k, cost_k, fv_k = par * mult, cost * mult, fv * mult
        if fv_k < 10:   # dust or negative (unfunded commitments)
            continue
        if par_k < 0.01:
            par_k = cost_k if cost_k > 0 else fv_k

        if currency == "USD" and par_k > 0:
            mark = fv_k / par_k * 100
        elif cost_k > 0:
            mark = fv_k / cost_k * 100
        else:
            continue

        if mark < 0.5 or mark > 200:
            continue

        out.append({
            "name":     name,
            "type":     itype,
            "currency": currency,
            "par":      round(par_k, 2),
            "fv":       round(fv_k, 2),
            "mark":     round(mark, 2),
        })

    return out


def _extract_bdc_investments_from_section(section: str) -> list:
    # Reporting units: ARCC uses millions, GSBD thousands
    units_ctx = re.sub(r'<[^>]+>', ' ', section[:200_000])
    um = re.search(r'in\s+(millions|thousands)', units_ctx, re.IGNORECASE)
    mult = 1000.0 if (um and um.group(1).lower() == 'millions') else 1.0

    # Parse table-by-table (each SOI page is its own table) to bound memory
    investments = []
    state = {"company": None, "type": "Debt"}
    for tm in re.finditer(r'<table[^>]*>', section, re.IGNORECASE):
        t_start = tm.start()
        t_end = section.find('</table>', t_start)
        if t_end == -1:
            break
        chunk = section[t_start:t_end + 8]
        if len(chunk) < 1500 or not DATE_ANY_RE.search(chunk):
            continue
        investments.extend(_parse_soi_table(chunk, state, mult))

    return investments


def _extract_bdc_investments(html_text: str, period: str) -> list:
    """Parse the Schedule of Investments from a BDC 10-K/10-Q."""
    if not html_text:
        return []

    best = []
    bounds = _soi_bounds(html_text, period)
    if bounds:
        best = _extract_bdc_investments_from_section(html_text[bounds[0]:bounds[1]])

    # Fallback: the fast section finder sometimes lands on a table of contents
    # or a note reference. Try a bounded number of candidate SOI headings and
    # keep the strongest parsed portfolio.
    tried = {bounds} if bounds else set()
    for start, end in _soi_candidate_bounds(html_text, period)[:30]:
        if (start, end) in tried:
            continue
        tried.add((start, end))
        invs = _extract_bdc_investments_from_section(html_text[start:end])
        # Prefer plausible schedules, then larger parsed fair value, then rows.
        if ((_plausible_portfolio(invs), sum(i["fv"] for i in invs), len(invs)) >
            (_plausible_portfolio(best), sum(i["fv"] for i in best), len(best))):
            best = invs

    fact_invs = _extract_bdc_investments_from_ixbrl_facts(html_text, period)
    if (_plausible_portfolio(fact_invs) and
            (_has_bdc_aggregate_parse_rows(best) or sum(i["fv"] for i in best) > sum(i["fv"] for i in fact_invs) * 1.5)):
        return fact_invs
    if ((_plausible_portfolio(fact_invs), sum(i["fv"] for i in fact_invs), len(fact_invs)) >
        (_plausible_portfolio(best), sum(i["fv"] for i in best), len(best))):
        return fact_invs

    if best:
        return best

    # Small doc (e.g. an XBRL R-file) — parse it all.
    if len(html_text) <= 3_000_000:
        return _extract_bdc_investments_from_section(html_text)
    return []

# ─── ANALYSIS ──────────────────────────────────────────────────────────────────

def _agg_borrowers(investments: list) -> dict:
    """FV-weighted average mark per normalized borrower name."""
    agg = defaultdict(lambda: {"fv": 0.0, "wmark": 0.0, "names": []})
    for inv in investments:
        norm = normalize_name(inv["name"])
        if not norm:
            continue
        fv   = inv.get("fv", 0) or 0
        mark = inv.get("mark")
        if fv <= 0 or mark is None:
            continue
        agg[norm]["fv"]    += fv
        agg[norm]["wmark"] += fv * mark
        agg[norm]["names"].append(inv["name"])

    result = {}
    for norm, d in agg.items():
        if d["fv"] > 0:
            result[norm] = {
                "fv":   d["fv"],
                "mark": d["wmark"] / d["fv"],
                "name": max(set(d["names"]), key=d["names"].count),
            }
    return result

def compute_buckets(all_data: dict, quarters: list) -> dict:
    bucket_data = {name: [] for name in BUCKET_NAMES}
    for qtr in quarters:
        counts = {name: 0.0 for name in BUCKET_NAMES}
        for inv in all_data.get(qtr, []):
            mark = inv.get("mark")
            fv   = inv.get("fv", 0) or 0
            if mark is None or fv <= 0:
                continue
            counts[BUCKET_NAMES[bucket_for(mark)]] += fv / 1000  # $K → $M
        for name in BUCKET_NAMES:
            bucket_data[name].append(round(counts[name], 1))
    return {"names": BUCKET_NAMES, "data": bucket_data}

def compute_rollrate(all_data: dict, quarters: list) -> dict:
    N = len(BUCKET_DEFS)
    period_labels   = []
    period_matrices = []
    avg_rates = {"forward_roll": [], "cure": [], "stay": [], "exit": []}

    for i in range(len(quarters) - 1):
        q0, q1 = quarters[i], quarters[i + 1]
        b0 = _agg_borrowers(all_data.get(q0, []))
        b1 = _agg_borrowers(all_data.get(q1, []))

        # (N+1) × (N+1) matrix; row N = new entries, col N = exits
        mat = [[0.0] * (N + 1) for _ in range(N + 1)]
        total_fv = fwd_fv = cure_fv = stay_fv = exit_fv = 0.0

        for norm in b0:
            fv = b0[norm]["fv"]
            total_fv += fv
            bi0 = bucket_for(b0[norm]["mark"])
            if norm in b1:
                bi1 = bucket_for(b1[norm]["mark"])
                mat[bi0][bi1] += (fv + b1[norm]["fv"]) / 2
                if bi1 < bi0:       # moved to worse (lower index) bucket
                    fwd_fv += fv
                elif bi1 > bi0:
                    cure_fv += fv
                else:
                    stay_fv += fv
            else:
                mat[bi0][N] += fv   # exit
                exit_fv += fv

        for norm in b1:
            if norm not in b0:
                bi1 = bucket_for(b1[norm]["mark"])
                mat[N][bi1] += b1[norm]["fv"]   # entry

        if total_fv > 0:
            avg_rates["forward_roll"].append(round(fwd_fv  / total_fv * 100, 1))
            avg_rates["cure"].append(        round(cure_fv / total_fv * 100, 1))
            avg_rates["stay"].append(        round(stay_fv / total_fv * 100, 1))
            avg_rates["exit"].append(        round(exit_fv / total_fv * 100, 1))
        else:
            for k in avg_rates:
                avg_rates[k].append(0.0)

        period_labels.append(f"{q0}→{q1}")
        period_matrices.append({
            "period": f"{q0}→{q1}",
            "matrix": [[round(v / 1000, 1) for v in row] for row in mat],  # $M
        })

    return {
        "bucket_names":    BUCKET_NAMES,
        "period_labels":   period_labels,
        "period_matrices": period_matrices,
        "avg_rates":       avg_rates,
    }

def compute_stress_positions(all_data: dict, quarters: list, min_fv_k: float = 100) -> list:
    latest_q = quarters[-1] if quarters else ""
    first_q  = quarters[0]  if quarters else ""

    by_norm = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, []]))
    for qtr in quarters:
        for inv in all_data.get(qtr, []):
            norm = normalize_name(inv["name"])
            if not norm:
                continue
            fv   = inv.get("fv", 0) or 0
            mark = inv.get("mark")
            if fv <= 0 or mark is None:
                continue
            by_norm[norm][qtr][0] += fv * mark
            by_norm[norm][qtr][1] += fv
            by_norm[norm][qtr][2].append(inv["name"])

    companies = []
    for norm, qtr_map in by_norm.items():
        marks = {}
        for qtr, (wsum, fv_total, _) in qtr_map.items():
            if fv_total > 0:
                marks[qtr] = round(wsum / fv_total, 1)

        latest_fv   = qtr_map.get(latest_q, [0, 0, []])[1]
        latest_mark = marks.get(latest_q)

        if latest_mark is None or latest_fv < min_fv_k or latest_mark >= 90:
            continue

        all_names = [n for _, _, ns in qtr_map.values() for n in ns]
        display   = max(set(all_names), key=all_names.count)

        first_found_q = next((q for q in quarters if q in marks), None)
        first_mark    = marks.get(first_found_q) if first_found_q else None
        chg = round((latest_mark - first_mark), 1) if first_mark is not None else 0.0

        section = ('new' if first_found_q != first_q
                   else 'migrated' if (first_mark is not None and first_mark >= 90)
                   else 'stressed')

        companies.append({
            "name":        display,
            "marks":       marks,
            "latest_fv_k": round(latest_fv),
            "latest_mark": latest_mark,
            "first_mark":  first_mark,
            "chg":         chg,
            "section":     section,
        })

    section_order = {"migrated": 0, "stressed": 1, "new": 2}
    companies.sort(key=lambda c: (section_order.get(c["section"], 3), c["latest_mark"]))
    return companies[:60]

def compute_position_scatter(all_data: dict, quarters: list, limit: int = 25) -> dict:
    latest_q = quarters[-1] if quarters else ""
    total_fv = sum((inv.get("fv", 0) or 0) for inv in all_data.get(latest_q, []))
    invs = []

    for inv in all_data.get(latest_q, []):
        fv = inv.get("fv", 0) or 0
        mark = inv.get("mark")
        par = inv.get("par")
        if fv <= 0 or mark is None:
            continue
        invs.append({
            "name": inv.get("name", "Unknown"),
            "type": inv.get("type", "Investment"),
            "currency": inv.get("currency", "USD"),
            "fv_k": round(fv, 2),
            "fv_m": round(fv / 1000, 2),
            "par_k": round(par, 2) if par is not None else None,
            # The parsed mark is fair value divided by par/principal, scaled to
            # 100. It is the closest available filing-derived proxy for dollar
            # price and avoids introducing external pricing data.
            "price": round(mark, 2),
            "portfolio_pct": round(fv / total_fv * 100, 2) if total_fv > 0 else None,
        })

    invs.sort(key=lambda i: i["fv_k"], reverse=True)

    return {
        "version": 2,
        "quarter": latest_q,
        "total_fv_m": round(total_fv / 1000, 1) if total_fv > 0 else None,
        "items": invs[:limit],
        "definition": (
            "Top positions are selected from the latest parsed SEC filing by fair value. "
            "Price is the parsed mark, calculated as fair value divided by par/principal "
            "and scaled to dollars per $100 par. Bubble size represents each position's "
            "share of the latest parsed portfolio fair value."
        ),
    }

def compute_summary(all_data: dict, quarters: list) -> dict:
    latest_q = quarters[-1] if quarters else ""
    invs = all_data.get(latest_q, [])
    total_fv    = sum(i.get("fv", 0) for i in invs)
    stressed_fv = sum(i.get("fv", 0) for i in invs if (i.get("mark") or 999) < 90)
    return {
        "total_fv_b":    round(total_fv / 1e6, 2),
        "n_positions":   len(invs),
        "stressed_fv_m": round(stressed_fv / 1000, 1),
        "stress_pct":    round(stressed_fv / total_fv * 100, 1) if total_fv > 0 else 0,
    }


# ─── LEVERAGE / ASSET COVERAGE ─────────────────────────────────────────────────

def compute_leverage(fund_type: str, rows: list, stress_pct: Optional[float] = None) -> dict:
    """Regulatory leverage and asset-coverage metrics with alert flags.

    `rows` is oldest-first: [{"quarter", "debt", "equity", "assets" (optional),
    "debtIsProxy" (optional)}]. Dollar values must share one unit.

    Asset coverage is approximated as (equity + debt) / debt — the standard
    1940 Act senior-securities computation ignoring non-debt liabilities. The
    statutory test is measured when debt is incurred; falling below it later
    blocks new borrowing and (for BDCs) distributions rather than triggering
    automatic default. Facility covenants typically bind sooner.
    """
    req = COVERAGE_REQUIREMENTS.get(fund_type, COVERAGE_REQUIREMENTS["bdc"])
    k = req["min_equity_per_debt"]

    items = []
    for row in rows:
        debt, equity = row.get("debt"), row.get("equity")
        item = {
            "quarter": row.get("quarter"),
            "debt": round(debt, 0) if debt else None,
            "equity": round(equity, 0) if equity else None,
            "assets": round(row["assets"], 0) if row.get("assets") else None,
            "debtIsProxy": bool(row.get("debtIsProxy")),
            "deRatio": None,
            "coveragePct": None,
            "cushionPct": None,
            "headroomDebt": None,
            "dataAvailable": False,
        }
        if debt and equity and debt > 0 and equity > 0:
            item["deRatio"] = round(debt / equity, 2)
            item["coveragePct"] = round((equity + debt) / debt * 100, 1)
            item["cushionPct"] = round(max(0.0, (equity - k * debt) / equity * 100), 1)
            item["headroomDebt"] = round(max(0.0, equity / k - debt), 0)
            item["dataAvailable"] = True
        items.append(item)

    avail = [i for i in items if i["dataAvailable"]]
    latest = avail[-1] if avail else None
    prior = avail[-2] if len(avail) >= 2 else None

    alerts = []
    if latest:
        cov = latest["coveragePct"]
        if cov < req["red_pct"]:
            alerts.append({"level": "red", "code": "coverage",
                           "message": f"Asset coverage of {cov}% is inside the red threshold "
                                      f"({req['red_pct']:.0f}%) versus the {req['limit_pct']:.0f}% regulatory minimum."})
        elif cov < req["amber_pct"]:
            alerts.append({"level": "amber", "code": "coverage",
                           "message": f"Asset coverage of {cov}% is below the {req['amber_pct']:.0f}% watch "
                                      f"threshold (regulatory minimum {req['limit_pct']:.0f}%)."})
        if prior and latest["deRatio"] > prior["deRatio"] and latest["equity"] < prior["equity"]:
            alerts.append({"level": "amber", "code": "leverage_creep",
                           "message": f"Leverage creep: debt/equity rose to {latest['deRatio']}x from "
                                      f"{prior['deRatio']}x while net assets declined — valuation-driven "
                                      f"leverage increase, the pattern that erodes coverage from both ends."})
        if len(avail) >= 3:
            d0, d1 = avail[0]["debt"], latest["debt"]
            a0 = avail[0]["assets"] or (avail[0]["equity"] + avail[0]["debt"])
            a1 = latest["assets"] or (latest["equity"] + latest["debt"])
            if d0 and a0 and d1 > d0 * 1.10:
                debt_g = d1 / d0 - 1
                asset_g = a1 / a0 - 1
                if debt_g > 2 * max(asset_g, 0.0):
                    alerts.append({"level": "amber", "code": "borrowing_outpacing",
                                   "message": f"Borrowings grew {debt_g * 100:.0f}% over the window versus "
                                              f"{asset_g * 100:.0f}% asset growth — leverage is funding a "
                                              f"growing share of the balance sheet."})
        if stress_pct is not None and stress_pct >= 5 and cov < req["amber_pct"]:
            alerts.append({"level": "red", "code": "stress_plus_coverage",
                           "message": f"Compound risk: {stress_pct}% of the portfolio is marked below 90 "
                                      f"while asset coverage is already below the watch threshold — further "
                                      f"markdowns feed directly into borrowing-base and coverage headroom."})
        if latest["debtIsProxy"]:
            alerts.append({"level": "info", "code": "debt_proxy",
                           "message": "Borrowings are proxied by total liabilities (NPORT Part B Item B.4 "
                                      "borrowing fields were not tagged), which overstates senior-securities "
                                      "debt and understates coverage."})

    if latest:
        cov = latest["coveragePct"]
        status = ("red" if any(a["level"] == "red" for a in alerts)
                  else "amber" if any(a["level"] == "amber" for a in alerts) else "green")
        insight = (
            f"Latest asset coverage is {cov}% against a {req['limit_pct']:.0f}% regulatory minimum "
            f"({latest['deRatio']}x debt/equity). Net assets could decline roughly "
            f"{latest['cushionPct']}% before the statutory test binds, holding debt constant."
        )
    else:
        status = "unavailable"
        insight = ("Leverage data was not available: the filings did not tag total debt "
                   "(BDC) or fund-level borrowings (NPORT-P) in a recognized format.")

    return {
        "version": 1,
        "fund_type": fund_type,
        "requirement": req,
        "items": items,
        "latest": latest,
        "status": status,
        "alerts": alerts,
        "insight": insight,
        "definition": (
            "Asset coverage is computed as (net assets + debt) / debt from filing-tagged "
            "balance-sheet values — the 1940 Act senior-securities approximation. BDCs "
            "electing reduced coverage must maintain 150% (≈2.0x debt/equity); closed-end "
            "interval funds must have 300% coverage (≈0.5x) when incurring borrowings. "
            "Breach blocks new borrowing and distributions and can force deleveraging. "
            "Bank-facility covenants (minimum shareholders' equity, borrowing bases with "
            "advance rates) typically bind before the statutory test."
        ),
    }


SCORE_WEIGHTS = {
    "delinquency": 25,
    "rollRates": 25,
    "lossesAndRecoveries": 20,
    "portfolioQuality": 15,
    "momentum": 15,
}


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _avg(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _score_category(score: Optional[float]) -> str:
    if score is None:
        return "Unavailable"
    if score >= 90:
        return "Exceptional"
    if score >= 80:
        return "Strong"
    if score >= 70:
        return "Stable"
    if score >= 60:
        return "Watch"
    if score >= 40:
        return "Weak"
    return "Distressed"


def _weighted_mark(invs: list) -> Optional[float]:
    total = sum((i.get("fv") or 0) for i in invs if i.get("mark") is not None)
    if total <= 0:
        return None
    return sum((i.get("fv") or 0) * i.get("mark") for i in invs
               if i.get("mark") is not None) / total


def _portfolio_metrics(invs: list) -> dict:
    total = sum((i.get("fv") or 0) for i in invs)
    if total <= 0:
        return {
            "total_fv": 0.0,
            "stress_pct": None,
            "severe_pct": None,
            "distressed_pct": None,
            "near_par_pct": None,
            "weighted_mark": None,
            "top_stressed_pct": None,
        }

    stressed = [i for i in invs if (i.get("mark") or 999) < 90 and (i.get("fv") or 0) > 0]
    severe = [i for i in invs if (i.get("mark") or 999) < 80 and (i.get("fv") or 0) > 0]
    distressed = [i for i in invs if (i.get("mark") or 999) < 70 and (i.get("fv") or 0) > 0]
    near_par = [i for i in invs if (i.get("mark") or 0) >= 90 and (i.get("fv") or 0) > 0]
    top_stressed = max(((i.get("fv") or 0) for i in stressed), default=0.0)
    return {
        "total_fv": total,
        "stress_pct": sum(i.get("fv") or 0 for i in stressed) / total * 100,
        "severe_pct": sum(i.get("fv") or 0 for i in severe) / total * 100,
        "distressed_pct": sum(i.get("fv") or 0 for i in distressed) / total * 100,
        "near_par_pct": sum(i.get("fv") or 0 for i in near_par) / total * 100,
        "weighted_mark": _weighted_mark(invs),
        "top_stressed_pct": top_stressed / total * 100,
    }


def _score_for_period(all_data: dict, quarters: list, idx: int, rollrate: dict,
                      realized_rows: list) -> dict:
    q = quarters[idx]
    metrics = _portfolio_metrics(all_data.get(q, []))
    total_dollars = metrics["total_fv"] * 1000
    stress = metrics["stress_pct"]
    severe = metrics["severe_pct"]
    distressed = metrics["distressed_pct"]
    near_par = metrics["near_par_pct"]
    weighted_mark = metrics["weighted_mark"]
    top_stressed = metrics["top_stressed_pct"]

    component_scores = {
        "delinquency": None,
        "rollRates": None,
        "lossesAndRecoveries": None,
        "portfolioQuality": None,
        "momentum": None,
    }
    observations = {}

    if stress is not None:
        component_scores["delinquency"] = round(_clamp(
            100 - stress * 3.0 - (severe or 0) * 4.5 - (distressed or 0) * 5.5
        ))
        observations.update({
            "stress_pct": round(stress, 1),
            "severe_pct": round(severe or 0, 1),
            "distressed_pct": round(distressed or 0, 1),
        })

    fwd = cure = stay = exit_rate = deterioration_ratio = None
    if idx > 0 and rollrate.get("avg_rates"):
        r_idx = idx - 1
        ar = rollrate["avg_rates"]
        try:
            fwd = ar["forward_roll"][r_idx]
            cure = ar["cure"][r_idx]
            stay = ar["stay"][r_idx]
            exit_rate = ar["exit"][r_idx]
        except Exception:
            fwd = cure = stay = exit_rate = None
        if fwd is not None and cure is not None:
            deterioration_ratio = round(fwd / cure, 1) if cure > 0 else None
            ratio_penalty = max(0.0, (deterioration_ratio or 0) - 1.0) * 7 if cure > 0 else 12
            component_scores["rollRates"] = round(_clamp(
                96 - fwd * 1.9 + cure * 1.1 + (stay or 0) * 0.05 -
                (exit_rate or 0) * 0.5 - ratio_penalty
            ))
            observations.update({
                "forward_roll": fwd,
                "cure": cure,
                "stay": stay,
                "exit": exit_rate,
                "deterioration_ratio": deterioration_ratio,
            })

    row = realized_rows[idx] if idx < len(realized_rows) else {}
    prior_row = realized_rows[idx - 1] if idx > 0 and idx - 1 < len(realized_rows) else {}
    latest_realized = row.get("originalValue") if row.get("dataAvailable") else None
    latest_unrealized = row.get("unrealizedChange") if row.get("unrealizedAvailable") else None
    if latest_realized is not None and total_dollars > 0:
        loss_pct = max(0.0, -latest_realized / total_dollars * 100)
        unreal_depr_pct = max(0.0, -(latest_unrealized or 0) / total_dollars * 100)
        trailing = [r for r in realized_rows[:idx + 1] if r.get("dataAvailable")]
        trailing = trailing[-4:]
        trailing_loss_pct = (
            max(0.0, -sum(r.get("originalValue") or 0 for r in trailing) / total_dollars * 100)
            if trailing else 0.0
        )
        component_scores["lossesAndRecoveries"] = round(_clamp(
            100 - loss_pct * 16 - trailing_loss_pct * 7 - unreal_depr_pct * 5
        ))
        observations.update({
            "latest_realized": latest_realized,
            "latest_loss_pct": round(loss_pct, 2),
            "latest_unrealized": latest_unrealized,
            "latest_unrealized_depr_pct": round(unreal_depr_pct, 2),
            "trailing_loss_pct": round(trailing_loss_pct, 2),
        })

    if stress is not None and weighted_mark is not None:
        component_scores["portfolioQuality"] = round(_clamp(
            100 - stress * 2.0 - (top_stressed or 0) * 6.0 -
            max(0.0, 98 - weighted_mark) * 2.0 + (near_par or 0) * 0.08
        ))
        observations.update({
            "near_par_pct": round(near_par or 0, 1),
            "weighted_mark": round(weighted_mark, 1),
            "top_stressed_pct": round(top_stressed or 0, 2),
        })

    if idx > 0 and stress is not None:
        prior_metrics = _portfolio_metrics(all_data.get(quarters[idx - 1], []))
        prior_stress = prior_metrics.get("stress_pct")
        stress_change = stress - prior_stress if prior_stress is not None else 0.0
        prior_loss = prior_row.get("originalValue") if prior_row.get("dataAvailable") else None
        loss_worsening = 0.0
        if latest_realized is not None and prior_loss is not None and total_dollars > 0:
            loss_worsening = max(0.0, (prior_loss - latest_realized) / total_dollars * 100)
        component_scores["momentum"] = round(_clamp(
            82 - max(0.0, stress_change) * 5.0 + max(0.0, -stress_change) * 2.0 -
            max(0.0, (fwd or 0) - (cure or 0)) * 1.1 - loss_worsening * 12.0
        ))
        observations["stress_change"] = round(stress_change, 1)

    available_weight = sum(SCORE_WEIGHTS[k] for k, v in component_scores.items() if v is not None)
    if available_weight:
        overall = round(sum(component_scores[k] * SCORE_WEIGHTS[k]
                            for k in component_scores if component_scores[k] is not None) / available_weight)
    else:
        overall = None

    return {
        "quarter": q,
        "score": overall,
        "category": _score_category(overall),
        "componentScores": component_scores,
        "dataCoveragePct": round(available_weight / sum(SCORE_WEIGHTS.values()) * 100),
        "observations": observations,
    }


def _driver(metric: str, value, prior, direction: str, impact: float, explanation: str) -> dict:
    return {
        "metric": metric,
        "currentValue": value,
        "priorValue": prior,
        "change": None if value is None or prior is None else round(value - prior, 1),
        "direction": direction,
        "impact": round(impact, 1),
        "explanation": explanation,
    }


def _score_trend(score: Optional[float], prior_score: Optional[float],
                 observations: dict) -> str:
    if score is None:
        return "Stable"
    change = score - prior_score if score is not None and prior_score is not None else 0
    stress_change = observations.get("stress_change") or 0
    if change >= 4 and stress_change <= 0:
        return "Improving"
    if change <= -8 or stress_change >= 4:
        return "Materially Deteriorating"
    if change <= -3 or stress_change > 1:
        return "Weakening"
    return "Stable"


def _score_summary(ticker: str, score: dict, prior_score: Optional[int],
                   positive: list, negative: list) -> str:
    current = score.get("overallScore")
    category = score.get("category")
    trend = score.get("trend", "Stable").lower()
    obs = score.get("observations", {})
    parts = [
        f"CreditPulse assigns {ticker} a score of {current}, placing it in the {category} category.",
        f"Overall credit performance appears {trend}."
    ]
    if prior_score is not None and current is not None:
        delta = current - prior_score
        parts.append(f"The score changed {delta:+.0f} points from the prior quarter.")
    if obs.get("stress_pct") is not None:
        parts.append(
            f"Latest stressed exposure is {obs['stress_pct']}% of fair value, "
            f"with {obs.get('distressed_pct', 0)}% below a 70 mark."
        )
    if obs.get("forward_roll") is not None:
        ratio = obs.get("deterioration_ratio")
        ratio_text = f" and a deterioration ratio of {ratio:.1f}" if ratio is not None else ""
        parts.append(
            f"Forward roll was {obs['forward_roll']}% versus a {obs.get('cure', 0)}% cure rate{ratio_text}."
        )
    if obs.get("latest_realized") is not None:
        parts.append(
            f"Latest realized gains/losses were {_compact_dollars(obs['latest_realized'])}, "
            f"or {obs.get('latest_loss_pct', 0)}% of the portfolio on a loss basis."
        )
    if negative:
        parts.append(f"The main pressure point is {negative[0]['explanation'][0].lower() + negative[0]['explanation'][1:]}")
    if positive:
        parts.append(f"A key offset is {positive[0]['explanation'][0].lower() + positive[0]['explanation'][1:]}")
    if score.get("confidence") != "High":
        parts.append(f"Confidence is {score.get('confidence').lower()} because data coverage is {score.get('dataCoveragePct')}%.")
    return " ".join(parts)


def compute_creditpulse_score(ticker: str, all_data: dict, quarters: list, rollrate: dict,
                              realized_rows: list, fund_type: str) -> dict:
    historical = [
        _score_for_period(all_data, quarters, i, rollrate, realized_rows)
        for i in range(len(quarters))
    ]
    latest = historical[-1] if historical else {}
    prior = next((p for p in reversed(historical[:-1]) if p.get("score") is not None), None)
    score = latest.get("score")
    prior_score = prior.get("score") if prior else None
    score_change = score - prior_score if score is not None and prior_score is not None else None
    coverage = latest.get("dataCoveragePct", 0)
    multi_period = len([p for p in historical if p.get("score") is not None]) >= 4
    confidence = "High" if coverage >= 85 and multi_period else ("Medium" if coverage >= 60 else "Low")
    trend = _score_trend(score, prior_score, latest.get("observations", {}))
    obs = latest.get("observations", {})

    positive = []
    negative = []
    if obs.get("near_par_pct") is not None and obs["near_par_pct"] >= 85:
        positive.append(_driver(
            "Near-par exposure", obs["near_par_pct"], None, "positive", 6,
            f"{obs['near_par_pct']}% of the portfolio is marked at 90 or above."
        ))
    if obs.get("cure") is not None and obs.get("forward_roll") is not None and obs["cure"] >= obs["forward_roll"]:
        positive.append(_driver(
            "Cure rate", obs["cure"], None, "positive", 7,
            f"Cure rate of {obs['cure']}% matched or exceeded forward roll of {obs['forward_roll']}%."
        ))
    if obs.get("latest_realized") is not None and obs["latest_realized"] >= 0:
        positive.append(_driver(
            "Realized gains/losses", obs["latest_realized"], None, "positive", 5,
            f"Latest realized gains/losses were positive at {_compact_dollars(obs['latest_realized'])}."
        ))
    if obs.get("stress_change") is not None and obs["stress_change"] < 0:
        positive.append(_driver(
            "Stress trend", obs["stress_pct"], None, "positive", 5,
            f"Stressed exposure declined by {abs(obs['stress_change'])} percentage points from the prior quarter."
        ))

    if obs.get("stress_pct") is not None and obs["stress_pct"] >= 5:
        negative.append(_driver(
            "Stressed exposure", obs["stress_pct"], None, "negative", -8,
            f"Stressed exposure is elevated at {obs['stress_pct']}% of portfolio fair value."
        ))
    if obs.get("deterioration_ratio") is not None and obs["deterioration_ratio"] > 1.5:
        negative.append(_driver(
            "Deterioration ratio", obs["deterioration_ratio"], None, "negative", -7,
            f"Forward roll is {obs['deterioration_ratio']:.1f}x the cure rate."
        ))
    if obs.get("latest_loss_pct") is not None and obs["latest_loss_pct"] >= 0.5:
        negative.append(_driver(
            "Realized loss rate", obs["latest_loss_pct"], None, "negative", -6,
            f"Latest realized losses were {obs['latest_loss_pct']}% of portfolio fair value."
        ))
    if obs.get("latest_unrealized_depr_pct") is not None and obs["latest_unrealized_depr_pct"] >= 1:
        negative.append(_driver(
            "Unrealized depreciation", obs["latest_unrealized_depr_pct"], None, "negative", -5,
            f"Latest net unrealized depreciation was {obs['latest_unrealized_depr_pct']}% of portfolio fair value."
        ))
    if obs.get("stress_change") is not None and obs["stress_change"] > 1:
        negative.append(_driver(
            "Stress trend", obs["stress_pct"], None, "negative", -5,
            f"Stressed exposure increased by {obs['stress_change']} percentage points from the prior quarter."
        ))

    positive = positive[:3]
    negative = negative[:3]
    result = {
        "version": 1,
        "overallScore": score,
        "category": _score_category(score),
        "priorPeriodScore": prior_score,
        "scoreChange": score_change,
        "confidence": confidence,
        "dataCoveragePct": coverage,
        "trend": trend,
        "componentScores": latest.get("componentScores", {}),
        "componentWeights": SCORE_WEIGHTS,
        "positiveDrivers": positive,
        "negativeDrivers": negative,
        "historicalScores": [
            {"quarter": p["quarter"], "score": p["score"], "category": p["category"]}
            for p in historical
        ],
        "observations": obs,
        "methodology": (
            "CreditPulse Score is a deterministic analytical score, not a credit rating. "
            "It combines mark-bucket stress, roll-rate migration, realized/unrealized losses, "
            "portfolio quality, and momentum. Missing components are excluded and remaining "
            "weights are reweighted proportionally."
        ),
    }
    result["summary"] = _score_summary(ticker, result, prior_score, positive, negative)
    return result

# ─── MAIN ENDPOINT ─────────────────────────────────────────────────────────────

@app.get("/api/analyze")
async def analyze(
    ticker:  str  = Query(...,   description="Ticker symbol, e.g. CCFLX, ARCC, GSBD"),
    refresh: bool = Query(False, description="Bypass cache and recompute"),
):
    ticker = ticker.upper().strip()
    if not re.match(r'^[A-Z0-9]{1,10}$', ticker):
        raise HTTPException(400, "Invalid ticker symbol")

    async with httpx.AsyncClient(follow_redirects=True) as client:

        # 1. Cache check
        if not refresh:
            cached = await cache_get(client, ticker)
            if cached:
                cached["cached"] = True
                return cached

        # 2. CIK lookup
        cik = await lookup_cik(client, ticker)
        if not cik:
            raise HTTPException(404, f"Ticker '{ticker}' not found on SEC EDGAR")

        # 3. Submissions (filing history)
        subs = await get_submissions(client, cik)
        if not subs:
            raise HTTPException(502, "Failed to fetch EDGAR filing history")

        fund_name = get_fund_name(subs)
        fund_type = detect_fund_type(subs)

        # 4. Fetch filings in parallel (semaphore = 3 concurrent EDGAR requests)
        sem      = asyncio.Semaphore(3)
        all_data = {}
        quarters = []
        realized_loss_metrics = []
        net_assets = None

        if fund_type == "interval_fund":
            filings = get_recent_nport_filings(subs, n=6)
            if not filings:
                raise HTTPException(404, f"No quarterly NPORT-P filings found for {ticker}")

            tasks = [fetch_nport_xml(client, sem, cik, f["acc"]) for f in filings]
            xmls  = await asyncio.gather(*tasks)
            net_assets = parse_nport_net_assets(xmls[-1]) if xmls else None

            leverage_rows = []
            for filing, xml_text in zip(filings, xmls):
                label = period_to_label(filing["period"])
                all_data[label] = parse_nport_xml(xml_text)
                quarters.append(label)
                fl = parse_nport_fund_level(xml_text)
                leverage_rows.append({
                    "quarter": label,
                    "debt": fl["borrowings"],
                    "equity": fl["netAssets"],
                    "assets": fl["totAssets"],
                    "debtIsProxy": fl["borrowingsIsProxy"],
                })
                realized_loss_metrics.append({
                    "periodEnd": filing["period"],
                    "quarter": _quarter_label_long(filing["period"]),
                    "periodStart": None,
                    "originalValue": None,
                    "metricType": "unavailable",
                    "source": "NPORT-P",
                    "sourceTag": None,
                    "dataAvailable": False,
                })

        else:  # BDC
            filings = get_recent_bdc_filings(subs, n=6)
            if not filings:
                raise HTTPException(404, f"No 10-K/10-Q filings found for {ticker}")

            # Metrics need one extra prior filing: the oldest displayed quarter
            # may be an annual/YTD figure that can only be de-cumulated against
            # its predecessor's YTD value. Fetch it for the chain, drop it after.
            metric_filings = get_recent_bdc_filings(subs, n=7)
            display_periods = {f["period"] for f in filings}

            tasks   = [fetch_bdc_filing(client, sem, cik, f["acc"], f["period"]) for f in filings]
            metric_tasks = [
                fetch_bdc_realized_loss_metric(client, sem, cik, f["acc"], f["period"], f["form"])
                for f in metric_filings
            ]
            results, realized_loss_metrics = await asyncio.gather(
                asyncio.gather(*tasks),
                asyncio.gather(*metric_tasks),
            )
            net_assets = next((m.get("netAssets") for m in reversed(realized_loss_metrics)
                               if m.get("netAssets")), None)

            for filing, invs in zip(filings, results):
                label = period_to_label(filing["period"])
                all_data[label] = invs
                quarters.append(label)

            # Leverage rows come from the same parsed iXBRL metric documents,
            # restricted to the displayed quarters.
            leverage_rows = [
                {
                    "quarter": period_to_label(m.get("periodEnd") or ""),
                    "debt": m.get("totalDebt"),
                    "equity": m.get("netAssets"),
                }
                for m in realized_loss_metrics
                if m.get("periodEnd") in display_periods
            ]

        if not quarters:
            raise HTTPException(404, "No filings could be parsed")

        # 5. Run analysis
        summary    = compute_summary(all_data, quarters)
        summary["net_assets"] = round(net_assets, 0) if net_assets else None
        summary["stress_vs_equity_pct"] = (
            round(summary["stressed_fv_m"] * 1e6 / net_assets * 100, 1)
            if net_assets else None
        )
        buckets    = compute_buckets(all_data, quarters)
        rollrate   = compute_rollrate(all_data, quarters)
        stress_pos = compute_stress_positions(all_data, quarters)
        position_scatter = compute_position_scatter(all_data, quarters)
        realized_loss_rows = calculate_quarterly_realized_losses(realized_loss_metrics)
        if fund_type != "interval_fund":
            realized_loss_rows = [r for r in realized_loss_rows
                                  if r.get("periodEnd") in display_periods]

        # Stat tiles: cumulative realized over the window, sized against the
        # average parsed portfolio fair value, plus latest-quarter values.
        avail_rows = [r for r in realized_loss_rows if r.get("dataAvailable")]
        qtr_totals = [sum(i.get("fv", 0) for i in all_data.get(q, [])) * 1000 for q in quarters]
        qtr_totals = [t for t in qtr_totals if t > 0]
        avg_portfolio = sum(qtr_totals) / len(qtr_totals) if qtr_totals else 0
        cum_realized = round(sum(r["originalValue"] for r in avail_rows), 2) if avail_rows else None
        last_row = realized_loss_rows[-1] if realized_loss_rows else {}
        gains_summary = {
            "quarters_covered": len(avail_rows),
            "cum_realized": cum_realized,
            "cum_realized_pct": (round(cum_realized / avg_portfolio * 100, 1)
                                 if cum_realized is not None and avg_portfolio > 0 else None),
            "latest_quarter": last_row.get("quarter"),
            "latest_realized": last_row.get("originalValue") if last_row.get("dataAvailable") else None,
            "latest_unrealized": last_row.get("unrealizedChange"),
            "avg_portfolio": round(avg_portfolio, 0) if avg_portfolio else None,
        }

        if fund_type == "interval_fund":
            definition = (
                "Interval funds report holdings via NPORT-P, which does not disclose fund-level "
                "realized or unrealized gains on the loan portfolio. This analysis is available "
                "for BDCs that file 10-K/10-Q statements of operations."
            )
        else:
            definition = (
                "Quarterly figures are extracted from each filing's statement of operations "
                "(inline XBRL). Where filings report cumulative year-to-date values, quarters "
                "are computed by differencing consecutive filings."
            )

        realized_losses = {
            "version": 3,
            "title": "Realized & Unrealized Gains (Losses)",
            "definition": definition,
            "items": realized_loss_rows,
            "insight": generate_realized_loss_insight(realized_loss_rows),
            "gains_summary": gains_summary,
        }
        credit_score = compute_creditpulse_score(
            ticker, all_data, quarters, rollrate, realized_loss_rows, fund_type
        )
        leverage = compute_leverage(fund_type, leverage_rows,
                                    stress_pct=summary.get("stress_pct"))

        result = {
            "ticker":           ticker,
            "cik":              cik,
            "fund_name":        fund_name,
            "fund_type":        fund_type,
            "quarters":         quarters,
            "summary":          summary,
            "buckets":          buckets,
            "rollrate":         rollrate,
            "stress_positions": stress_pos,
            "position_scatter":  position_scatter,
            "realized_losses":   realized_losses,
            "credit_score":      credit_score,
            "leverage":          leverage,
            "cached":           False,
        }

        # 6. Cache result
        await cache_set(client, ticker, result)

        return result

# ─── VERCEL HANDLER ────────────────────────────────────────────────────────────

handler = Mangum(app, lifespan="off")
