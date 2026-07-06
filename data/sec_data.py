"""
SEC EDGAR data utilities for the AI Hedge Fund project.

This module intentionally does one thing well:
- resolve a ticker to a CIK
- list recent SEC filings
- download the latest filing of a given form type, such as 10-K or 10-Q

Run directly for a manual test:
    python data/sec_data.py
"""

from pathlib import Path
import os
import re
from bs4 import BeautifulSoup

import requests
from dotenv import load_dotenv


load_dotenv()

SEC_HEADERS = {
    "User-Agent": os.getenv(
        "SEC_USER_AGENT",
        "AI Hedge Fund Research App contact@example.com",
    ),
    "Accept-Encoding": "gzip, deflate",
}

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANY_FACTS_URL_TEMPLATE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"
DEFAULT_RESEARCH_FORMS = ("10-K", "10-Q")
DEFAULT_FINANCIAL_FACTS = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capital_expenditures": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
    "short_term_investments": ["ShortTermInvestments"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"],
    "shares_outstanding": ["EntityCommonStockSharesOutstanding"],
    "common_stock_repurchased": ["PaymentsForRepurchaseOfCommonStock"],
}


class SecEdgarError(Exception):
    """Raised when SEC EDGAR data cannot be fetched or interpreted."""


def _safe_filename(value):
    """Return a filesystem-safe version of a filename component."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def get_company_tickers():
    """Download SEC ticker-to-CIK mapping."""
    response = requests.get(COMPANY_TICKERS_URL, headers=SEC_HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def get_cik_for_ticker(ticker):
    """Return the zero-padded SEC CIK for a stock ticker."""
    ticker = ticker.upper().strip()
    companies = get_company_tickers()

    for company in companies.values():
        if company["ticker"].upper() == ticker:
            return str(company["cik_str"]).zfill(10)

    raise SecEdgarError(f"Could not find CIK for ticker: {ticker}")


def get_recent_filings(ticker, limit=None):
    """
    Return recent SEC filings for a ticker.

    If limit is None, return all filings available in the SEC recent filings payload.
    """
    ticker = ticker.upper().strip()
    try:
        cik = get_cik_for_ticker(ticker)
    except Exception as exc:
        return {"ticker": ticker, "error": f"Could not fetch SEC ticker map: {exc}"}

    url = SUBMISSIONS_URL_TEMPLATE.format(cik=cik)
    try:
        response = requests.get(url, headers=SEC_HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return {
            "ticker": ticker,
            "cik": cik,
            "error": f"Could not fetch recent SEC filings: {exc}",
        }

    filings = data["filings"]["recent"]
    total = len(filings["form"])
    count = total if limit is None else min(limit, total)

    results = []
    for i in range(count):
        results.append(
            {
                "form": filings["form"][i],
                "filing_date": filings["filingDate"][i],
                "report_date": filings["reportDate"][i],
                "accession_number": filings["accessionNumber"][i],
                "primary_document": filings["primaryDocument"][i],
            }
        )

    return {
        "ticker": ticker,
        "cik": cik,
        "company_name": data.get("name"),
        "recent_filings": results,
    }


def get_company_facts(ticker):
    """Return the SEC XBRL company facts payload for a ticker."""
    ticker = ticker.upper().strip()
    cik = get_cik_for_ticker(ticker)
    url = COMPANY_FACTS_URL_TEMPLATE.format(cik=cik)

    response = requests.get(url, headers=SEC_HEADERS, timeout=45)
    response.raise_for_status()
    return response.json()


def get_structured_financial_facts(ticker):
    """
    Extract core SEC financial facts from companyfacts XBRL data.

    This favors primary SEC facts over secondary provider fields and returns a compact,
    prompt-friendly packet with recent annual and quarterly values.
    """
    ticker = ticker.upper().strip()

    try:
        facts = get_company_facts(ticker)
    except Exception as exc:
        return {
            "ticker": ticker,
            "error": f"Could not fetch SEC company facts: {exc}",
        }

    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    metrics = {}

    for metric_name, candidate_tags in DEFAULT_FINANCIAL_FACTS.items():
        metric = extract_metric_from_company_facts(us_gaap, candidate_tags)
        metrics[metric_name] = metric

    derived = derive_financial_facts(metrics)

    return {
        "ticker": ticker,
        "cik": str(facts.get("cik")).zfill(10),
        "company_name": facts.get("entityName"),
        "source": "SEC companyfacts XBRL API",
        "metrics": metrics,
        "derived": derived,
        "notes": [
            "Annual facts favor 10-K FY values in USD where available.",
            "Quarterly facts favor 10-Q frame values in USD where available.",
            "Use these facts to reconcile Yahoo Finance or other secondary data conflicts.",
        ],
    }


def format_structured_financial_facts(facts):
    if "error" in facts:
        return f"Error: {facts['error']}"

    lines = [
        f"Structured SEC financial facts for {facts['ticker']} - {facts['company_name']}",
        f"Source: {facts['source']}",
        "",
        "Latest annual facts:",
    ]

    for metric_name, metric in facts["metrics"].items():
        latest = metric.get("annual", [None])[0] if metric.get("annual") else None
        if latest is None:
            lines.append(f"- {metric_name}: missing")
            continue

        lines.append(
            f"- {metric_name}: {latest['value']} "
            f"({metric['unit']}, {latest['end']}, {latest['form']}, tag={metric['tag']})"
        )

    lines.extend([
        "",
        "Derived facts:",
    ])

    for name, value in facts["derived"].items():
        lines.append(f"- {name}: {value}")

    return "\n".join(lines)


def extract_metric_from_company_facts(us_gaap, candidate_tags):
    candidates = []

    for tag in candidate_tags:
        tag_payload = us_gaap.get(tag)
        if not tag_payload:
            continue

        usd_units = tag_payload.get("units", {}).get("USD")
        shares_units = tag_payload.get("units", {}).get("shares")
        units = usd_units or shares_units
        if not units:
            continue

        annual = select_recent_facts(units, form="10-K", frame_prefix="CY", limit=3)
        quarterly = select_recent_facts(units, form="10-Q", frame_prefix="CY", limit=4)

        candidates.append({
            "tag": tag,
            "label": tag_payload.get("label"),
            "description": tag_payload.get("description"),
            "unit": "USD" if usd_units else "shares",
            "annual": annual,
            "quarterly": quarterly,
        })

    if candidates:
        candidates.sort(key=metric_recency_key, reverse=True)
        return candidates[0]

    return {
        "tag": None,
        "label": None,
        "description": None,
        "unit": None,
        "annual": [],
        "quarterly": [],
        "missing": True,
    }


def metric_recency_key(metric):
    dates = []

    for period in ("annual", "quarterly"):
        for fact in metric.get(period, []):
            if fact.get("end"):
                dates.append(fact["end"])

    return max(dates) if dates else ""


def select_recent_facts(facts, form, frame_prefix, limit):
    filtered = []

    for fact in facts:
        if fact.get("form") != form:
            continue
        if fact.get("val") is None:
            continue

        frame = fact.get("frame", "")
        if frame_prefix and frame and not frame.startswith(frame_prefix):
            continue

        filtered.append({
            "end": fact.get("end"),
            "fy": fact.get("fy"),
            "fp": fact.get("fp"),
            "form": fact.get("form"),
            "filed": fact.get("filed"),
            "frame": fact.get("frame"),
            "value": fact.get("val"),
            "accession_number": fact.get("accn"),
        })

    filtered.sort(key=lambda item: (item.get("end") or "", item.get("filed") or ""), reverse=True)
    return filtered[:limit]


def latest_metric_value(metrics, metric_name, period="annual"):
    metric = metrics.get(metric_name, {})
    values = metric.get(period, [])
    if not values:
        return None
    return values[0].get("value")


def derive_financial_facts(metrics):
    annual_revenue = latest_metric_value(metrics, "revenue")
    annual_net_income = latest_metric_value(metrics, "net_income")
    annual_operating_cash_flow = latest_metric_value(metrics, "operating_cash_flow")
    annual_capex = latest_metric_value(metrics, "capital_expenditures")
    cash = latest_metric_value(metrics, "cash_and_equivalents")
    short_term_investments = latest_metric_value(metrics, "short_term_investments")
    long_term_debt = latest_metric_value(metrics, "long_term_debt")

    free_cash_flow = None
    if annual_operating_cash_flow is not None and annual_capex is not None:
        free_cash_flow = annual_operating_cash_flow - abs(annual_capex)

    net_cash_or_debt = None
    if cash is not None and long_term_debt is not None:
        net_cash_or_debt = cash + (short_term_investments or 0) - long_term_debt

    net_margin = None
    if annual_revenue and annual_net_income is not None:
        net_margin = annual_net_income / annual_revenue

    return {
        "latest_annual_free_cash_flow": free_cash_flow,
        "latest_net_cash_or_debt": net_cash_or_debt,
        "latest_annual_net_margin": net_margin,
    }


def find_latest_filing(ticker, form_type):
    """Find the most recent filing matching a form type, such as 10-K or 10-Q."""
    form_type = form_type.upper().strip()
    filings = get_recent_filings(ticker)

    if "error" in filings:
        raise SecEdgarError(filings["error"])

    for filing in filings["recent_filings"]:
        if filing["form"].upper() == form_type:
            return filings, filing

    raise SecEdgarError(f"No recent {form_type} found for {ticker.upper()}.")


def build_filing_url(cik, accession_number, primary_document):
    """Build the SEC Archives URL for a primary filing document."""
    cik_without_leading_zeroes = str(int(cik))
    accession_without_dashes = accession_number.replace("-", "")
    return (
        f"{ARCHIVES_BASE_URL}/"
        f"{cik_without_leading_zeroes}/"
        f"{accession_without_dashes}/"
        f"{primary_document}"
    )


def extract_text_from_html(html_path):
    """
    Convert a downloaded SEC filing HTML file into readable plain text.

    Returns the path to the generated TXT file.
    """
    html_path = Path(html_path)
    raw_html = html_path.read_text(encoding="utf-8", errors="ignore")

    soup = BeautifulSoup(raw_html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip()

    txt_path = html_path.with_suffix(".txt")
    txt_path.write_text(text, encoding="utf-8")

    print(f"Extracted text to {txt_path}")
    return txt_path


def read_filing_text_excerpt(txt_path, max_chars=12000):
    """Read a bounded excerpt from an extracted SEC filing text file."""
    text = Path(txt_path).read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip()

    if len(text) <= max_chars:
        return text

    return (
        text[:max_chars]
        + "\n\n[SEC filing excerpt truncated for prompt size. Use local TXT file for full filing.]"
    )


def download_filing(ticker, form_type="10-K"):
    """
    Download the latest filing of the requested form type and save it locally.

    Returns the path to the downloaded HTML file.
    """
    ticker = ticker.upper().strip()
    form_type = form_type.upper().strip()

    filing_payload, target = find_latest_filing(ticker, form_type)
    cik = filing_payload["cik"]

    url = build_filing_url(
        cik=cik,
        accession_number=target["accession_number"],
        primary_document=target["primary_document"],
    )

    print(f"Downloading {ticker} {form_type}...")
    print(url)

    response = requests.get(url, headers=SEC_HEADERS, timeout=45)
    response.raise_for_status()

    company_dir = Path("companies") / ticker / "filings"
    company_dir.mkdir(parents=True, exist_ok=True)

    filename = _safe_filename(
        f"{target['filing_date']}_{form_type}_{target['accession_number']}.html"
    )
    filepath = company_dir / filename
    filepath.write_text(response.text, encoding="utf-8")

    print(f"Saved to {filepath}")
    return filepath


def get_latest_filing_evidence(ticker, form_types=DEFAULT_RESEARCH_FORMS, max_chars_per_filing=12000):
    """
    Download, extract, and return bounded text excerpts for primary SEC filings.

    Returns structured evidence instead of raising on individual filing failures so
    the research agent can continue while clearly reporting missing sources.
    """
    evidence = []

    for form_type in form_types:
        form_type = form_type.upper().strip()
        try:
            filing_payload, target = find_latest_filing(ticker, form_type)
            html_path = download_filing(ticker, form_type)
            txt_path = extract_text_from_html(html_path)
            excerpt = read_filing_text_excerpt(txt_path, max_chars=max_chars_per_filing)

            evidence.append(
                {
                    "form": form_type,
                    "ticker": filing_payload["ticker"],
                    "company_name": filing_payload["company_name"],
                    "cik": filing_payload["cik"],
                    "filing_date": target["filing_date"],
                    "report_date": target["report_date"],
                    "accession_number": target["accession_number"],
                    "primary_document": target["primary_document"],
                    "html_path": str(html_path),
                    "text_path": str(txt_path),
                    "text_excerpt": excerpt,
                }
            )
        except Exception as exc:
            evidence.append(
                {
                    "form": form_type,
                    "ticker": ticker.upper().strip(),
                    "error": str(exc),
                }
            )

    return evidence


def print_recent_filings(ticker, limit=25):
    """Print recent filings for quick debugging."""
    filings = get_recent_filings(ticker, limit=limit)

    print(f"\nRecent SEC filings for {filings['ticker']} - {filings['company_name']}:\n")

    for filing in filings["recent_filings"]:
        print(
            f"{filing['filing_date']}  "
            f"{filing['form']:<8}  "
            f"{filing['primary_document']}"
        )

    print()


if __name__ == "__main__":
    ticker_input = input("Ticker: ").strip().upper()

    print_recent_filings(ticker_input, limit=25)
    html_path = download_filing(ticker_input, "10-K")
    extract_text_from_html(html_path)
