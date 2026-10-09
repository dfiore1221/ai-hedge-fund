import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PROJECT_ROOT / "framework" / "strategy_policy.json"


def load_strategy_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def route_strategy(summary, data_health=None, feedback=None, policy=None):
    """Choose strategy, horizon, and vehicle before a ticker becomes an order."""
    policy = policy or load_strategy_policy()
    data_health = data_health or {}
    feedback = feedback or {}
    decision = (summary.get("final_decision") or {}).get("status", "")
    technical = str(summary.get("technical_stance") or "unknown").lower()
    risk_decision = str(summary.get("risk_decision") or "unknown").lower()
    news = str(summary.get("news_stance") or "unknown").lower()
    thesis = summary.get("current_thesis") or {}
    source_reports = summary.get("source_reports") or {}
    backtest = source_reports.get("backtest") or {}
    failed_long = summary.get("failed_long_signal") or (source_reports.get("risk") or {}).get("failed_long_signal") or {}
    macro_regime = str(summary.get("market_regime") or "unknown")
    expectancy = to_float(backtest.get("expectancy_pct"))
    sample = int(to_float(backtest.get("sample_size")) or 0)
    learning = feedback.get("setup_review_learning") or {}
    domain_scores = data_health.get("domain_scores") or {}
    options_ratio = domain_ratio(domain_scores.get("options") or {})

    selected = "swing"
    rationale = []
    blocked = []

    bearish_confirmation = (
        technical == "bearish"
        and str(failed_long.get("status") or "") in {"watch", "strong_watch"}
        and (risk_decision != "veto" or bearish_option_risk_eligible(summary))
    )
    bullish_confirmation = technical == "bullish" and risk_decision in {
        "approved_for_paper_trade", "conditional_setup"
    }
    durable_thesis = thesis.get("rating") in {"Watchlist", "Deep Research Candidate"}
    positive_evidence = expectancy is not None and expectancy > 0 and sample >= 10

    if bearish_confirmation:
        selected = "long_put"
        rationale.append("Technical weakness and the failed-long signal support a defined-loss bearish expression.")
    elif bullish_confirmation and get_catalyst_strength(summary) >= 8 and options_ratio >= 0.6:
        selected = "long_call"
        rationale.append("A confirmed bullish setup with a material catalyst supports a defined-premium call experiment.")
    elif bullish_confirmation and durable_thesis and positive_evidence and macro_regime != "Risk-Off":
        selected = "position"
        rationale.append("Technical confirmation, a durable thesis, and positive historical evidence support a longer holding period.")
    elif bullish_confirmation:
        selected = "swing"
        rationale.append("The evidence supports a bounded multi-day equity setup, but not a long-duration thesis yet.")
    else:
        rationale.append("No strategy has enough directional and risk confirmation for autonomous execution.")

    catalyst_score = abs(to_float(summary.get("news_catalyst_score")) or 0)
    if catalyst_score >= 8 or news in {"positive_catalyst", "negative_catalyst"}:
        for family in ("scalp", "day"):
            row = (policy.get("families") or {}).get(family) or {}
            if not row.get("enabled"):
                blocked.append({
                    "strategy": family,
                    "reason": row.get("blocked_reason"),
                })

    family = (policy.get("families") or {}).get(selected) or {}
    status = "eligible" if family.get("enabled") else "data_blocked"
    evidence_gaps = []
    if selected in {"long_call", "long_put"}:
        if options_ratio < 0.6:
            status = "data_blocked"
            evidence_gaps.append("Options domain quality is below 60%.")
        evidence_gaps.extend([
            "Full OPRA-style intraday flow and historical options chains are not connected.",
            "Paper execution requires a live contract-level liquidity check before an order is created.",
        ])

    if selected == "position" and not durable_thesis:
        status = "data_blocked"
        evidence_gaps.append("A durable fundamental thesis is required for position trading.")
    if decision not in {"PAPER TRADE ONLY", "CONDITIONAL SETUP"} and not (
        selected == "long_put" and bearish_option_risk_eligible(summary)
    ):
        status = "committee_blocked"
    if risk_decision == "veto" and not (selected == "long_put" and bearish_option_risk_eligible(summary)):
        status = "risk_veto"

    family_memory = strategy_memory(feedback, selected)
    if family_memory and family_memory.get("entered_count", 0) >= 10:
        if family_memory.get("target_1_hit_rate", 0) < 10:
            status = "learning_hold"
            evidence_gaps.append("This strategy family has at least 10 entries and a Target 1 hit rate below 10%.")
        rationale.append(
            f"Strategy memory: {family_memory.get('entered_count')} entries, "
            f"{family_memory.get('target_1_hit_rate', 0):.1f}% Target 1 hit rate."
        )

    return {
        "selected_family": selected,
        "vehicle": family.get("vehicle", "equity"),
        "holding_horizon": family.get("holding_horizon", "unknown"),
        "direction": "bearish" if selected == "long_put" else "bullish",
        "side": "long",
        "execution_status": status,
        "risk_per_trade_pct": family.get("risk_per_trade_pct"),
        "target_r": family.get("target_r"),
        "profit_take_pct": family.get("profit_take_pct"),
        "stop_loss_pct": family.get("stop_loss_pct"),
        "rationale": rationale,
        "blocked_alternatives": blocked,
        "evidence_gaps": evidence_gaps,
        "strategy_memory": family_memory,
        "policy_version": policy.get("version"),
    }


def strategy_memory(feedback, family):
    for row in (feedback.get("setup_review_learning") or {}).get("by_strategy_family", []):
        if row.get("strategy_family") == family:
            return row
    for row in feedback.get("by_option_strategy", []):
        if row.get("strategy_family") == family:
            return row
    return {}


def domain_ratio(row):
    maximum = to_float(row.get("max_score"))
    return (to_float(row.get("score")) or 0) / maximum if maximum else 0


def get_catalyst_strength(summary):
    return abs(to_float(summary.get("news_catalyst_score")) or 0)


def bearish_option_risk_eligible(summary):
    vetoes = [str(item).lower() for item in summary.get("risk_vetoes", [])]
    if not vetoes:
        return True
    return all("bearish; long simulated trade is blocked" in item for item in vetoes)


def to_float(value):
    try:
        return float(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None
