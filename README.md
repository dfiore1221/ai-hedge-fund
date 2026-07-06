# AI Hedge Fund

An early-stage research operating system for generating investment-grade company research memos using fund governance documents, public market data, SEC filing metadata, and AI analysis.

## Current Workflow

```bash
python3 main.py macro today
python3 main.py analyze MSFT
python3 main.py history MSFT
python3 main.py thesis MSFT
python3 main.py facts MSFT
```

The `macro today` command:

1. Pulls major index, volatility, rate, dollar, commodity, and crypto proxies.
2. Ranks sector ETFs versus SPY.
3. Produces a macro score, market regime, confidence score, and sector rotation table.
4. Saves the report in `reports/market_intelligence/`.

Company research reads the latest market-intelligence report when available, so the preferred workflow is:

```bash
python3 main.py macro today
python3 main.py analyze MSFT
```

The `analyze` command:

1. Loads the fund documents in `docs/`.
2. Pulls public company data from Yahoo Finance.
3. Pulls recent SEC filing metadata.
4. Pulls structured SEC company facts for core financial metrics.
5. Downloads and extracts the latest 10-K and 10-Q when available.
6. Generates a structured research memo with an investment scorecard.
7. Validates the memo against the investment framework.
8. Saves the report, validation report, and local memory record.

The `thesis` command previews recent stored research for a ticker so the agent and operator can see the prior view before running a new memo.

The `facts` command prints structured SEC company facts and derived values such as free cash flow and net cash/debt.

Validation reports are written to `reports/validation/` and include a framework quality score plus missing checklist items.

## SEC Filing Tools

The `edgar-fetcher-v1` branch can also download and extract the latest SEC filing text:

```bash
python3 data/sec_data.py
```

Downloaded filings are written under `companies/`.

## Setup

Create a local `.env` file:

```bash
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-5
SEC_USER_AGENT="AI Hedge Fund Research App your_email@example.com"
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Next Build Targets

- Daily market regime intelligence
- Capital flow and sector rotation analysis
- Watchlist and ticker comparison workflows
- Portfolio and trade construction layers
