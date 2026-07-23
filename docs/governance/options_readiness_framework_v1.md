# AIFundOS Options Readiness Framework

Version: 1.0

## Purpose

This framework prepares AIFundOS for options research before a paid options provider is connected.

The goal is not to approve real options trades now. The goal is to teach the system and the user how to evaluate options setups, track paper-options decisions, and make the future Intrinio or Tradier integration useful from day one.

## Role

Options readiness is a governance and workflow layer, not a new Committee agent.

The options layer supplies evidence about liquidity, implied volatility, open interest, contract selection, and risk. The Committee can use that evidence, but Risk and CIO still decide whether a paper setup is acceptable.

## Beginner Strategy Menu

Allowed first strategies:

- Long call: bullish, max loss is premium paid.
- Long put: bearish, max loss is premium paid.
- Call debit spread: bullish, max loss is net debit paid.
- Put debit spread: bearish, max loss is net debit paid.

Avoid for now:

- Naked calls
- Naked puts
- Short straddles
- Short strangles
- Ratio spreads
- Complex multi-leg strategies

## Required Checklist

Before AIFundOS marks an options idea as paper-options ready, it should check:

- Liquidity: volume, open interest, and bid/ask spread.
- Contract structure: expiration, strike, premium, max loss, and break-even.
- Greeks: delta, theta, vega, and gamma when a provider supports them.
- Implied volatility: whether the option is unusually expensive or cheap.
- Event risk: earnings, FDA decisions, macro events, or major news.
- Stock setup: options should not override a weak stock setup.
- Risk sizing: max loss must fit account and options-sleeve limits.
- Exit plan: profit target, stop rule, and time stop.

## Paper-Options Ledger

The local paper-options journal tracks:

- Symbol and option contract
- Strategy and direction
- Expiration, strike, and option type
- Contracts and multiplier
- Entry premium, current premium, exit premium
- Premium paid, max loss, break-even
- Target premium and stop premium
- Status, source, thesis, notes, and lessons

This journal does not place trades and does not require a paid data provider.

## Provider Trial Plan

When an Intrinio trial starts, test it immediately against this checklist:

1. Fetch option chains for the highest-priority watchlist names.
2. Verify bid, ask, last, volume, open interest, implied volatility, and Greeks.
3. Fetch historical option prices.
4. Compare provider data against Yahoo starter snapshots.
5. Score liquidity and contract quality.
6. Record sample paper-options trades.
7. Review whether options evidence improves Committee decisions.

## Current Limitation

Until a paid options provider is connected, AIFundOS can track paper-options ideas and use starter Yahoo chain context, but it should not treat options data as execution-grade.
