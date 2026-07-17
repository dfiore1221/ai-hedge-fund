# Data Quality Systems Research

Updated: 2026-07-17

## Goal

Upgrade the data layer so the agents can clearly separate:

1. Bad setup
2. Bad data
3. Missing data
4. Stale data
5. Conflicting data

The system should not approve simulated trades unless the data packet is complete enough for the decision tier. It should still surface conditional/watchlist ideas when the opportunity is interesting but the data packet is incomplete.

## Current Weakness

The project currently uses Yahoo Finance via `yfinance`, SEC EDGAR, a basic earnings calendar, basic news/options/backtest modules, local memory, and a local trade journal. This is enough for prototyping, but not enough to treat morning recommendations as reliable without stronger source checks.

Main issue: Yahoo Finance is useful but should not be the single source of truth for prices, corporate actions, options, earnings, and recommendations.

## Recommended Architecture

Use a layered provider model:

1. Primary-source layer: official/public sources where possible.
2. Market-data layer: authenticated market data API for prices, bars, options, reference data, and corporate actions.
3. Event/news layer: earnings, analyst actions, economic calendar, market-moving news.
4. Validation layer: compare provider values, timestamps, and completeness.
5. Cache layer: local normalized snapshots so the same morning run is reproducible.

Each data packet should include:

- provider
- endpoint
- retrieved_at
- as_of
- freshness/staleness
- status: ok, missing, stale, partial, error
- confidence
- raw citation/reference

## Core Principle

The system should grade data before it grades trades.

A morning brief should not treat every missing field as a bearish signal. Missing options flow, stale earnings dates, bad corporate-action data, or conflicting prices should push the idea into `needs_data` or `watch_only`, not into a false "neutral" conclusion. This keeps the agents from sounding overly conservative when the real issue is incomplete inputs.

## Data Domains We Need

For a simulated trade recommendation, the CIO committee should have a minimum packet:

1. Price and volume: current quote, prior close, daily bars, gap, volume, average volume, ATR.
2. Corporate actions/reference: ticker validity, exchange, splits, dividends, ETF/company classification.
3. Earnings and events: next earnings date, major economic events, FDA/defense/energy/crypto-specific events when relevant.
4. News and analyst actions: overnight headlines, rating changes, target changes, deal/regulatory headlines.
5. Options and volatility: optionability, implied volatility, options volume/open interest, unusual activity when available.
6. Macro and capital flows: index regime, sector rotation, rates, dollar, oil, crypto, volatility, positioning.
7. Historical setup evidence: recent similar technical setups, simple forward-return expectancy, win/loss distribution.
8. Portfolio context: current simulated exposure, correlated names, max risk, sector concentration.

Minimum standard:

- If price/bars are missing or stale, no actionable idea.
- If earnings date is missing, no swing trade through the expected event window.
- If news is missing, recommendations must be labeled `data_incomplete`.
- If options data is missing, stock/ETF ideas can still be reviewed, but options-specific ideas cannot be approved.
- If two providers disagree materially, show the conflict instead of hiding it.

## Provider Matrix

### Official / Public Foundation

#### SEC EDGAR

Best for 10-K, 10-Q, 8-K, Form 4, company facts, filings history, and primary-source fundamentals.

Fit: keep as primary source for company research and verified fundamentals. Add better XBRL normalization and reconciliation checks.

Source: https://www.sec.gov/search-filings/edgar-application-programming-interfaces

#### FRED

Best for rates, inflation, monetary data, credit spreads, employment, GDP, and other macro series.

Fit: use as the macro agent's primary time-series source for rates, inflation, and growth. Add ALFRED/vintage dates later for point-in-time macro backtesting.

Source: https://fred.stlouisfed.org/docs/api/fred/

#### BLS

Best for CPI, PPI, unemployment, payroll-related labor series.

Fit: use directly for labor/inflation releases when official data matters more than aggregator convenience.

Source: https://www.bls.gov/bls/api_features.htm

#### BEA

Best for GDP, NIPA, income, industry GDP, and regional economic data.

Fit: use for quarterly macro context, GDP revisions, and industry-level macro trends.

Source: https://apps.bea.gov/api/signup/

#### EIA

Best for oil, natural gas, electricity, nuclear/power, and fuel data.

Fit: important for energy, nuclear/power, electrification, and commodities buckets.

Source: https://www.eia.gov/opendata/documentation.php

#### CFTC Commitments of Traders

Best for futures positioning, commodities, dollar/rates proxies, and crowded macro trades.

Fit: add to macro/capital-flow agent for positioning context.

Sources:

- https://www.cftc.gov/MarketReports/CommitmentsofTraders/AbouttheCOTReports/index.htm
- https://publicreporting.cftc.gov/stories/s/User-s-Guide/p2fg-u73y/

### Market Data APIs

#### Alpaca Market Data

Best for stocks, options, crypto, historical bars, real-time/websocket data, and possible paper-trading workflow.

Fit: best near-term single vendor if we want price data, options basics, crypto, and future paper-trading integration in one ecosystem.

Source: https://docs.alpaca.markets/us/docs/about-market-data-api

#### Polygon.io

Best for US equities, options, forex, crypto, historical bars, trades, reference data, and market news.

Fit: strong developer market-data provider, especially if we want robust equities/options history without brokerage coupling.

Sources:

- https://polygon.io/docs/rest/stocks/overview
- https://polygon.io/docs/options/get_v3_trades__optionsticker

#### Databento

Best for institutional-grade historical/live data, futures, OPRA, equities, tick/order-book data, and serious backtesting.

Fit: later-stage upgrade for high-quality backtesting, futures/macro, OPRA, and intraday market microstructure.

Source: https://databento.com/pricing/

#### Nasdaq Data Link

Best for alternative datasets, tables, premium datasets, some free/open data, Python/Excel/SQL workflows.

Fit: good marketplace for specialized datasets once we know which alpha inputs matter.

Sources:

- https://docs.data.nasdaq.com/
- https://docs.data.nasdaq.com/docs/getting-started

### Fundamentals, Earnings, News, Estimates

#### Finnhub

Best for company news, earnings calendar, financials, estimates, news sentiment, and economic calendar on premium tiers.

Fit: good practical API candidate for earnings calendar, company news, and estimates.

Source: https://finnhub.io/docs/api

#### Financial Modeling Prep

Best for standardized financial statements, earnings calendar, stock news, press releases, analyst/market endpoints.

Fit: useful convenience layer for standardized fundamentals, earnings calendar, and quick comps. Reconcile with SEC for audited truth.

Sources:

- https://site.financialmodelingprep.com/developer/docs/quickstart
- https://site.financialmodelingprep.com/contact

#### Intrinio

Best for standardized/as-reported financials, company news, stock prices, options, estimates, ETF data.

Fit: strong data vendor if we want one place for fundamentals, options, estimates, and news.

Sources:

- https://intrinio.com/docs
- https://docs.intrinio.com/documentation/api_v2/getting_started

#### Benzinga

Best for market-moving news, analyst ratings, earnings, calendars, unusual options activity, why-is-it-moving, government trades, and FDA calendar.

Fit: best candidate for the morning brief's "overnight news and analyst action" upgrade.

Sources:

- https://docs.benzinga.com/ws-reference/overview
- https://www.benzinga.com/apis/cloud-product/analyst-ratings-api/
- https://www.benzinga.com/apis/

### Economic Calendar

#### Trading Economics

Best for economic calendar, global macro indicators, forecasts, rates, commodities, exchange rates, bonds, and company financials.

Fit: best candidate for our missing economic calendar/event-risk layer.

Sources:

- https://docs.tradingeconomics.com/get_started/
- https://tradingeconomics.com/api/calendar.aspx
- https://docs.tradingeconomics.com/economic_calendar/schema/

### Options Data

#### Tradier

Best for options chains, greeks, brokerage/paper trading workflow.

Fit: strong if we want broker plus paper trading plus options chain integration.

Sources:

- https://docs.tradier.com/docs/market-data
- https://docs.tradier.com/reference/brokerage-api-markets-get-options-chains
- https://docs.tradier.com/docs/trading

#### ORATS

Best for options analytics, delayed/live/historical/intraday options, implied volatility, greeks, proprietary indicators.

Fit: best specialized options analytics source for options-flow and implied-volatility agent.

Sources:

- https://orats.com/docs
- https://docs.orats.io/datav2-api-guide/data.html

#### Cboe DataShop / OPRA

Best for official options trades/quotes history and OPRA-disseminated options data.

Fit: later-stage institutional options research/backtesting.

Source: https://datashop.cboe.com/option-trades

### Open News

#### GDELT

Best for global news monitoring, broad media signals, open-source event/news data.

Fit: good fallback/breadth source, not a replacement for market-moving financial news.

Source: https://www.gdeltproject.org/

## Recommended Stack By Phase

### Phase 1: Harden Free/Public Data

Implement first:

- SEC EDGAR as primary company data.
- FRED for macro time series.
- BLS and BEA for official inflation/labor/GDP.
- EIA for energy/power.
- CFTC COT for positioning.
- Keep Yahoo Finance only as a convenience/chart fallback.

Why:

- Low cost.
- High trust for macro/fundamentals.
- Immediately improves "bad setup" vs "bad data" separation.
- Gives the macro, risk, and CIO agents an official baseline before paid feeds are added.

Expected project work:

- Normalize official-source snapshots into one local cache.
- Store source, retrieval time, release time, as-of date, and revision/vintage where available.
- Add `data_health` output to every morning brief.
- Add warnings when a conclusion is driven by missing data.

### Phase 2: Add One Paid Market Data Provider

Recommended shortlist:

1. Alpaca Market Data if future paper trading/broker integration matters.
2. Polygon.io if clean developer-first equities/options market data matters most.
3. Databento if serious intraday/futures/options backtesting becomes the priority.

Near-term recommendation: evaluate Alpaca and Polygon side-by-side. Pick one primary market-data provider and keep Yahoo as fallback only.

My current lean:

- Choose Alpaca if paper trading integration is a near-term priority.
- Choose Polygon if pure data quality and flexible market-data coverage matter more than brokerage workflow.
- Delay Databento until we are doing serious intraday, futures, OPRA, or institutional backtests.

### Phase 3: Add Event/News Provider

Recommended shortlist:

1. Benzinga for analyst ratings, market-moving news, calendars, unusual options, and morning-trader workflow.
2. Finnhub for a practical API that covers company news, earnings calendar, and estimates.
3. Trading Economics for economic calendar and global macro releases.

Near-term recommendation: add Trading Economics for economic calendar and either Benzinga or Finnhub for news/earnings/analyst actions.

My current lean:

- Trading Economics for macro calendar and global event risk.
- Benzinga if the morning brief needs trader-grade overnight news, analyst actions, unusual options, FDA calendar, and "why moving" style context.
- Finnhub if we want a lower-friction, broad API for news, earnings, estimates, and company-level data.

### Phase 4: Options Upgrade

Recommended shortlist:

1. Tradier if we want brokerage/paper trading plus options chains.
2. ORATS if we want options analytics quality.
3. Cboe DataShop/OPRA or Databento if institutional options history becomes essential.

## Data Quality Score

Every symbol/morning packet should get a `data_quality_score` from 0 to 100:

- Price/bars present and fresh: 25
- Corporate actions/reference data present: 10
- Earnings/event calendar present: 15
- News/analyst data present: 15
- Options data present when symbol has options: 10
- Macro/event context present: 10
- Provider agreement/checks passed: 10
- No critical errors/staleness: 5

Decision rules:

- 85+: eligible for simulated trade review.
- 70-84: conditional/watchlist only.
- 50-69: needs data or watchlist only.
- Below 50: no actionable recommendation.

## Provider Agreement Rules

When more than one source is available, the validation layer should compare:

- Last price and prior close.
- Daily OHLCV bars.
- Market cap and shares outstanding.
- Next earnings date.
- Split/dividend adjustment.
- SEC-derived financial values versus aggregator-derived financial values.
- News timestamp and duplicate headline clustering.
- Option chain availability and stale chain detection.

Suggested thresholds:

- Price conflict above 0.5% during market hours: flag `provider_conflict`.
- Prior close conflict above 0.25%: flag `provider_conflict`.
- Volume conflict above 5% after the close: flag `provider_conflict`.
- Earnings date conflict: require manual/secondary confirmation before swing-trade approval.
- SEC financial conflict: prefer SEC for audited/as-filed truth, but retain aggregator values for speed and comps.
- News missing for a stock with a large premarket move: no actionable recommendation until news is checked.

## Best Data-System Candidates

### Immediate Free/Public Core

Use now:

- SEC EDGAR for filings, company facts, and as-filed fundamentals.
- FRED for rates, inflation, credit, employment, GDP, and financial conditions.
- BLS for official CPI/PPI/labor detail.
- BEA for GDP, income, NIPA, and industry macro.
- EIA for oil, gas, electricity, nuclear/power, and energy inventory context.
- CFTC COT for positioning.

This gives the agents a better macro and fundamental foundation without vendor spend.

### First Paid Test

Run a 30-day provider bakeoff:

1. Alpaca vs Polygon for prices/bars/reference/options basics.
2. Trading Economics for economic calendar.
3. Benzinga vs Finnhub for news, earnings, analyst actions, and morning catalysts.

Measure each vendor on:

- Coverage of our watchlist.
- Freshness before 5:00 AM ET.
- Missing fields.
- API reliability.
- Ease of Python integration.
- Cost for personal/research use.
- Terms around storage, redistribution, and email summaries.
- Whether outputs improve CIO recommendations.

### Later Institutional Upgrade

Consider later:

- Databento for serious intraday/futures/options market-data history.
- ORATS for options analytics and implied-volatility research.
- Cboe DataShop/OPRA for official options history.
- Intrinio or Nasdaq Data Link for premium fundamentals, estimates, and specialty datasets.

Do not buy these until the agents prove which data domains drive better simulated trade outcomes.

## Implementation Plan

1. Add `data/provider_status.py`. Status: deferred into `data/data_quality.py` until provider complexity requires a separate module.
2. Add `data/data_quality.py`. Status: implemented.
3. Add `python3 main.py data-health today`. Status: implemented.
4. Add data-quality section to morning brief. Status: implemented as a lightweight gate snapshot.
5. Add dashboard Data Quality tab.
6. Add provider config to `.env.example`. Status: implemented.
7. Keep all raw provider responses cached locally under ignored `data_cache/`. Status: ignore rule added; cache writer not implemented yet.

## Data-Health Command Design

`python3 main.py data-health today` should print:

- Active providers.
- Missing provider keys.
- Last successful retrieval by domain.
- Watchlist coverage percentage.
- Number of stale symbols.
- Number of provider conflicts.
- Top symbols blocked by bad data.
- Whether the morning brief is eligible for simulated-trade recommendations.

Example output:

```text
Data Health: Conditional
Price/bars: 92% coverage, 4 stale symbols
Earnings calendar: 71% coverage, 18 missing dates
News: 64% coverage, Benzinga not configured
Options: 48% coverage, options provider not configured
Macro: OK, FRED/BLS/EIA present
Decision: allow watchlist ideas; block options trades; require data_incomplete labels
```

## Dashboard Additions

Add a `Data Quality` tab with:

- Provider status cards.
- Watchlist coverage table.
- Stale/missing/conflicting data alerts.
- Per-symbol data quality score.
- Provider conflict detail.
- "Why this recommendation was blocked" explanation.

This is important because the operator should see whether the system is conservative because the setup is bad or because the data packet is incomplete.

## Immediate Recommendation

Do not buy a big institutional data stack yet. First, build the provider abstraction and data-health scoring with free official sources. Then run Alpaca vs Polygon vs Finnhub/Benzinga tests on the exact data packets our agents need.

Practical next build:

1. Implement the data health framework with current providers. Status: implemented.
2. Add FRED official macro series. Status: implemented; requires `FRED_API_KEY` in `.env` to activate live official macro data.
3. Add provider placeholders and local cache.
4. Add data-quality section to morning brief.
5. Run one week of morning briefs and record which missing fields block decisions.
6. Use that evidence to decide whether Alpaca, Polygon, Benzinga, Finnhub, or Trading Economics should be the first paid provider.
