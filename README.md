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
python3 main.py journal open MSFT 400 380 430 10 --status planned --run-id RUN_ID
python3 main.py journal close TRADE_ID 425 --reason target
python3 main.py feedback summary
python3 main.py security check
python3 main.py data-health today
python3 main.py project status
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

1. Starts a local Streamlit dashboard at `http://localhost:8501`, or the port set in `DASHBOARD_PORT`.
2. Shows the latest morning brief, watchlist categories, simulated trade journal, agent debate logs, and research memory.
3. Shows a Data Quality tab with provider status, data-quality score, live price sample coverage, blockers, and recommended provider fixes.
4. Stores simulated trade journal entries locally in `portfolio/trade_journal.csv`.
5. Tracks planned/open/closed simulated trades, setup type, source, entry, stop, target, shares, planned risk, live price refresh, unrealized P&L, realized P&L, R-multiple, outcome, exit reason, and lessons learned.
6. Keeps local trade notes out of Git.
7. Does not place trades.

The `journal summary` command:

1. Reads the local simulated trade journal.
2. Calculates open trades, planned trades, closed trades, realized P&L, daily/weekly realized P&L, unrealized P&L, open planned risk, win rate, average R, and open symbols.
3. Feeds CIO, Risk, the morning brief, and the feedback loop with simulated portfolio memory.

The `journal open` and `journal close` commands:

1. Add planned/open simulated trades from a CIO run or manual review.
2. Close simulated trades with exit price, exit reason, and lessons learned.
3. Keep all trade records local in ignored `portfolio/trade_journal.csv`.
4. Do not place live trades.

The `feedback summary` command:

1. Reads closed simulated trades from the local trade journal.
2. Groups realized outcomes by setup type, source, symbol, and decision tier.
3. Uses `agent_run_id` to connect trades back to the CIO committee run when available.
4. Scores linked agent calls against trade outcomes so Technical, Risk, CIO, Macro, Options, and Quant can be evaluated over time.
5. Saves a local decision feedback report in `reports/feedback/`.

The `security check` command:

1. Confirms required environment settings are present without printing secret values.
2. Checks that local-only files such as `.env`, memory DBs, reports, holdings, and the trade journal are ignored by Git.
3. Warns if dashboard passcode protection or approved email recipient allowlisting is missing.
4. Verifies morning brief email recipients against `APPROVED_EMAIL_RECIPIENTS` when configured.

The `data-health today` command:

1. Checks configured data providers without printing secret values.
2. Runs a live sample of watchlist price-history checks through the current market-data path.
3. Verifies FRED macro data, the FRED/Trading Economics economic calendar path, and Finnhub/Yahoo news availability when configured or available.
4. Checks Tiingo latest equity prices when `TIINGO_API_KEY` is configured, or Alpaca latest stock bars when Alpaca keys are configured, then compares the second provider vs Yahoo for provider agreement.
5. Scores the morning data packet across price/bars, reference data, earnings/events, news/analyst, options, macro/event context, provider agreement checks, and critical errors.
6. Produces a data-quality gate: Pass, Conditional, Watch Only, Needs Data, or Blocked.
7. Explains whether caution is caused by weak setup quality or missing/stale/conflicting data.

The `project status` command:

1. Prints the active project root, Git branch, latest commit, remote, working-tree cleanliness, `.env` presence, dashboard port, and automation paths.
2. Use this first when you are unsure which local copy is active.

## Source of Truth

The active working copy is:

```bash
/Users/davidfiore/Documents/Hedge Fund/current-ai-hedge-fund
```

Use this folder for dashboard runs, morning brief automation, commits, and future development. Older copies may exist, but this folder is the current GitHub-backed source of truth.

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
APPROVED_EMAIL_RECIPIENTS=your_email@example.com
```

Optional dashboard protection:

```bash
DASHBOARD_PASSCODE=choose_a_local_dashboard_passcode
```

When `DASHBOARD_PASSCODE` is set, the Streamlit cockpit requires that passcode before showing the dashboard. If it is not set, the dashboard remains usable but `security check` will warn you.

To schedule the email for 4:45 AM on macOS:

```bash
cd "/Users/davidfiore/Documents/Hedge Fund/current-ai-hedge-fund"
chmod +x scripts/run_morning_email.sh
mkdir -p ~/Library/LaunchAgents
cp automation/com.dfiore.ai-hedge-fund.morning-brief.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.dfiore.ai-hedge-fund.morning-brief.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.dfiore.ai-hedge-fund.morning-brief.plist
```

Logs are written to `reports/morning_brief/automation.log`, `launchd.out.log`, and `launchd.err.log`.

The `macro today` command:

1. Pulls major index, volatility, rate, dollar, commodity, and crypto proxies.
2. Pulls official FRED macro series when `FRED_API_KEY` is configured.
3. Pulls economic calendar events from Trading Economics when `TRADING_ECONOMICS_API_KEY` is configured, otherwise from the free FRED release calendar when `FRED_API_KEY` is configured.
4. Ranks sector ETFs versus SPY.
5. Produces a macro score, market regime, confidence score, official macro section, economic calendar section, and sector rotation table.
6. Saves the report in `reports/market_intelligence/`.

The `technical` command:

1. Pulls daily OHLCV history.
2. Calculates moving averages, RSI, MACD, ATR, support/resistance, and relative strength vs SPY.
3. Produces entry trigger, stop/invalidation, targets, reward/risk, stance, confidence, risks, and missing information.
4. Saves the report in `reports/technical/`.

The `risk` command:

1. Reads the technical setup, earnings calendar, economic calendar, and portfolio exposure for a ticker.
2. Reads the simulated trade journal for daily/weekly realized P&L and current open planned risk.
3. Validates entry, stop, target, reward-to-risk, earnings proximity, macro event risk, correlated exposure, loss limits, and open-risk limits.
4. Calculates paper position size from the risk policy.
5. Blocks long simulated trades when Technical stance is bearish or no_trade.
6. Issues vetoes, warnings, missing information, or approval for paper trade.
7. Saves the report in `reports/risk/`.

The `cio` command:

1. Runs Macro, Technical, Risk, news, options, backtest, and memory/thesis context.
2. Logs each agent's structured output to local memory.
3. Generates a conflict memo when agents disagree.
4. Runs a Devil's Advocate countercase to challenge the setup.
5. Respects Risk Manager vetoes.
6. Uses overnight news stance, catalyst scoring, and analyst-action clues without letting headlines override risk controls.
7. Produces one pre-market decision: paper trade, watch only, or no trade.
8. Saves the report in `reports/cio/`.

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
DASHBOARD_PASSCODE=choose_a_local_dashboard_passcode
DASHBOARD_PORT=8501
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Next Build Targets

- Data-quality scoring, provider status checks, and source conflict detection
- Daily market regime intelligence
- Capital flow and sector rotation analysis
- Watchlist and ticker comparison workflows
- Portfolio and trade construction layers

See `docs/data_quality_systems_research.md` for the current data-provider research and implementation roadmap.

## Data Upgrade To-Do List

- [x] Add FRED official macro data integration. Requires `FRED_API_KEY` in `.env` to activate live official macro series.
- [x] Add economic calendar integration. Uses `TRADING_ECONOMICS_API_KEY` when available, otherwise uses the free FRED release calendar through `FRED_API_KEY`.
- [x] Add starter news / analyst feed. Uses Finnhub company news and recommendation trends when `FINNHUB_API_KEY` is configured, with Yahoo starter headlines and analyst actions as fallback.
- [x] Add better market data provider. Uses Tiingo latest equity prices when `TIINGO_API_KEY` is configured, or Alpaca latest stock bars when Alpaca keys are configured, with Yahoo as fallback.
- [ ] Add options data provider. Interim enhanced starter layer uses Yahoo/yfinance chains for watch-only put/call, IV, liquidity, and unusual-activity clues.
- [x] Add local data cache. Stores successful provider JSON responses under ignored `data_cache/` with short TTLs and stale fallback where appropriate.
- [ ] Add provider comparison checks.
