import json
from datetime import datetime
from pathlib import Path

from agents.technical_analyst import analyze_technical_setup
from data.earnings_calendar import get_earnings_calendar
from data.portfolio import analyze_portfolio_exposure


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "risk"
RISK_POLICY_PATH = PROJECT_ROOT / "framework" / "risk_policy.json"


def load_risk_policy():
    return json.loads(RISK_POLICY_PATH.read_text(encoding="utf-8"))


def evaluate_trade_risk(ticker, technical_report=None, policy=None):
    ticker = ticker.upper().strip()
    policy = policy or load_risk_policy()
    technical_report = technical_report or analyze_technical_setup(ticker)
    earnings = get_earnings_calendar(ticker)
    portfolio_exposure = analyze_portfolio_exposure(
        ticker,
        correlated_symbols=policy["ai_semi_correlated_symbols"],
    )

    if technical_report.get("error"):
        return build_veto_report(
            ticker=ticker,
            policy=policy,
            technical_report=technical_report,
            reasons=[f"No reliable data for symbol: {technical_report['error']}"],
        )

    setup = technical_report["setup"]
    entry = setup.get("entry_trigger")
    alternative_entry = setup.get("alternative_entry")
    stop = setup.get("stop")
    target = setup.get("target_1")
    target_2 = setup.get("target_2")
    target_3 = setup.get("target_3")
    reward_to_risk_value = setup.get("reward_to_risk")
    reward_to_risk_to_target_2 = setup.get("reward_to_risk_to_target_2")
    reward_to_risk_to_target_3 = setup.get("reward_to_risk_to_target_3")
    pullback_reward_to_risk = setup.get("pullback_reward_to_risk")

    vetoes = []
    conditional_issues = []
    warnings = []

    if entry is None or stop is None or target is None:
        vetoes.append("Missing entry, stop, or target.")
    elif stop >= entry:
        vetoes.append("Invalid stop relative to entry.")

    if reward_to_risk_value is None:
        conditional_issues.append("Reward-to-risk unavailable.")
    elif reward_to_risk_value < policy["minimum_reward_to_risk"]:
        conditional_issues.append(
            f"Reward-to-risk {reward_to_risk_value:.2f} is below minimum "
            f"{policy['minimum_reward_to_risk']:.2f}."
        )

    if technical_report["stance"] == "no_trade":
        conditional_issues.append("Technical Analyst stance is no_trade.")
    elif technical_report["stance"] == "bearish":
        vetoes.append("Technical Analyst stance is bearish; long simulated trade is blocked.")

    position = calculate_position_size(entry, stop, policy)

    if position.get("error"):
        vetoes.append(position["error"])
    elif position.get("size_limited_by_exposure"):
        warnings.append(
            "Position size was capped by max single-position exposure."
        )

    if ticker in policy["ai_semi_correlated_symbols"]:
        warnings.append(
            "Ticker is in the AI/semi correlated universe; CIO/Portfolio Manager must check aggregate exposure."
        )

    earnings_days = earnings.get("days_until_earnings")
    if earnings_days is None:
        warnings.append("Earnings date unavailable; event risk is unknown.")
    elif 0 <= earnings_days <= 7:
        vetoes.append(f"Earnings are within {earnings_days} days; no new swing trade without explicit approval.")
    elif 0 <= earnings_days <= 14:
        warnings.append(f"Earnings are within {earnings_days} days; reduce confidence or require explicit approval.")

    if portfolio_exposure["correlated_exposure_pct"] > 40:
        vetoes.append(
            f"Correlated AI/semi exposure is {portfolio_exposure['correlated_exposure_pct']:.2f}%, above 40% limit."
        )
    elif portfolio_exposure["correlated_exposure_pct"] > 25:
        warnings.append(
            f"Correlated AI/semi exposure is {portfolio_exposure['correlated_exposure_pct']:.2f}%; watch concentration."
        )

    conditional_plan = build_conditional_plan(
        entry=entry,
        alternative_entry=alternative_entry,
        stop=stop,
        target=target,
        target_2=target_2,
        target_3=target_3,
        minimum_reward_to_risk=policy["minimum_reward_to_risk"],
        reward_to_risk=reward_to_risk_value,
        reward_to_risk_to_target_2=reward_to_risk_to_target_2,
        reward_to_risk_to_target_3=reward_to_risk_to_target_3,
        pullback_reward_to_risk=pullback_reward_to_risk,
    )

    if vetoes:
        decision = "veto"
        confidence = 0.35
    elif conditional_issues:
        decision = "conditional_setup" if technical_report["stance"] in {"bullish", "neutral"} else "watchlist_setup"
        confidence = 0.5 if decision == "conditional_setup" else 0.4
    else:
        decision = "approved_for_paper_trade"
        confidence = 0.65

    return {
        "agent": "Risk Manager",
        "run_id": datetime.now().strftime("%Y-%m-%d-risk"),
        "symbol": ticker,
        "decision": decision,
        "confidence": confidence,
        "policy_version": policy["version"],
        "technical_stance": technical_report["stance"],
        "entry": entry,
        "alternative_entry": alternative_entry,
        "stop": stop,
        "target_1": target,
        "target_2": target_2,
        "target_3": target_3,
        "reward_to_risk": reward_to_risk_value,
        "reward_to_risk_to_target_2": reward_to_risk_to_target_2,
        "reward_to_risk_to_target_3": reward_to_risk_to_target_3,
        "pullback_reward_to_risk": pullback_reward_to_risk,
        "position": position,
        "earnings": earnings,
        "portfolio_exposure": portfolio_exposure,
        "vetoes": vetoes,
        "conditional_issues": conditional_issues,
        "conditional_plan": conditional_plan,
        "warnings": warnings,
        "missing_information": [
            "Daily and weekly realized P&L limits are not connected yet.",
            "Economic event calendar is not connected yet.",
        ],
        "citations": technical_report.get("citations", []),
    }


def calculate_position_size(entry, stop, policy):
    if entry is None or stop is None:
        return {"error": "Cannot size position without entry and stop."}

    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return {"error": "Risk per share must be positive."}

    account_size = policy["paper_account_size"]
    max_dollar_risk = account_size * policy["max_risk_per_trade_pct"]
    max_position_value = account_size * policy["max_single_position_pct"]
    shares_by_risk = int(max_dollar_risk // risk_per_share)
    shares_by_exposure = int(max_position_value // entry)
    shares = min(shares_by_risk, shares_by_exposure)

    if shares <= 0:
        return {"error": "Position size rounds to zero under current risk limits."}

    position_value = shares * entry

    return {
        "paper_account_size": account_size,
        "max_dollar_risk": max_dollar_risk,
        "risk_per_share": risk_per_share,
        "shares": shares,
        "shares_by_risk": shares_by_risk,
        "shares_by_exposure": shares_by_exposure,
        "size_limited_by_exposure": shares < shares_by_risk,
        "position_value": position_value,
        "max_position_value": max_position_value,
    }


def build_conditional_plan(
    entry,
    alternative_entry,
    stop,
    target,
    target_2,
    target_3,
    minimum_reward_to_risk,
    reward_to_risk,
    reward_to_risk_to_target_2,
    reward_to_risk_to_target_3,
    pullback_reward_to_risk,
):
    if entry is None or stop is None or target is None:
        return {}

    max_entry = max_entry_for_reward_to_risk(stop, target, minimum_reward_to_risk)
    plan = {
        "minimum_reward_to_risk": minimum_reward_to_risk,
        "current_entry": entry,
        "max_entry_for_target_1": max_entry,
        "better_entry_required": max_entry is not None and entry > max_entry,
        "suggested_entry": max_entry if max_entry is not None and entry > max_entry else entry,
        "use_second_target": (
            reward_to_risk_to_target_2 is not None
            and reward_to_risk_to_target_2 >= minimum_reward_to_risk
            and (reward_to_risk is None or reward_to_risk < minimum_reward_to_risk)
        ),
        "use_third_target": (
            reward_to_risk_to_target_3 is not None
            and reward_to_risk_to_target_3 >= minimum_reward_to_risk
            and (reward_to_risk_to_target_2 is None or reward_to_risk_to_target_2 < minimum_reward_to_risk)
        ),
        "pullback_entry_viable": (
            pullback_reward_to_risk is not None
            and pullback_reward_to_risk >= minimum_reward_to_risk
        ),
        "alternative_entry": alternative_entry,
        "target_1": target,
        "target_2": target_2,
        "target_3": target_3,
    }

    if plan["pullback_entry_viable"]:
        plan["condition"] = "Wait for pullback entry; do not chase breakout."
    elif plan["better_entry_required"]:
        plan["condition"] = "Only consider if price is at or below suggested entry."
    elif plan["use_second_target"]:
        plan["condition"] = "Only consider if target 2 is the intended profit objective."
    elif plan["use_third_target"]:
        plan["condition"] = "Only consider if target 3 is realistic and aligned with market regime."
    else:
        plan["condition"] = "Monitor only; current setup does not meet risk structure."

    return plan


def max_entry_for_reward_to_risk(stop, target, minimum_reward_to_risk):
    if stop is None or target is None:
        return None
    return (target + (minimum_reward_to_risk * stop)) / (1 + minimum_reward_to_risk)


def build_veto_report(ticker, policy, technical_report, reasons):
    return {
        "agent": "Risk Manager",
        "run_id": datetime.now().strftime("%Y-%m-%d-risk"),
        "symbol": ticker,
        "decision": "veto",
        "confidence": 0.9,
        "policy_version": policy["version"],
        "technical_stance": technical_report.get("stance"),
        "entry": None,
        "alternative_entry": None,
        "stop": None,
        "target_1": None,
        "target_2": None,
        "target_3": None,
        "reward_to_risk": None,
        "reward_to_risk_to_target_2": None,
        "reward_to_risk_to_target_3": None,
        "pullback_reward_to_risk": None,
        "position": {},
        "earnings": {},
        "portfolio_exposure": {},
        "vetoes": reasons,
        "conditional_issues": [],
        "conditional_plan": {},
        "warnings": [],
        "missing_information": [],
        "citations": technical_report.get("citations", []),
    }


def format_risk_report(report):
    lines = [
        "# Risk Manager Report",
        "",
        f"Run ID: {report['run_id']}",
        f"Symbol: {report['symbol']}",
        f"Decision: {report['decision']}",
        f"Confidence: {report['confidence']}",
        f"Policy Version: {report['policy_version']}",
        f"Technical Stance: {report['technical_stance']}",
        "",
        "## Setup",
        f"- Entry: {format_number(report['entry'])}",
        f"- Alternative Entry: {format_number(report.get('alternative_entry'))}",
        f"- Stop: {format_number(report['stop'])}",
        f"- Target 1: {format_number(report['target_1'])}",
        f"- Target 2: {format_number(report.get('target_2'))}",
        f"- Target 3: {format_number(report.get('target_3'))}",
        f"- Reward/Risk: {format_number(report['reward_to_risk'])}",
        f"- Reward/Risk to Target 2: {format_number(report.get('reward_to_risk_to_target_2'))}",
        f"- Pullback Reward/Risk: {format_number(report.get('pullback_reward_to_risk'))}",
        "",
        "## Position Sizing",
    ]

    position = report.get("position") or {}
    if position.get("error"):
        lines.append(f"- Error: {position['error']}")
    elif position:
        lines.extend([
            f"- Paper Account Size: {format_number(position['paper_account_size'])}",
            f"- Max Dollar Risk: {format_number(position['max_dollar_risk'])}",
            f"- Risk Per Share: {format_number(position['risk_per_share'])}",
            f"- Shares: {position['shares']}",
            f"- Shares by Risk Limit: {position.get('shares_by_risk')}",
            f"- Shares by Exposure Limit: {position.get('shares_by_exposure')}",
            f"- Size Limited by Exposure: {position.get('size_limited_by_exposure')}",
            f"- Position Value: {format_number(position['position_value'])}",
            f"- Max Position Value: {format_number(position['max_position_value'])}",
        ])
    else:
        lines.append("- Not available.")

    lines.extend([
        "",
        "## Event Risk",
        f"- Earnings Date: {report.get('earnings', {}).get('earnings_date') or 'n/a'}",
        f"- Days Until Earnings: {report.get('earnings', {}).get('days_until_earnings') if report.get('earnings', {}).get('days_until_earnings') is not None else 'n/a'}",
        "",
        "## Portfolio Exposure",
        f"- Current Symbol Exposure: {format_number(report.get('portfolio_exposure', {}).get('current_symbol_exposure_pct'))}%",
        f"- Correlated Exposure: {format_number(report.get('portfolio_exposure', {}).get('correlated_exposure_pct'))}%",
        "",
        "## Vetoes",
    ])
    lines.extend([f"- {item}" for item in report["vetoes"]] or ["- None."])

    lines.extend([
        "",
        "## Conditional Issues",
    ])
    lines.extend([f"- {item}" for item in report.get("conditional_issues", [])] or ["- None."])

    conditional_plan = report.get("conditional_plan") or {}
    if conditional_plan:
        lines.extend([
            "",
            "## Conditional Plan",
            f"- Condition: {conditional_plan.get('condition')}",
            f"- Suggested Entry: {format_number(conditional_plan.get('suggested_entry'))}",
            f"- Max Entry for Target 1: {format_number(conditional_plan.get('max_entry_for_target_1'))}",
            f"- Pullback Entry Viable: {conditional_plan.get('pullback_entry_viable')}",
            f"- Use Second Target: {conditional_plan.get('use_second_target')}",
        ])

    lines.extend([
        "",
        "## Warnings",
    ])
    lines.extend([f"- {item}" for item in report["warnings"]] or ["- None."])

    lines.extend([
        "",
        "## Missing Information",
    ])
    lines.extend([f"- {item}" for item in report["missing_information"]] or ["- None."])

    return "\n".join(lines) + "\n"


def save_risk_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{report['symbol']}_risk_report.md"
    path.write_text(format_risk_report(report), encoding="utf-8")
    return path


def format_number(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"
