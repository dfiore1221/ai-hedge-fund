import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.research_memory import build_research_memory_context, save_research_report
from openai import OpenAI
from dotenv import load_dotenv
from pypdf import PdfReader
from data.market_data import get_company_data
from data.sec_data import (
    get_latest_filing_evidence,
    get_recent_filings,
    get_structured_financial_facts,
)

load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")

DOCS_DIR = PROJECT_ROOT / "docs"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
MARKET_INTELLIGENCE_REPORT = PROJECT_ROOT / "reports" / "market_intelligence" / "daily_market_intelligence.md"


def get_openai_api_key():
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key == "your_openai_api_key_here":
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to a local .env file before running research."
        )
    return api_key


def read_pdf(path):
    try:
        reader = PdfReader(path)
    except Exception as exc:
        return f"[Could not read {path.name}: {exc}]"

    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text + "\n"

    return text.strip()


def load_fund_documents():
    documents = []

    for pdf_path in DOCS_DIR.glob("*.pdf"):
        text = read_pdf(pdf_path)
        if text:
            documents.append(f"\n\n--- {pdf_path.name} ---\n{text}")

    return "\n".join(documents)


def load_market_intelligence_context(max_chars=6000):
    if not MARKET_INTELLIGENCE_REPORT.exists():
        return {
            "status": "missing",
            "message": "No daily market intelligence report found. Run `python3 main.py macro today` before company research.",
        }

    text = MARKET_INTELLIGENCE_REPORT.read_text(encoding="utf-8")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[Market intelligence context truncated for prompt size.]"

    return {
        "status": "available",
        "path": str(MARKET_INTELLIGENCE_REPORT),
        "report_excerpt": text,
    }


def build_research_prompt(
    ticker,
    fund_docs,
    company_data,
    sec_filings,
    sec_financial_facts,
    sec_filing_evidence,
    research_memory,
    market_intelligence,
):
    return f"""
You are the first Research Analyst Agent for an AI Hedge Fund.

You must follow the fund documents below. Treat them as binding operating rules.

Fund Documents:
{fund_docs}

Company Data:
{company_data}

SEC Filing Data:
{sec_filings}

Verified SEC Financial Facts:
{sec_financial_facts}

Primary SEC Filing Evidence:
{sec_filing_evidence}

Research Memory:
{research_memory}

Market Intelligence Context:
{market_intelligence}

Task:
Analyze the company with ticker: {ticker}

Give a beginner-friendly but investment-grade research memo.

Use this exact structure:

1. Executive Summary
2. Memory and Thesis Update
   - If prior research exists, summarize the prior view.
   - State what changed, what stayed the same, and whether confidence increased or decreased.
   - If no prior research exists, say this is the first stored memo for this ticker.
3. Business Overview
4. Market Regime Context
   - Summarize the current macro regime if a daily market intelligence report is available.
   - Explain whether the macro/capital-flow backdrop supports or weakens deeper research on this company.
5. Data Quality Check
   - State what data is present.
   - State what data is missing or stale.
   - Reconcile conflicts between secondary market data and Verified SEC Financial Facts.
   - Do not invent facts.
6. Investment Scorecard
   - Business Quality: 0-100
   - Financial Strength: 0-100
   - Growth: 0-100
   - Valuation: 0-100
   - Risk: 0-100, where higher means lower risk
   - Catalyst Strength: 0-100
   - Overall Research Score: 0-100
7. Why This Company Might Be Attractive
8. Main Risks
9. Key SEC Filing Signals
   - Use the primary SEC filing evidence when available.
   - Name the filing form and filing date behind important claims.
10. What Data We Still Need
11. Recommended Next Step
12. Final Rating: Reject / Watchlist / Deep Research Candidate

Rules:
- Be clear enough for a beginner but rigorous enough for an investment committee.
- If current financial data is missing, say so clearly.
- Prefer primary SEC filing evidence over secondary data when they conflict.
- Prefer Verified SEC Financial Facts over Yahoo Finance or other secondary fields when calculating financial strength, free cash flow, net cash/debt, or margins.
- Use Research Memory to compare the new analysis against prior stored reports.
- If Market Intelligence Context is available, incorporate it before making any company-level conclusion.
- Do not treat prior reports as truth. Treat them as historical analyst views to confirm, update, or reject.
- Avoid false precision. Explain every score in plain English.
- Do not recommend a trade yet. This is company research only.
"""


def research_company(ticker):
    get_openai_api_key()

    ticker = ticker.upper()
    fund_docs = load_fund_documents()
    company_data = get_company_data(ticker)
    sec_filings = get_recent_filings(ticker, limit=10)
    sec_financial_facts = get_structured_financial_facts(ticker)
    sec_filing_evidence = get_latest_filing_evidence(ticker)
    research_memory = build_research_memory_context(ticker)
    market_intelligence = load_market_intelligence_context()
    prompt = build_research_prompt(
        ticker,
        fund_docs,
        company_data,
        sec_filings,
        sec_financial_facts,
        sec_filing_evidence,
        research_memory,
        market_intelligence,
    )

    client = OpenAI()
    response = client.responses.create(
        model=OPENAI_MODEL,
        input=prompt,
    )

    return response.output_text


if __name__ == "__main__":
    ticker = input("Enter stock ticker: ")
    result = research_company(ticker)

    print("\n\n--- RESEARCH MEMO ---\n")
    print(result)

    OUTPUTS_DIR.mkdir(exist_ok=True)
    output_path = OUTPUTS_DIR / f"{ticker.upper()}_research_memo.txt"
    output_path.write_text(result, encoding="utf-8")
    save_research_report(ticker, result)

    print("Saved memo to memory database.")
    print(f"\nSaved memo to: {output_path}")
