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
import re

import requests


SEC_HEADERS = {
    "User-Agent": "AI Hedge Fund Research App david@example.com",
    "Accept-Encoding": "gzip, deflate",
}

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"


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
    cik = get_cik_for_ticker(ticker)

    url = SUBMISSIONS_URL_TEMPLATE.format(cik=cik)
    response = requests.get(url, headers=SEC_HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()

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


def find_latest_filing(ticker, form_type):
    """Find the most recent filing matching a form type, such as 10-K or 10-Q."""
    form_type = form_type.upper().strip()
    filings = get_recent_filings(ticker)

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
    download_filing(ticker_input, "10-K")
