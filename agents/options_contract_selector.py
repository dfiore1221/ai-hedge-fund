from datetime import date, datetime

from agents.options_flow import analyze_options_flow
from agents.strategy_router import load_strategy_policy


def select_defined_loss_contract(symbol, strategy, equity, policy=None):
    policy = policy or load_strategy_policy()
    gate = policy.get("options_contract_gate") or {}
    option_type = "put" if strategy == "long_put" else "call"
    report = analyze_options_flow(symbol, max_expirations=8)
    if report.get("error"):
        return {"status": "blocked", "reason": report.get("error"), "options_report": report}

    candidates = []
    for expiration in report.get("expiration_summaries", []):
        dte = int(expiration.get("days_to_expiration") or 0)
        if not gate.get("minimum_days_to_expiration", 21) <= dte <= gate.get("maximum_days_to_expiration", 90):
            continue
        key = "top_put_contracts" if option_type == "put" else "top_call_contracts"
        for contract in expiration.get(key, []):
            if contract_passes(contract, gate):
                candidates.append({**contract, "days_to_expiration": dte})

    if not candidates:
        return {
            "status": "blocked",
            "reason": "No contract passed volume, open-interest, spread, IV, DTE, and moneyness gates.",
            "options_report": report,
        }

    candidates.sort(key=lambda row: (
        row.get("spread_pct") or 99,
        abs(row.get("moneyness") or 0),
        -int(row.get("open_interest") or 0),
    ))
    contract = candidates[0]
    ask = float(contract.get("ask") or 0)
    premium_budget = float(equity or 0) * float(gate.get("maximum_single_idea_premium_pct", 0.0025))
    contracts = int(premium_budget // (ask * 100)) if ask else 0
    if contracts < 1:
        return {
            "status": "blocked",
            "reason": f"One contract costs ${ask * 100:,.2f}, above the ${premium_budget:,.2f} premium budget.",
            "options_report": report,
        }
    profit_pct = float((policy.get("families") or {}).get(strategy, {}).get("profit_take_pct", 50)) / 100
    stop_pct = float((policy.get("families") or {}).get(strategy, {}).get("stop_loss_pct", 50)) / 100
    return {
        "status": "eligible",
        "symbol": symbol.upper(),
        "strategy": strategy,
        "direction": "bearish" if option_type == "put" else "bullish",
        "option_type": option_type,
        "contract_symbol": contract.get("contract_symbol"),
        "expiration": contract.get("expiration"),
        "days_to_expiration": contract.get("days_to_expiration"),
        "strike": contract.get("strike"),
        "contracts": contracts,
        "entry_premium": round(ask, 4),
        "target_premium": round(ask * (1 + profit_pct), 4),
        "stop_premium": round(ask * (1 - stop_pct), 4),
        "premium_at_risk": round(ask * contracts * 100, 2),
        "quote": contract,
        "provider": report.get("provider"),
        "warning": report.get("warning"),
    }


def contract_passes(contract, gate):
    spread = contract.get("spread_pct")
    iv = contract.get("implied_volatility")
    moneyness = contract.get("moneyness")
    return all([
        float(contract.get("ask") or 0) > 0,
        int(contract.get("volume") or 0) >= int(gate.get("minimum_volume", 100)),
        int(contract.get("open_interest") or 0) >= int(gate.get("minimum_open_interest", 500)),
        spread is not None and float(spread) <= float(gate.get("maximum_bid_ask_spread_pct", 0.15)),
        iv is not None and float(iv) <= float(gate.get("maximum_implied_volatility", 1.2)),
        moneyness is not None and abs(float(moneyness)) <= float(gate.get("maximum_moneyness_distance", 0.1)),
    ])
