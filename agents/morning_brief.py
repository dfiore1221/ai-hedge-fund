import json
import io
import contextlib
from datetime import datetime
from pathlib import Path

from agents.cio import create_cio_summary
from agents.market_intelligence import generate_daily_market_intelligence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WATCHLIST_PATH = PROJECT_ROOT / "framework" / "watchlist.json"
REPORTS_DIR = PROJECT_ROOT / "reports" / "morning_brief"


def load_watchlist():
    if not WATCHLIST_PATH.exists():
        return ["MSFT", "NVDA", "GOOGL", "AMZN", "META", "QQQ", "SPY"]

    data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    symbols = data.get("symbols", [])
    return [symbol.upper().strip() for symbol in symbols if symbol.strip()]


def create_morning_brief(symbols=None, max_ideas=5):
    symbols = symbols or load_watchlist()
    macro_report = generate_daily_market_intelligence()
    summaries = []

    for symbol in symbols:
        try:
            summaries.append(run_committee_scan(symbol, macro_report))
        except Exception as exc:
            summaries.append(build_error_summary(symbol, exc, macro_report))

    ranked = sorted(
        [
            summary
            for summary in summaries
            if not summary.get("error") and is_review_candidate(summary)
        ],
        key=score_candidate,
        reverse=True,
    )
    ideas = ranked[:max_ideas]

    return {
        "agent": "Morning Brief",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "watch_only",
        "macro": macro_report,
        "symbols_scanned": symbols,
        "ideas": [summarize_idea(summary) for summary in ideas],
        "committee_summaries": summaries,
        "missing_information": collect_missing_information(summaries),
    }


def run_committee_scan(symbol, macro_report):
    # yfinance can print harmless ETF metadata warnings directly; keep the brief clean.
    with contextlib.redirect_stderr(io.StringIO()):
        return create_cio_summary(symbol, macro_report=macro_report)


def build_error_summary(symbol, exc, macro_report):
    return {
        "agent": "Chief Investment Officer",
        "symbol": symbol.upper().strip(),
        "error": str(exc),
        "market_regime": macro_report["assessment"]["market_regime"],
        "final_decision": {
            "status": "ERROR",
            "confidence": 0,
            "reason": "Committee scan failed for this symbol.",
        },
    }


def score_candidate(summary):
    score = 0
    decision = summary.get("final_decision", {})
    technical_stance = summary.get("technical_stance")
    risk_decision = summary.get("risk_decision")
    options_stance = summary.get("options_stance")
    thesis = summary.get("current_thesis") or {}
    plan = summary.get("trade_plan") or {}
    source_reports = summary.get("source_reports") or {}
    backtest = source_reports.get("backtest") or {}
    conflict_memo = summary.get("conflict_memo") or {}

    if decision.get("status") == "PAPER TRADE ONLY":
        score += 40
    elif decision.get("status") == "WATCH ONLY":
        score += 22
    elif decision.get("status") == "NO TRADE":
        score += 5

    if risk_decision == "approved_for_paper_trade":
        score += 18
    elif risk_decision == "veto":
        score -= 20

    if technical_stance == "bullish":
        score += 25
    elif technical_stance == "neutral":
        score += 12
    elif technical_stance == "no_trade":
        score -= 10
    elif technical_stance == "bearish":
        score -= 35

    reward_to_risk = get_reward_to_risk(summary)
    if reward_to_risk is not None:
        score += min(20, max(-10, (reward_to_risk - 1) * 12))

    expectancy = backtest.get("expectancy_pct")
    sample_size = backtest.get("sample_size") or 0
    if expectancy is not None:
        score += min(15, max(-15, expectancy * 5))
    if sample_size >= 20:
        score += 6
    elif sample_size:
        score -= 4

    if options_stance == "bullish_positioning":
        score += 5
    elif options_stance == "bearish_positioning":
        score -= 5

    if thesis.get("rating") == "Watchlist":
        score += 12
    elif thesis.get("rating") == "Deep Research Candidate":
        score += 8

    if summary.get("market_regime") == "Risk-Off" and decision.get("status") != "PAPER TRADE ONLY":
        score -= 8

    score -= 4 * conflict_memo.get("conflict_count", 0)

    if plan.get("entry_trigger") and plan.get("stop") and plan.get("target_1"):
        score += 4

    return round(score, 2)


def is_review_candidate(summary):
    decision = summary.get("final_decision", {}).get("status")
    technical_stance = summary.get("technical_stance")

    if decision == "PAPER TRADE ONLY":
        return True
    if technical_stance in {"bullish", "neutral"}:
        return True

    return False


def get_reward_to_risk(summary):
    source_reports = summary.get("source_reports") or {}
    risk = source_reports.get("risk") or {}
    if risk.get("reward_to_risk") is not None:
        return risk["reward_to_risk"]

    technical = source_reports.get("technical") or {}
    setup = technical.get("setup") or {}
    return setup.get("reward_to_risk")


def summarize_idea(summary):
    source_reports = summary.get("source_reports") or {}
    technical = source_reports.get("technical") or {}
    risk = source_reports.get("risk") or {}
    backtest = source_reports.get("backtest") or {}
    thesis = summary.get("current_thesis") or {}

    return {
        "symbol": summary["symbol"],
        "score": score_candidate(summary),
        "decision": summary["final_decision"]["status"],
        "reason": build_idea_reason(summary),
        "technical_stance": summary.get("technical_stance"),
        "risk_decision": summary.get("risk_decision"),
        "reward_to_risk": get_reward_to_risk(summary),
        "entry_trigger": risk.get("entry") or (technical.get("setup") or {}).get("entry_trigger"),
        "stop": risk.get("stop") or (technical.get("setup") or {}).get("stop"),
        "target_1": risk.get("target_1") or (technical.get("setup") or {}).get("target_1"),
        "backtest_expectancy": backtest.get("expectancy_pct"),
        "backtest_sample_size": backtest.get("sample_size"),
        "thesis_rating": thesis.get("rating"),
        "conflict_count": (summary.get("conflict_memo") or {}).get("conflict_count", 0),
    }


def build_idea_reason(summary):
    reasons = []
    source_reports = summary.get("source_reports") or {}
    technical = source_reports.get("technical") or {}
    risk = source_reports.get("risk") or {}
    backtest = source_reports.get("backtest") or {}
    thesis = summary.get("current_thesis") or {}

    if summary.get("risk_decision") == "approved_for_paper_trade":
        reasons.append("risk checks passed")
    elif risk.get("vetoes"):
        reasons.append(f"risk veto: {risk['vetoes'][0]}")

    if summary.get("technical_stance"):
        reasons.append(f"technical stance is {summary['technical_stance']}")

    rr = get_reward_to_risk(summary)
    if rr is not None:
        reasons.append(f"reward/risk {rr:.2f}")

    expectancy = backtest.get("expectancy_pct")
    if expectancy is not None:
        reasons.append(f"starter backtest expectancy {expectancy:.2f}%")

    if thesis.get("rating"):
        reasons.append(f"thesis rating {thesis['rating']}")

    evidence = technical.get("key_evidence") or []
    if evidence:
        reasons.append(evidence[0].lower())

    return "; ".join(reasons[:4])


def collect_missing_information(summaries):
    missing = []
    seen = set()

    for summary in summaries:
        for item in summary.get("missing_information", []):
            if item not in seen:
                missing.append(item)
                seen.add(item)

    return missing


def format_morning_brief(report):
    assessment = report["macro"]["assessment"]
    ideas = report["ideas"]
    summaries = report["committee_summaries"]

    lines = [
        "# AI Hedge Fund Morning Brief",
        "",
        f"Created At: {report['created_at']}",
        "Mode: Watch Only / No Live Trading",
        "",
        "## CIO Summary",
        f"- Market Regime: {assessment['market_regime']} ({assessment['macro_score']}/100)",
        f"- Macro Confidence: {assessment['confidence_score']}/100",
        f"- Symbols Scanned: {len(report['symbols_scanned'])}",
        f"- Paper-Trade Candidates: {count_decisions(summaries, 'PAPER TRADE ONLY')}",
        f"- Watch-Only Candidates: {count_decisions(summaries, 'WATCH ONLY')}",
        f"- No-Trade / Avoid Today: {count_decisions(summaries, 'NO TRADE')}",
        "",
        "## Stocks / ETFs Worth Looking At",
    ]

    if not ideas:
        lines.append("- None today.")
    else:
        for index, idea in enumerate(ideas, start=1):
            lines.append(
                f"{index}. {idea['symbol']} - {idea['decision']} "
                f"(score {format_number(idea['score'])})"
            )
            lines.append(f"   - Why: {idea['reason'] or 'No clear positive setup.'}")
            lines.append(
                f"   - Setup: entry {format_number(idea['entry_trigger'])}, "
                f"stop {format_number(idea['stop'])}, target {format_number(idea['target_1'])}, "
                f"reward/risk {format_number(idea['reward_to_risk'])}"
            )
            lines.append(
                f"   - Backtest: expectancy {format_number(idea['backtest_expectancy'])}%, "
                f"sample {idea['backtest_sample_size'] if idea['backtest_sample_size'] is not None else 'n/a'}"
            )

    lines.extend([
        "",
        "## Committee Scan",
    ])

    for summary in summaries:
        decision = summary.get("final_decision", {})
        if summary.get("error"):
            lines.append(f"- {summary['symbol']}: ERROR - {summary['error']}")
            continue

        lines.append(
            f"- {summary['symbol']}: {decision.get('status')} | "
            f"technical {summary.get('technical_stance')} | "
            f"risk {summary.get('risk_decision')} | "
            f"conflicts {(summary.get('conflict_memo') or {}).get('conflict_count', 0)}"
        )

    lines.extend([
        "",
        "## Guardrails",
        "- This is a watch-only research brief, not a live trade instruction.",
        "- Risk vetoes override bullish thesis, options flow, or news clues.",
        "- Any paper trade still requires human review before action.",
        "",
        "## Missing Information",
    ])
    lines.extend([f"- {item}" for item in report["missing_information"]] or ["- None."])

    return "\n".join(lines) + "\n"


def save_morning_brief(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "daily_morning_brief.md"
    path.write_text(format_morning_brief(report), encoding="utf-8")
    return path


def count_decisions(summaries, status):
    return sum(
        1
        for summary in summaries
        if (summary.get("final_decision") or {}).get("status") == status
    )


def format_number(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"
