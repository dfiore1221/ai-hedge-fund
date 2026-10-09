# AIFundOS System Roles

Version: 1.1

## Purpose

AIFundOS should stay quiet, disciplined, and understandable as it grows.

The Committee is not every module in the system. The Committee is the small set of decision roles that synthesize evidence and make or block paper-trade decisions. Other modules should feed evidence, enforce governance, update memory, or operate the paper portfolio.

## Role Rule

Use this rule before adding or naming any new component:

- If it makes or challenges a decision, it may be a Committee agent.
- If it supplies facts, context, signals, or calculations, it is a shared data layer.
- If it enforces discipline, limits, approvals, or safety, it is governance.
- If it records outcomes or improves future judgment, it is memory and learning.
- If it helps track, execute, display, or notify, it is a tool or workflow.

## Committee Agents

These are the primary voices in the investment decision process:

- Macro: defines the market backdrop and regime.
- Technical: evaluates setup, trend, levels, entries, stops, and targets.
- Risk: sizes or vetoes a setup using portfolio and event guardrails.
- CIO: makes the final paper-trade, conditional, watchlist, no-trade, or needs-data decision.
- Devil's Advocate: speaks only when there is meaningful disagreement, high risk, or a decision that needs challenge.

## Shared Data Layers

These are evidence sources. They should not sound like extra voting members:

- Data Quality: provider health, coverage, and data gates.
- Macro Event Interpretation: CPI, PCE, jobs, retail sales, sentiment, credit, rates, and event-risk translation.
- News Intelligence: overnight headlines, analyst actions, and catalysts.
- Options Flow: starter positioning/liquidity evidence until an institutional options feed exists.
- Backtest / Quant: historical expectancy evidence attached to a setup.
- SEC / Fundamentals / Research Facts: primary-source company evidence.
- Economic Calendar: event risk and timing.
- Intraday Market Surveillance: configured-watchlist plus bounded U.S.-listed news discovery, quote, volume, setup-level, and news-change detection. It wakes the Committee when evidence changes; it is not another voting agent.

## Governance Rules

These enforce discipline:

- The Strategy Router is a deterministic governance workflow, not another Committee voice. It converts the Committee decision into an eligible strategy family, horizon, and vehicle without bypassing Risk or data gates.

- Autonomous paper mode may plan, enter, manage, and close qualifying simulated positions without human approval.
- Watch-only mode remains the fallback when autonomy, data, risk, or tradability gates fail.
- Human approval remains available for manual overrides and discretionary paper orders.
- Market-hours guard for rebalance execution realism.
- Risk veto overrides bullish news, technicals, or thesis.
- Data gate limits action when evidence is incomplete.
- Earnings/event guardrails can block new swing entries.
- Core ETF sleeve uses policy bands and cash-reserve rules.
- Autonomous risk, order count, position size, and experiment-sleeve parameters may adapt only inside `framework/autonomy_policy.json` bounds.
- Strategy selection is governed by `framework/strategy_policy.json`; enabled paper families are swing, position, long call, and long put.
- Scalping and same-day trading remain blocked until their intraday data, spread/slippage, monitoring-frequency, and forced-close requirements pass.
- Long calls and long puts require a contract-level liquidity check and known premium-at-risk. Multi-leg spreads and naked options are not autonomously executable.
- Live brokerage execution, live-money credentials, unlimited-loss structures, hidden actions, and removal of hard risk limits are prohibited.
- The Human Escalation Monitor must notify the operator when evidence suggests a source-code change, paid data review, risk-policy counterfactual, or real-money readiness review may be beneficial.
- An escalation is not approval. Only the human operator may authorize those restricted changes.

## Memory And Learning

These should evaluate process quality over time:

- Research memory
- Committee question feedback
- Daily setup review
- Weekly review
- Feedback loop / outcome scoring
- Lessons on closed simulated trades
- Benchmark attribution versus SPY and a configurable TSP-style C/S proxy
- Evidence milestone tracking toward at least 30 closed paper trades, then 50
- Outcome scoring by strategy family so a weak method can be paused without contaminating every other method
- Options expectancy and realized premium P&L scored separately from equity trade expectancy
- Strategy/source review when a mature review sample is not improving the learning score

## Tools And Workflows

These operate the system but are not Committee voices:

- Morning brief
- Dashboard
- Paper ledger
- Automatic paper fills
- Core rebalance approval workflow
- Position manager
- Intraday monitor
- Intraday opportunity engine: scans the full watchlist plus up to 20 U.S.-listed symbols discovered from fresh Benzinga market news every 15 minutes during regular market hours and requests event-driven Committee review.
- Five-minute paper execution lane: checks planned entries and open-position exits independently of slower research workflows.
- Email retry queue
- Desktop net liquidation ticker
- Autonomous paper planner and execution loop
- Automation watchdog and private always-on worker
- Human escalation notification and approval workflow

## Presentation Rule

The morning brief should lead with:

1. Decision
2. Why
3. What would change the decision
4. Risk / invalidation
5. Missing data

Detailed evidence and debate should remain available in logs and memory, but the user-facing brief should not read like every module is speaking equally loudly.
