# AIFundOS Infrastructure And Strategy Roadmap

Updated: 2026-10-08

## Objective

Move AIFundOS from a laptop-dependent autonomous paper swing system to an always-on, event-driven research and paper-execution platform. Strategy expansion must follow demonstrated infrastructure and data readiness rather than precede it.

## Current State

### Operational

- Autonomous paper-only swing and position workflow.
- Morning brief, risk review, portfolio governor, trade journal, paper ledger, memory, email alerts, and end-of-day review.
- Desktop watchdog every 10 minutes during the trading session.
- Paper entry/exit checks every 5 minutes.
- Tiingo authenticated equity data with Yahoo fallback.
- Benzinga, Finnhub, FRED, SEC EDGAR, and Quiver integrations.
- Docker worker/dashboard deployment package.
- Data-quality gate currently passes for the swing workflow.

### Not Yet Execution-Grade

- The Mac laptop remains the active host and stops working when asleep, offline, or traveling.
- Equity data is polled rather than consumed as a continuous streaming feed.
- Provider checks can fall back to prior closes or cached observations.
- Options data is starter-level and does not provide dependable OPRA-quality quotes, greeks history, IV history, or flow.
- No borrow, locate, SSR, margin, or borrow-fee model exists for short selling.
- No continuous event engine supervises same-session strategies.
- Costs and lifecycle handling remain incomplete for dividends, interest, borrow fees, assignment, and exercise.
- Tactical evidence remains below the 30-completed-trade minimum gate.

## Phase 1: Always-On Private Host

Deploy one authoritative worker on a wired Linux mini PC, Mac mini, or private cloud host.

Required controls:

- Wired Ethernet and UPS protection for a home host.
- Docker Compose with automatic restart.
- Private VPN or authenticated tunnel; never expose Streamlit directly to the public internet.
- Encrypted secret storage and encrypted backups.
- Persistent portfolio, memory, reports, and data-cache volumes.
- Time synchronization and America/New_York market clock.
- Health alerts for missed runs, stale files, failed email, failed data providers, and disk pressure.
- Exactly one ledger-writing worker. Dashboard replicas must remain read-only.

Definition of done:

- 20 consecutive U.S. market sessions with at least 99.5% scheduled-service availability.
- No missed morning brief, market-session worker, end-of-day review, or Friday review caused by host sleep or Wi-Fi loss.
- Successful restart recovery without duplicate fills or ledger divergence.
- A tested encrypted backup and restore of portfolio and memory data.

## Phase 2: Streaming Equity Data

Add a primary streaming provider and retain an independent provider for validation.

Required normalized fields:

- Symbol, exchange, event timestamp, receive timestamp, and sequence identifier where available.
- Last trade, bid, ask, spread, size, cumulative volume, and session status.
- One-minute OHLCV bars and prior adjusted close.
- Split, dividend, symbol-change, and trading-halt context.

Required controls:

- Reject stale or out-of-order quotes.
- Reject execution when bid/ask or timestamp is missing.
- Detect material disagreement between primary and validation providers.
- Persist normalized market events for reproducible review and backtesting.
- Separate regular-session, extended-hours, and closed-market observations.

Definition of done:

- At least 99.9% complete one-minute bars during regular sessions for the active universe.
- Quote age and provider status are visible on every execution decision.
- Twenty sessions without fills based on stale, cached, or prior-close data.

## Phase 3: Event-Driven Paper Execution

Replace interval-only execution with a durable pipeline:

`market event -> candidate update -> strategy gate -> risk gate -> working order -> simulated fill -> position supervision -> exit -> review`

Required controls:

- Idempotent order and fill identifiers.
- Duplicate-order protection across crashes and restarts.
- Gap-through-entry veto and mandatory re-underwriting after abnormal adverse gaps.
- Spread, slippage, partial-fill, and liquidity assumptions.
- Trading-halt and missing-data behavior.
- Forced same-day liquidation for day strategies.
- Restart recovery and ledger reconciliation.
- Complete funnel attribution from discovery through realized P&L.

Definition of done:

- Zero duplicate fills across restart tests.
- Every fill traceable to the exact quote, strategy decision, risk decision, and working order.
- Replay tests produce the same ledger result from the same event stream.

## Phase 4: Strategy-Specific Data

### Options

Require OPRA-quality chains, bid/ask, greeks, IV and IV history, volume, open interest, contract adjustments, and conservative multi-leg synchronization. Model assignment, exercise, expiration, and spread slippage before enabling multi-leg strategies.

### Equity Shorts

Require borrow availability, hard-to-borrow status, estimated borrow cost, short-sale restriction status, margin, dividends owed, recall risk, and gap-risk controls.

## Phase 5: Staged Strategy Introduction

1. Continue swing and position paper experiments under current risk constraints.
2. Add equity-short signals in shadow mode with no fills.
3. Enable small defined-risk paper shorts after the short-data gate passes.
4. Enable long calls and long puts after the options-data and lifecycle gates pass.
5. Enable day strategies only after 20 reliable always-on sessions and event-engine verification.
6. Consider scalping last. Keep it disabled unless sub-minute supervision and realistic spread/slippage evidence justify it.

## Evidence Gates

Infrastructure readiness and strategy edge are separate questions. Passing one does not imply the other.

- Keep tactical risk constrained until at least 30 autonomous trades close.
- Evaluate expectancy after spread, slippage, and modeled costs.
- Track win rate, average win/loss, drawdown, profit factor, exposure, and benchmark-relative return by strategy family.
- Require out-of-sample or walk-forward evidence before increasing size.
- A strategy that lacks its required data remains blocked even when the overall data-quality score is high.

## Recommended Next Build Order

1. Choose and provision the always-on host.
2. Deploy the existing Docker worker and dashboard privately.
3. Prove uptime and single-writer ledger safety.
4. Select and integrate the streaming equity provider.
5. Build normalized market-event storage and freshness gates.
6. Implement the event-driven paper execution engine.
7. Add short and options data in that order of operational simplicity.
