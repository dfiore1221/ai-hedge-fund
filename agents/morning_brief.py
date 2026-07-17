import json
import io
import contextlib
from datetime import datetime
from pathlib import Path

from agents.cio import create_cio_summary
from agents.market_intelligence import generate_daily_market_intelligence
from data.data_quality import generate_data_health_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WATCHLIST_PATH = PROJECT_ROOT / "framework" / "watchlist.json"
REPORTS_DIR = PROJECT_ROOT / "reports" / "morning_brief"
DEFAULT_TOP_N = 10


def load_watchlist_entries():
    if not WATCHLIST_PATH.exists():
        return [
            {"symbol": "MSFT", "display_symbol": "MSFT", "category": "AI Platforms"},
            {"symbol": "NVDA", "display_symbol": "NVDA", "category": "AI Semiconductors"},
            {"symbol": "GOOGL", "display_symbol": "GOOGL", "category": "AI Platforms"},
            {"symbol": "AMZN", "display_symbol": "AMZN", "category": "AI Platforms"},
            {"symbol": "META", "display_symbol": "META", "category": "AI Platforms"},
            {"symbol": "QQQ", "display_symbol": "QQQ", "category": "ETF"},
            {"symbol": "SPY", "display_symbol": "SPY", "category": "ETF"},
        ]

    data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    entries = []

    for item in data.get("symbols", []):
        if isinstance(item, str):
            symbol = item.upper().strip()
            if symbol:
                entries.append({
                    "symbol": symbol,
                    "display_symbol": symbol,
                    "category": "Uncategorized",
                })
            continue

        symbol = item.get("symbol", "").upper().strip()
        if not symbol:
            continue
        entries.append({
            "symbol": symbol,
            "display_symbol": item.get("display_symbol", symbol).upper().strip(),
            "category": item.get("category", "Uncategorized"),
            "notes": item.get("notes"),
        })

    return entries


def load_watchlist():
    return [entry["symbol"] for entry in load_watchlist_entries()]


def create_morning_brief(symbols=None, max_ideas=DEFAULT_TOP_N):
    entries = build_entries(symbols)
    symbols = [entry["symbol"] for entry in entries]
    metadata_by_symbol = {entry["symbol"]: entry for entry in entries}
    data_health = generate_data_health_report(symbols=symbols, live_checks=True)
    macro_report = generate_daily_market_intelligence()
    summaries = []

    for symbol in symbols:
        try:
            summary = run_committee_scan(symbol, macro_report)
        except Exception as exc:
            summary = build_error_summary(symbol, exc, macro_report)
        summary["watchlist"] = metadata_by_symbol.get(symbol, {
            "symbol": symbol,
            "display_symbol": symbol,
            "category": "Uncategorized",
        })
        summaries.append(summary)

    ranked = sorted(
        [summary for summary in summaries if not summary.get("error")],
        key=score_candidate,
        reverse=True,
    )
    approved = [
        summary
        for summary in ranked
        if summary.get("final_decision", {}).get("status") == "PAPER TRADE ONLY"
    ]
    conditional = [
        summary
        for summary in ranked
        if summary.get("final_decision", {}).get("status") == "CONDITIONAL SETUP"
    ]
    watch = [
        summary
        for summary in ranked
        if summary.get("final_decision", {}).get("status") == "WATCHLIST SETUP"
    ]
    rejected = [
        summary
        for summary in ranked
        if summary.get("final_decision", {}).get("status") == "NO TRADE"
    ]
    needs_data = [
        summary
        for summary in ranked
        if summary.get("final_decision", {}).get("status") == "NEEDS DATA"
    ]

    return {
        "agent": "Morning Brief",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "watch_only",
        "data_health": data_health,
        "macro": macro_report,
        "symbols_scanned": symbols,
        "top_n": max_ideas,
        "approved_simulated_trades": [summarize_idea(summary) for summary in approved[:max_ideas]],
        "conditional_setups": [summarize_idea(summary) for summary in conditional[:max_ideas]],
        "worth_watching": [summarize_idea(summary) for summary in watch[:max_ideas]],
        "rejected_or_avoid": [summarize_idea(summary) for summary in rejected[:max_ideas]],
        "needs_data": [summarize_idea(summary) for summary in needs_data[:max_ideas]],
        "ideas": [summarize_idea(summary) for summary in ranked[:max_ideas]],
        "category_summary": build_category_summary(summaries),
        "committee_summaries": summaries,
        "missing_information": collect_missing_information(summaries),
    }


def build_entries(symbols):
    if symbols is None:
        return load_watchlist_entries()

    entries = []
    for symbol in symbols:
        clean = symbol.upper().strip()
        if clean:
            entries.append({
                "symbol": clean,
                "display_symbol": clean,
                "category": "Ad Hoc Scan",
            })
    return entries


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
    elif decision.get("status") == "CONDITIONAL SETUP":
        score += 30
    elif decision.get("status") == "WATCHLIST SETUP":
        score += 20
    elif decision.get("status") == "NO TRADE":
        score -= 5
    elif decision.get("status") == "NEEDS DATA":
        score -= 25

    if risk_decision == "approved_for_paper_trade":
        score += 18
    elif risk_decision == "conditional_setup":
        score += 10
    elif risk_decision == "watchlist_setup":
        score += 4
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


def get_reward_to_risk(summary):
    source_reports = summary.get("source_reports") or {}
    risk = source_reports.get("risk") or {}
    if risk.get("reward_to_risk") is not None:
        return risk["reward_to_risk"]
    if risk.get("reward_to_risk_to_target_2") is not None:
        return risk["reward_to_risk_to_target_2"]
    if risk.get("pullback_reward_to_risk") is not None:
        return risk["pullback_reward_to_risk"]

    technical = source_reports.get("technical") or {}
    setup = technical.get("setup") or {}
    return setup.get("reward_to_risk")


def summarize_idea(summary):
    source_reports = summary.get("source_reports") or {}
    technical = source_reports.get("technical") or {}
    risk = source_reports.get("risk") or {}
    backtest = source_reports.get("backtest") or {}
    thesis = summary.get("current_thesis") or {}
    watchlist = summary.get("watchlist") or {}

    return {
        "symbol": summary["symbol"],
        "run_id": summary.get("run_id"),
        "display_symbol": watchlist.get("display_symbol", summary["symbol"]),
        "category": watchlist.get("category", "Uncategorized"),
        "score": score_candidate(summary),
        "decision": summary["final_decision"]["status"],
        "reason": build_idea_reason(summary),
        "technical_stance": summary.get("technical_stance"),
        "risk_decision": summary.get("risk_decision"),
        "reward_to_risk": get_reward_to_risk(summary),
        "entry_trigger": risk.get("entry") or (technical.get("setup") or {}).get("entry_trigger"),
        "suggested_entry": (risk.get("conditional_plan") or {}).get("suggested_entry"),
        "condition": (risk.get("conditional_plan") or {}).get("condition"),
        "stop": risk.get("stop") or (technical.get("setup") or {}).get("stop"),
        "target_1": risk.get("target_1") or (technical.get("setup") or {}).get("target_1"),
        "target_2": risk.get("target_2") or (technical.get("setup") or {}).get("target_2"),
        "target_3": risk.get("target_3") or (technical.get("setup") or {}).get("target_3"),
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
    elif summary.get("risk_decision") == "conditional_setup":
        plan = risk.get("conditional_plan") or {}
        reasons.append(plan.get("condition") or "conditional setup")
    elif summary.get("risk_decision") == "watchlist_setup":
        issues = risk.get("conditional_issues") or []
        reasons.append(issues[0] if issues else "watchlist setup")
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


def build_category_summary(summaries):
    categories = {}

    for summary in summaries:
        watchlist = summary.get("watchlist") or {}
        category = watchlist.get("category", "Uncategorized")
        decision = (summary.get("final_decision") or {}).get("status", "ERROR")

        if category not in categories:
            categories[category] = {
                "category": category,
                "symbols": 0,
                "paper_trade": 0,
                "conditional": 0,
                "watchlist": 0,
                "no_trade": 0,
                "needs_data": 0,
                "errors": 0,
            }

        categories[category]["symbols"] += 1
        if decision == "PAPER TRADE ONLY":
            categories[category]["paper_trade"] += 1
        elif decision == "CONDITIONAL SETUP":
            categories[category]["conditional"] += 1
        elif decision == "WATCHLIST SETUP":
            categories[category]["watchlist"] += 1
        elif decision == "NO TRADE":
            categories[category]["no_trade"] += 1
        elif decision == "NEEDS DATA":
            categories[category]["needs_data"] += 1
        else:
            categories[category]["errors"] += 1

    return sorted(categories.values(), key=lambda item: item["category"])


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
    summaries = report["committee_summaries"]
    top_n = report.get("top_n", DEFAULT_TOP_N)
    data_health = report.get("data_health") or {}
    data_gate = data_health.get("gate") or {}

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
        f"- Conditional Setups: {count_decisions(summaries, 'CONDITIONAL SETUP')}",
        f"- Watchlist Setups: {count_decisions(summaries, 'WATCHLIST SETUP')}",
        f"- No-Trade / Avoid Today: {count_decisions(summaries, 'NO TRADE')}",
        f"- Needs Data: {count_decisions(summaries, 'NEEDS DATA')}",
        "",
        "## Data Quality Gate",
        f"- Score: {data_health.get('data_quality_score', 'n/a')}/100",
        f"- Status: {data_gate.get('status', 'n/a')}",
        f"- Decision: {data_gate.get('decision', 'n/a')}",
        "- Note: Full live provider checks are available with `python3 main.py data-health today`.",
        "",
        "## Approved Simulated Trades",
    ]

    append_idea_section(lines, report["approved_simulated_trades"], empty_text="None today.")

    lines.extend([
        "",
        f"## Conditional Setups (Top {top_n})",
    ])
    append_idea_section(
        lines,
        report["conditional_setups"],
        empty_text="None today.",
        show_guardrail=True,
    )

    lines.extend([
        "",
        f"## Watchlist Setups (Top {top_n})",
    ])
    append_idea_section(
        lines,
        report["worth_watching"],
        empty_text="None today.",
        show_guardrail=True,
    )

    lines.extend([
        "",
        f"## Rejected / Avoid Today (Top {top_n} by Review Score)",
    ])
    append_idea_section(
        lines,
        report["rejected_or_avoid"],
        empty_text="No rejected names surfaced.",
        show_guardrail=True,
    )

    lines.extend([
        "",
        "## Category Scan",
    ])

    for category in report.get("category_summary", []):
        lines.append(
            f"- {category['category']}: {category['symbols']} scanned | "
            f"paper {category['paper_trade']} | conditional {category['conditional']} | "
            f"watchlist {category['watchlist']} | no trade {category['no_trade']} | "
            f"needs data {category['needs_data']} | errors {category['errors']}"
        )

    errors = [summary for summary in summaries if summary.get("error")]
    if errors:
        lines.extend(["", "## Symbols Needing Cleanup"])
        for summary in errors[:top_n]:
            display = get_display_symbol(summary)
            lines.append(f"- {display}: {summary['error']}")

    lines.extend([
        "",
        "## Guardrails",
        "- This is a watch-only research brief, not a live trade instruction.",
        "- Hard Risk vetoes override bullish thesis, options flow, or news clues.",
        "- Conditional setups require the stated entry, target, or confirmation before simulated trade approval.",
        "- Any paper trade still requires human review before action.",
        "",
        "## Missing Information",
    ])
    lines.extend([f"- {item}" for item in report["missing_information"]] or ["- None."])

    return "\n".join(lines) + "\n"


def append_idea_section(lines, ideas, empty_text, show_guardrail=False):
    if not ideas:
        lines.append(f"- {empty_text}")
        return

    for index, idea in enumerate(ideas, start=1):
        label = format_idea_label(idea)
        lines.append(
            f"{index}. {label} - {idea['decision']} "
            f"(score {format_number(idea['score'])}, {idea['category']})"
        )
        lines.append(f"   - Why: {idea['reason'] or 'No clear positive setup.'}")
        if idea.get("run_id"):
            lines.append(f"   - Run ID: {idea['run_id']}")
        lines.append(
            f"   - Setup: entry {format_number(idea['entry_trigger'])}, "
            f"suggested {format_number(idea.get('suggested_entry'))}, "
            f"stop {format_number(idea['stop'])}, target {format_number(idea['target_1'])}, "
            f"reward/risk {format_number(idea['reward_to_risk'])}"
        )
        if idea.get("condition"):
            lines.append(f"   - Condition: {idea['condition']}")
        if idea.get("target_2") or idea.get("target_3"):
            lines.append(
                f"   - Extra targets: target 2 {format_number(idea.get('target_2'))}, "
                f"target 3 {format_number(idea.get('target_3'))}"
            )
        lines.append(
            f"   - Backtest: expectancy {format_number(idea['backtest_expectancy'])}%, "
            f"sample {idea['backtest_sample_size'] if idea['backtest_sample_size'] is not None else 'n/a'}"
        )
        if show_guardrail and idea["decision"] != "PAPER TRADE ONLY":
            lines.append("   - Guardrail: review only; not approved as a simulated trade.")


def format_idea_label(idea):
    if idea["display_symbol"] == idea["symbol"]:
        return idea["symbol"]
    return f"{idea['display_symbol']} ({idea['symbol']})"


def get_display_symbol(summary):
    watchlist = summary.get("watchlist") or {}
    display = watchlist.get("display_symbol", summary.get("symbol"))
    symbol = summary.get("symbol")
    if display == symbol:
        return symbol
    return f"{display} ({symbol})"


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
