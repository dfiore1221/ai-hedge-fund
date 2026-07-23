# AIFundOS System Roles

Version: 1.0

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

## Governance Rules

These enforce discipline:

- Watch-only mode unless explicitly paper-approved.
- Human approval required for paper ledger actions.
- Market-hours guard for rebalance execution realism.
- Risk veto overrides bullish news, technicals, or thesis.
- Data gate limits action when evidence is incomplete.
- Earnings/event guardrails can block new swing entries.
- Core ETF sleeve uses policy bands and cash-reserve rules.

## Memory And Learning

These should evaluate process quality over time:

- Research memory
- Committee question feedback
- Daily setup review
- Weekly review
- Feedback loop / outcome scoring
- Lessons on closed simulated trades

## Tools And Workflows

These operate the system but are not Committee voices:

- Morning brief
- Dashboard
- Paper ledger
- Automatic paper fills
- Core rebalance approval workflow
- Position manager
- Intraday monitor
- Email retry queue
- Desktop net liquidation ticker

## Presentation Rule

The morning brief should lead with:

1. Decision
2. Why
3. What would change the decision
4. Risk / invalidation
5. Missing data

Detailed evidence and debate should remain available in logs and memory, but the user-facing brief should not read like every module is speaking equally loudly.
