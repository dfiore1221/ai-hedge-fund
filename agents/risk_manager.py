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
    stop = setup.get("stop")
    target = setup.get("target_1")
    reward_to_risk_value = setup.get("reward_to_risk")

    vetoes = []
    warnings = []

    if entry is None or stop is None or target is None:
        vetoes.append("Missing entry, stop, or target.")
    elif stop >= entry:
        vetoes.append("Invalid stop relative to entry.")

    if reward_to_risk_value is None:
        vetoes.append("Reward-to-risk unavailable.")
    elif reward_to_risk_value < policy["minimum_reward_to_risk"]:
        vetoes.append(
            f"Reward-to-risk {reward_to_risk_value:.2f} is below minimum "
            f"{policy['minimum_reward_to_risk']:.2f}."
        )

    if technical_report["stance"] == "no_trade":
        vetoes.append("Technical Analyst stance is no_trade.")
    elif technical_report["stance"] == "bearish":
        warnings.append("Technical Analyst stance is bearish.")

    position = calculate_position_size(entry, stop, policy)

    if position.get("error"):
        vetoes.append(position["error"])
    elif position["position_value"] > position["max_position_value"]:
        vetoes.append(
            "Position value exceeds max single-position exposure: "
            f"{position['position_value']:.2f} > {position['max_position_value']:.2f}."
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

    decision = "veto" if vetoes else "approved_for_paper_trade"
    confidence = 0.25 if vetoes else 0.65

    return {
        "agent": "Risk Manager",
        "run_id": datetime.now().strftime("%Y-%m-%d-risk"),
        "symbol": ticker,
        "decision": decision,
        "confidence": confidence,
        "policy_version": policy["version"],
        "technical_stance": technical_report["stance"],
        "entry": entry,
        "stop": stop,
        "target_1": target,
        "reward_to_risk": reward_to_risk_value,
        "position": position,
        "earnings": earnings,
        "portfolio_exposure": portfolio_exposure,
        "vetoes": vetoes,
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
    shares = int(max_dollar_risk // risk_per_share)
    position_value = shares * entry
    max_position_value = account_size * policy["max_single_position_pct"]

    return {
        "paper_account_size": account_size,
        "max_dollar_risk": max_dollar_risk,
        "risk_per_share": risk_per_share,
        "shares": shares,
        "position_value": position_value,
        "max_position_value": max_position_value,
    }


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
        "stop": None,
        "target_1": None,
        "reward_to_risk": None,
        "position": {},
        "earnings": {},
        "portfolio_exposure": {},
        "vetoes": reasons,
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
        f"- Stop: {format_number(report['stop'])}",
        f"- Target 1: {format_number(report['target_1'])}",
        f"- Reward/Risk: {format_number(report['reward_to_risk'])}",
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
