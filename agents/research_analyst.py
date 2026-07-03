import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.research_memory import save_research_report, get_reports_for_ticker
from openai import OpenAI
from dotenv import load_dotenv
from pypdf import PdfReader
from data.market_data import get_company_data
from data.sec_data import get_recent_filings

load_dotenv()

client = OpenAI()

DOCS_DIR = Path("docs")


def read_pdf(path):
    reader = PdfReader(path)
    text = ""

    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text + "\n"

    return text


def load_fund_documents():
    documents = []

    for pdf_path in DOCS_DIR.glob("*.pdf"):
        text = read_pdf(pdf_path)
        documents.append(f"\n\n--- {pdf_path.name} ---\n{text}")

    return "\n".join(documents)


def research_company(ticker):
    fund_docs = load_fund_documents()
    company_data = get_company_data(ticker)
    sec_filings = get_recent_filings(ticker)

    prompt = f"""
You are the first Research Analyst Agent for an AI Hedge Fund.

You must follow the fund documents below.

Fund Documents:
{fund_docs}

Company Data:
{company_data}

SEC Filing Data:
{sec_filings}

Task:
Analyze the company with ticker: {ticker}

Give a beginner-friendly but investment-grade research memo.

Include:
1. Business overview
2. Why this company might be attractive
3. Main risks
4. What data we still need
5. Whether it deserves deeper research
6. Final rating: Reject / Watchlist / Deep Research Candidate

Do not pretend to know facts you do not know.
If current financial data is missing, say so clearly.
"""

    response = client.responses.create(
        model="gpt-5.5",
        input=prompt,
    )

    return response.output_text


if __name__ == "__main__":
    ticker = input("Enter stock ticker: ")
    result = research_company(ticker)

    print("\n\n--- RESEARCH MEMO ---\n")
    print(result)

    output_path = Path("outputs") / f"{ticker.upper()}_research_memo.txt"
    output_path.write_text(result)
    save_research_report(ticker, result)

    print("Saved memo to memory database.")
    print(f"\nSaved memo to: {output_path}")