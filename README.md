# AI Hedge Fund

An early-stage research operating system for generating investment-grade company research memos using fund governance documents, public market data, SEC filing metadata, and AI analysis.

## Current Workflow

```bash
python3 main.py dashboard start
python3 main.py morning today
python3 main.py morning-email today
python3 main.py morning-email today --dry-run
python3 main.py macro today
python3 main.py earnings MSFT
python3 main.py portfolio MSFT
python3 main.py journal summary
python3 main.py technical MSFT
python3 main.py options MSFT
python3 main.py news MSFT
python3 main.py backtest MSFT
python3 main.py risk MSFT
python3 main.py cio MSFT
python3 main.py analyze MSFT
python3 main.py history MSFT
python3 main.py thesis MSFT
python3 main.py facts MSFT
```

The `dashboard start` command:

1. Starts a local Streamlit dashboard at `http://localhost:8501`.
2. Shows the latest morning brief, watchlist categories, simulated trade journal, agent debate logs, and research memory.
3. Stores simulated trade journal entries locally in `portfolio/trade_journal.csv`.
4. Tracks planned/open/closed simulated trades, setup type, source, entry, stop, target, shares, planned risk, live price refresh, unrealized P&L, realized P&L, R-multiple, outcome, exit reason, and lessons learned.
5. Keeps local trade notes out of Git.
6. Does not place trades.

The `journal summary` command:

1. Reads the local simulated trade journal.
2. Calculates open trades, closed trades, realized P&L, unrealized P&L, open planned risk, win rate, and average R.
3. Supports the future feedback loop where CIO and Risk can compare recommendations against simulated outcomes.

The `morning today` command:

1. Runs one shared daily macro read.
2. Scans the categorized default watchlist in `framework/watchlist.json`.
3. Runs the CIO committee process for each symbol.
4. Splits names into Approved Simulated Trades, Conditional Setups, Watchlist Setups, Rejected / Avoid Today, and Needs Data.
5. Prints only the top 10 names per section while preserving the full scan in the saved report object.
6. Summarizes results by category, including AI semiconductors, nuclear/power, space, quantum, healthcare, critical materials, and energy.
7. Keeps the output watch-only unless Risk approves a paper-trade candidate.
8. Saves the daily brief in `reports/morning_brief/`.
9. Surfaces better-entry and pullback conditions instead of treating every imperfect setup as avoid.

The `morning-email today` command:

1. Runs the same morning brief.
2. Saves the markdown report in `reports/morning_brief/`.
3. Sends a concise email summary with the full report attached.
4. Supports `--dry-run` to validate email settings without sending.

Required `.env` fields for email delivery:

```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=your_email@example.com
SMTP_PASSWORD=your_email_app_password_here
EMAIL_FROM=your_email@example.com
MORNING_BRIEF_EMAIL_TO=your_email@example.com
```

To schedule the email for 4:45 AM on macOS:

```bash
chmod +x scripts/run_morning_email.sh
mkdir -p ~/Library/LaunchAgents
cp automation/com.dfiore.ai-hedge-fund.morning-brief.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.dfiore.ai-hedge-fund.morning-brief.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.dfiore.ai-hedge-fund.morning-brief.plist
```

Logs are written to `reports/morning_brief/automation.log`, `launchd.out.log`, and `launchd.err.log`.

The `macro today` command:

1. Pulls major index, volatility, rate, dollar, commodity, and crypto proxies.
2. Ranks sector ETFs versus SPY.
3. Produces a macro score, market regime, confidence score, and sector rotation table.
4. Saves the report in `reports/market_intelligence/`.

The `technical` command:

1. Pulls daily OHLCV history.
2. Calculates moving averages, RSI, MACD, ATR, support/resistance, and relative strength vs SPY.
3. Produces entry trigger, stop/invalidation, targets, reward/risk, stance, confidence, risks, and missing information.
4. Saves the report in `reports/technical/`.

The `risk` command:

1. Reads the technical setup, earnings calendar, and portfolio exposure for a ticker.
2. Validates entry, stop, target, reward-to-risk, earnings proximity, and correlated exposure.
3. Calculates paper position size from the risk policy.
4. Blocks long simulated trades when Technical stance is bearish or no_trade.
5. Issues vetoes, warnings, missing information, or approval for paper trade.
6. Saves the report in `reports/risk/`.

The `cio` command:

1. Runs Macro, Technical, Risk, news, options, backtest, and memory/thesis context.
2. Logs each agent's structured output to local memory.
3. Generates a conflict memo when agents disagree.
4. Runs a Devil's Advocate countercase to challenge the setup.
5. Respects Risk Manager vetoes.
6. Produces one pre-market decision: paper trade, watch only, or no trade.
7. Saves the report in `reports/cio/`.

Company research reads the latest market-intelligence report when available, so the preferred workflow is:

```bash
python3 main.py macro today
python3 main.py analyze MSFT
```

Company research also applies the rules in `framework/macro_scoring_rules.json` so valuation, risk, catalyst strength, and final rating are interpreted in the context of the current market regime.

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
