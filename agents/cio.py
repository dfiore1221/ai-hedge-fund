from datetime import datetime
from pathlib import Path

from agents.communication import (
    build_run_id,
    generate_conflict_memo,
    log_agent_outputs,
    save_conflict_memo,
)
from agents.devils_advocate import save_devils_advocate_report, write_countercase
from agents.market_intelligence import generate_daily_market_intelligence
from agents.news_intelligence import collect_overnight_news
from agents.options_flow import analyze_options_flow
from agents.quant_researcher import backtest_sma_trend_strategy
from agents.risk_manager import evaluate_trade_risk
from agents.technical_analyst import analyze_technical_setup
from memory.research_memory import build_research_memory_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "cio"


def create_cio_summary(ticker):
    ticker = ticker.upper().strip()
    run_id = build_run_id(ticker)
    macro_report = generate_daily_market_intelligence()
    technical_report = analyze_technical_setup(ticker)
    risk_report = evaluate_trade_risk(ticker, technical_report=technical_report)
    memory_context = build_research_memory_context(ticker)
    news_report = collect_overnight_news(ticker)
    options_report = analyze_options_flow(ticker)
    backtest_report = backtest_sma_trend_strategy(ticker)

    agent_outputs = {
        "macro": macro_report,
        "technical": technical_report,
        "risk": risk_report,
        "news": news_report,
        "options": options_report,
        "backtest": backtest_report,
        "memory": memory_context,
    }
    conflict_memo = generate_conflict_memo(ticker, agent_outputs)
    devils_advocate = write_countercase(ticker, agent_outputs, conflict_memo["conflicts"])
    agent_outputs["conflict_memo"] = conflict_memo
    agent_outputs["devils_advocate"] = devils_advocate
    log_agent_outputs(run_id, ticker, agent_outputs)
    save_conflict_memo(conflict_memo)
    save_devils_advocate_report(devils_advocate)

    decision = determine_final_decision(macro_report, technical_report, risk_report, memory_context)
    disagreements = identify_disagreements(
        macro_report,
        technical_report,
        risk_report,
        memory_context,
        conflict_memo,
    )
    missing_information = collect_missing_information(
        technical_report,
        risk_report,
        memory_context,
        news_report,
        options_report,
        backtest_report,
        devils_advocate,
    )

    return {
        "agent": "Chief Investment Officer",
        "run_id": run_id,
        "symbol": ticker,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "market_regime": macro_report["assessment"]["market_regime"],
        "macro_score": macro_report["assessment"]["macro_score"],
        "macro_confidence": macro_report["assessment"]["confidence_score"],
        "technical_stance": technical_report.get("stance"),
        "technical_confidence": technical_report.get("confidence"),
        "risk_decision": risk_report["decision"],
        "risk_vetoes": risk_report["vetoes"],
        "options_stance": options_report.get("stance"),
        "backtest_expectancy": backtest_report.get("expectancy_pct"),
        "backtest_sample_size": backtest_report.get("sample_size"),
        "news_items_count": len(news_report.get("items", [])),
        "current_thesis": memory_context.get("current_thesis"),
        "final_decision": decision,
        "trade_plan": build_trade_plan(technical_report, risk_report, decision),
        "disagreements": disagreements,
        "conflict_memo": conflict_memo,
        "devils_advocate": devils_advocate,
        "missing_information": missing_information,
        "source_reports": agent_outputs,
    }


def determine_final_decision(macro_report, technical_report, risk_report, memory_context):
    if risk_report["decision"] == "veto":
        return {
            "status": "NO TRADE",
            "confidence": 0.85,
            "reason": "Risk Manager veto has authority over trade approval.",
        }

    regime = macro_report["assessment"]["market_regime"]
    technical_stance = technical_report.get("stance")
    thesis = memory_context.get("current_thesis") or {}
    rating = thesis.get("rating")

    if regime == "Risk-Off" and technical_stance != "bullish":
        return {
            "status": "WATCH ONLY",
            "confidence": 0.7,
            "reason": "Macro backdrop is risk-off and technical stance is not bullish.",
        }

    if technical_stance == "bullish" and rating in {"Watchlist", "Deep Research Candidate"}:
        return {
            "status": "PAPER TRADE ONLY",
            "confidence": 0.65,
            "reason": "Setup passed risk checks and thesis quality is sufficient for paper-trade testing.",
        }

    return {
        "status": "WATCH ONLY",
        "confidence": 0.55,
        "reason": "Evidence is not strong enough for a paper trade, but monitoring is justified.",
    }


def build_trade_plan(technical_report, risk_report, decision):
    position = risk_report.get("position") or {}

    return {
        "symbol": technical_report.get("symbol"),
        "action": decision["status"],
        "entry_trigger": risk_report.get("entry"),
        "stop": risk_report.get("stop"),
        "target_1": risk_report.get("target_1"),
        "position_size_shares": position.get("shares"),
        "max_dollar_risk": position.get("max_dollar_risk"),
        "time_horizon": technical_report.get("time_horizon"),
        "review_date": datetime.now().date().isoformat(),
    }


def identify_disagreements(macro_report, technical_report, risk_report, memory_context, conflict_memo=None):
    disagreements = []
    regime = macro_report["assessment"]["market_regime"]
    technical_stance = technical_report.get("stance")
    thesis = memory_context.get("current_thesis") or {}

    if regime == "Risk-Off" and thesis.get("rating") in {"Watchlist", "Deep Research Candidate"}:
        disagreements.append(
            "Fundamental/thesis memory is constructive, but macro regime is risk-off."
        )

    if technical_stance in {"bullish", "neutral"} and risk_report["decision"] == "veto":
        disagreements.append(
            "Technical setup is not fully negative, but Risk Manager vetoed the trade."
        )

    if technical_stance == "no_trade" and thesis.get("rating") in {"Watchlist", "Deep Research Candidate"}:
        disagreements.append(
            "Company thesis is constructive, but Technical Analyst says no_trade."
        )

    if conflict_memo:
        for conflict in conflict_memo.get("conflicts", []):
            if conflict not in disagreements:
                disagreements.append(conflict)

    return disagreements


def collect_missing_information(
    technical_report,
    risk_report,
    memory_context,
    news_report,
    options_report,
    backtest_report,
    devils_advocate=None,
):
    missing = []
    missing.extend(technical_report.get("missing_information", []))
    missing.extend(risk_report.get("missing_information", []))

    thesis = memory_context.get("current_thesis") or {}
    if thesis.get("open_questions"):
        missing.append("Open thesis questions remain in research memory.")
    if news_report.get("error"):
        missing.append("Overnight news feed unavailable or incomplete.")
    missing.extend(news_report.get("missing_information", []))
    if options_report.get("error"):
        missing.append("Options flow unavailable or incomplete.")
    if not backtest_report.get("tested"):
        missing.append("Backtested expectancy unavailable or sample size is zero.")
    elif backtest_report.get("sample_size", 0) < 20:
        missing.append("Backtest sample size is small; confidence should be discounted.")

    if devils_advocate:
        missing.extend(devils_advocate.get("missing_information", []))

    return missing


def format_cio_report(report):
    decision = report["final_decision"]
    plan = report["trade_plan"]
    thesis = report.get("current_thesis") or {}

    lines = [
        "# CIO Pre-Market Summary",
        "",
        f"Run ID: {report['run_id']}",
        f"Created At: {report['created_at']}",
        f"Symbol: {report['symbol']}",
        "",
        "## Final Decision",
        f"- Status: {decision['status']}",
        f"- Confidence: {decision['confidence']}",
        f"- Reason: {decision['reason']}",
        "",
        "## Agent Inputs",
        f"- Market Regime: {report['market_regime']} ({report['macro_score']}/100, confidence {report['macro_confidence']}/100)",
        f"- Technical Stance: {report['technical_stance']} (confidence {report['technical_confidence']})",
        f"- Risk Decision: {report['risk_decision']}",
        f"- Options Stance: {report.get('options_stance') or 'n/a'}",
        f"- Backtest Expectancy: {format_number(report.get('backtest_expectancy'))}%",
        f"- Backtest Sample Size: {report.get('backtest_sample_size') if report.get('backtest_sample_size') is not None else 'n/a'}",
        f"- Overnight News Items: {report.get('news_items_count')}",
        f"- Thesis Rating: {thesis.get('rating') or 'n/a'}",
        f"- Thesis Score: {thesis.get('overall_score') or 'n/a'}",
        "",
        "## Trade Plan",
        f"- Action: {plan['action']}",
        f"- Entry Trigger: {format_number(plan['entry_trigger'])}",
        f"- Stop: {format_number(plan['stop'])}",
        f"- Target 1: {format_number(plan['target_1'])}",
        f"- Position Size: {plan['position_size_shares'] if plan['position_size_shares'] is not None else 'n/a'} shares",
        f"- Max Dollar Risk: {format_number(plan['max_dollar_risk'])}",
        f"- Time Horizon: {plan['time_horizon'] or 'n/a'}",
        f"- Review Date: {plan['review_date']}",
        "",
        "## Risk Vetoes",
    ]

    lines.extend([f"- {item}" for item in report["risk_vetoes"]] or ["- None."])
    lines.append("")
    lines.append("## Disagreements")
    lines.extend([f"- {item}" for item in report["disagreements"]] or ["- None."])
    lines.append("")
    lines.append("## Conflict Memo")
    conflict_memo = report.get("conflict_memo") or {}
    lines.append(f"- Conflict Count: {conflict_memo.get('conflict_count', 0)}")
    lines.extend([f"- {item}" for item in conflict_memo.get("conflicts", [])] or ["- None."])
    lines.append("")
    lines.append("## Devil's Advocate Countercase")
    devils_advocate = report.get("devils_advocate") or {}
    lines.extend([f"- {item}" for item in devils_advocate.get("countercase", [])] or ["- None."])
    lines.append("")
    lines.append("## Bias Flags")
    lines.extend([f"- {item}" for item in devils_advocate.get("bias_flags", [])] or ["- None."])
    lines.append("")
    lines.append("## Missing Information")
    lines.extend([f"- {item}" for item in report["missing_information"]] or ["- None."])
    lines.append("")
    lines.append("## Human Decision")
    lines.append("- Approve / Paper Trade / Reject: Pending human review.")

    return "\n".join(lines) + "\n"


def save_cio_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{report['symbol']}_cio_summary.md"
    path.write_text(format_cio_report(report), encoding="utf-8")
    return path


def format_number(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"
