from datetime import datetime
from pathlib import Path

from agents.communication import (
    build_run_id,
    generate_conflict_memo,
    log_agent_outputs,
    save_conflict_memo,
)
from agents.alternative_data import analyze_alternative_data, save_alternative_data_report
from agents.devils_advocate import save_devils_advocate_report, write_countercase
from agents.market_intelligence import generate_daily_market_intelligence
from agents.news_intelligence import collect_overnight_news
from agents.options_flow import analyze_options_flow
from agents.quant_researcher import backtest_sma_trend_strategy
from agents.risk_manager import evaluate_trade_risk
from agents.technical_analyst import analyze_technical_setup
from memory.research_memory import build_research_memory_context, save_agent_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "cio"


def create_cio_summary(
    ticker,
    macro_report=None,
    include_options=True,
    include_alternative=True,
    include_recommendation_trends=True,
):
    ticker = ticker.upper().strip()
    run_id = build_run_id(ticker)
    macro_report = macro_report or generate_daily_market_intelligence()
    technical_report = analyze_technical_setup(ticker)
    risk_report = evaluate_trade_risk(ticker, technical_report=technical_report)
    memory_context = build_research_memory_context(ticker)
    news_report = collect_overnight_news(
        ticker,
        include_recommendation_trends=include_recommendation_trends,
    )
    options_report = analyze_options_flow(ticker) if include_options else build_options_skipped_report(ticker)
    alternative_report = (
        analyze_alternative_data(ticker)
        if include_alternative
        else build_alternative_skipped_report(ticker)
    )
    backtest_report = backtest_sma_trend_strategy(ticker)
    failed_long_signal = enhance_failed_long_signal(
        risk_report.get("failed_long_signal") or {},
        news_report,
        backtest_report,
    )
    risk_report["failed_long_signal"] = failed_long_signal
    risk_report["trade_vehicle_options"] = merge_vehicle_options(
        risk_report.get("trade_vehicle_options", []),
        failed_long_signal.get("possible_trade_options", []),
    )

    agent_outputs = {
        "macro": macro_report,
        "technical": technical_report,
        "risk": risk_report,
        "news": news_report,
        "options": options_report,
        "alternative": alternative_report,
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
    save_alternative_data_report(alternative_report)

    decision = determine_final_decision(
        macro_report,
        technical_report,
        risk_report,
        memory_context,
        news_report,
        failed_long_signal,
    )
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
        alternative_report,
        backtest_report,
        devils_advocate,
    )

    result = {
        "agent": "Chief Investment Officer",
        "system_role": "committee_agent",
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
        "journal_summary": risk_report.get("journal_summary"),
        "news_stance": news_report.get("stance"),
        "news_catalyst_score": (news_report.get("summary") or {}).get("total_score"),
        "news_top_headline": (news_report.get("summary") or {}).get("top_headline"),
        "options_stance": options_report.get("stance"),
        "alternative_data_stance": alternative_report.get("stance"),
        "alternative_data_summary": alternative_report.get("summary"),
        "backtest_expectancy": backtest_report.get("expectancy_pct"),
        "backtest_sample_size": backtest_report.get("sample_size"),
        "failed_long_signal": failed_long_signal,
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
    save_agent_report(
        run_id=run_id,
        agent_name=result["agent"],
        output=result,
        symbol=ticker,
        stance=decision["status"],
        confidence=decision["confidence"],
    )
    return result


def build_options_skipped_report(ticker):
    return {
        "agent": "Options & Flow Analyst",
        "system_role": "shared_data_layer",
        "layer": "Options Flow Evidence",
        "symbol": ticker.upper().strip(),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "provider": "not_run",
        "mode": "morning_brief_fast_path",
        "stance": "unknown",
        "confidence": 0,
        "liquidity_quality": "unknown",
        "warning": "Execution-grade options provider is not connected; starter options readiness is available separately.",
        "missing_information": [
            "Execution-grade options flow/greeks provider is not connected yet.",
            "Live starter options-chain scraping is skipped during broad morning scans for reliability.",
            "No paid Intrinio/Tradier/ORATS options feed connected.",
        ],
    }


def build_alternative_skipped_report(ticker):
    return {
        "agent": "Alternative Data",
        "system_role": "shared_data_layer",
        "layer": "Alternative / Positioning Evidence",
        "symbol": ticker.upper().strip(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "provider": "not_run",
        "mode": "morning_brief_fast_path",
        "configured": False,
        "status": "skipped",
        "stance": "skipped",
        "confidence": 0,
        "summary": "Alternative-data check skipped during broad morning scans for reliability.",
        "missing_information": [
            "Quiver alternative-data check was skipped during broad morning scan.",
            "Trader-tier Quiver ownership/crowding datasets are not connected.",
        ],
    }


def determine_final_decision(macro_report, technical_report, risk_report, memory_context, news_report=None, failed_long_signal=None):
    if risk_report["decision"] == "veto":
        if has_data_veto(risk_report):
            return {
                "status": "NEEDS DATA",
                "confidence": 0.9,
                "reason": "Market data or setup data was unavailable, so the committee cannot form a useful view.",
            }
        return {
            "status": "NO TRADE",
            "confidence": 0.85,
            "reason": "Risk Manager veto has authority over trade approval.",
        }

    if risk_report["decision"] == "conditional_setup":
        failed_long_signal = failed_long_signal or risk_report.get("failed_long_signal") or {}
        if failed_long_signal.get("status") == "strong_watch":
            return {
                "status": "WATCHLIST SETUP",
                "confidence": 0.6,
                "reason": failed_long_signal.get("summary") or "Possible failed-long setup; do not force a long trade.",
            }
        plan = risk_report.get("conditional_plan") or {}
        calibration = risk_report.get("setup_calibration") or {}
        if calibration.get("classification") == "interesting_not_tradable":
            return {
                "status": "WATCHLIST SETUP",
                "confidence": 0.5,
                "reason": calibration.get("message") or "Interesting idea, but the trade structure is not realistic enough.",
            }
        return {
            "status": "CONDITIONAL SETUP",
            "confidence": 0.55,
            "reason": calibration.get("message") or plan.get("condition") or "Setup is interesting but requires a better entry, target, or confirmation.",
        }

    if risk_report["decision"] == "watchlist_setup":
        return {
            "status": "WATCHLIST SETUP",
            "confidence": 0.45,
            "reason": "Interesting enough to monitor, but not structured well enough for a simulated trade.",
        }

    regime = macro_report["assessment"]["market_regime"]
    technical_stance = technical_report.get("stance")
    thesis = memory_context.get("current_thesis") or {}
    rating = thesis.get("rating")
    news_report = news_report or {}

    if regime == "Risk-Off" and technical_stance != "bullish":
        return {
            "status": "WATCHLIST SETUP",
            "confidence": 0.7,
            "reason": "Macro backdrop is risk-off and technical stance is not bullish.",
        }

    if news_report.get("stance") == "negative_catalyst":
        return {
            "status": "WATCHLIST SETUP",
            "confidence": 0.6,
            "reason": "News layer detected a negative catalyst, so setup needs more confirmation.",
        }

    if technical_stance == "bullish" and rating in {"Watchlist", "Deep Research Candidate"}:
        return {
            "status": "PAPER TRADE ONLY",
            "confidence": 0.65,
            "reason": "Setup passed risk checks and thesis quality is sufficient for paper-trade testing.",
        }

    return {
        "status": "WATCHLIST SETUP",
        "confidence": 0.55,
        "reason": "Evidence is not strong enough for a paper trade, but monitoring is justified.",
    }


def has_data_veto(risk_report):
    veto_text = " ".join(risk_report.get("vetoes", [])).lower()
    return "no reliable data" in veto_text or "missing entry" in veto_text


def build_trade_plan(technical_report, risk_report, decision):
    position = risk_report.get("position") or {}
    conditional_plan = risk_report.get("conditional_plan") or {}
    failed_long_signal = risk_report.get("failed_long_signal") or {}

    return {
        "symbol": technical_report.get("symbol"),
        "action": decision["status"],
        "entry_trigger": risk_report.get("entry"),
        "alternative_entry": risk_report.get("alternative_entry"),
        "suggested_entry": conditional_plan.get("suggested_entry"),
        "condition": conditional_plan.get("condition"),
        "stop": risk_report.get("stop"),
        "target_1": risk_report.get("calibrated_target_1") or risk_report.get("target_1"),
        "original_target_1": risk_report.get("original_target_1") or risk_report.get("target_1"),
        "target_2": risk_report.get("target_2"),
        "target_3": risk_report.get("target_3"),
        "partial_win_level": (risk_report.get("setup_calibration") or {}).get("partial_win_level"),
        "tradability": (risk_report.get("setup_calibration") or {}).get("classification"),
        "failed_long_signal": failed_long_signal,
        "trade_vehicle_options": risk_report.get("trade_vehicle_options", []),
        "position_size_shares": position.get("shares"),
        "max_dollar_risk": position.get("max_dollar_risk"),
        "time_horizon": technical_report.get("time_horizon"),
        "review_date": datetime.now().date().isoformat(),
    }


def enhance_failed_long_signal(signal, news_report, backtest_report):
    signal = dict(signal or {})
    evidence = list(signal.get("evidence") or [])
    score = float(signal.get("score") or 0)
    news_score = (news_report.get("summary") or {}).get("total_score") if news_report else None
    news_stance = news_report.get("stance") if news_report else None
    expectancy = backtest_report.get("expectancy_pct") if backtest_report else None
    sample_size = backtest_report.get("sample_size") if backtest_report else None
    base_score = score

    if news_stance == "positive_catalyst" and news_score is not None and news_score >= 10 and base_score >= 35:
        score += 12
        evidence.append(f"Positive catalyst score is high ({news_score:.2f}); hype/fade risk should be checked.")
    elif news_stance == "negative_catalyst":
        score += 12
        evidence.append("News layer detected a negative catalyst.")

    if expectancy is not None and expectancy < 0:
        score += 10
        evidence.append(f"Starter backtest expectancy is negative ({expectancy:.2f}%).")
    elif expectancy is not None and sample_size is not None and sample_size < 15:
        score += 4
        evidence.append(f"Backtest sample is thin ({sample_size}); do not trust the long pattern yet.")

    if score >= 75:
        status = "strong_watch"
        summary = "Possible failed-long / bearish reversal candidate; avoid long unless evidence improves."
    elif score >= 55:
        status = "watch"
        summary = "Long setup has fade risk; monitor for failed breakout or breakdown confirmation."
    else:
        status = signal.get("status", "low")
        summary = signal.get("summary", "No strong failed-long signal.")

    signal.update({
        "status": status,
        "score": round(score, 1),
        "summary": summary,
        "evidence": evidence,
    })
    if status in {"watch", "strong_watch"} and not signal.get("possible_trade_options"):
        signal["possible_trade_options"] = [
            {
                "vehicle": "avoid_or_cancel_long",
                "status": "preferred_risk_control",
                "reason": "No capital has to be deployed when a long setup is structurally weak.",
            },
            {
                "vehicle": "bear_put_spread",
                "status": "watch_only",
                "reason": "Defined-risk downside expression if breakdown confirms and options liquidity is acceptable.",
            },
            {
                "vehicle": "long_put",
                "status": "watch_only_high_premium_risk",
                "reason": "Can benefit from sharp downside but may overpay without IV/greeks data.",
            },
            {
                "vehicle": "short_shares",
                "status": "watch_only_high_risk",
                "reason": "Clean downside expression but carries open-ended risk.",
            },
        ]
    return signal


def merge_vehicle_options(existing, additional):
    merged = []
    seen = set()
    for option in [*(existing or []), *(additional or [])]:
        vehicle = option.get("vehicle")
        if not vehicle or vehicle in seen:
            continue
        seen.add(vehicle)
        merged.append(option)
    return merged


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
    alternative_report,
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
        missing.append("Options flow provider failed or returned incomplete data.")
    missing.extend(alternative_report.get("missing_information", []))
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
        f"- Open / Planned Simulated Trades: {(report.get('journal_summary') or {}).get('open_trades', 0)}",
        f"- News Stance: {report.get('news_stance') or 'n/a'}",
        f"- News Catalyst Score: {format_number(report.get('news_catalyst_score'))}",
        f"- Top Headline: {report.get('news_top_headline') or 'n/a'}",
        f"- Options Stance: {report.get('options_stance') or 'n/a'}",
        f"- Alternative Data: {report.get('alternative_data_stance') or 'n/a'}; {report.get('alternative_data_summary') or 'n/a'}",
        f"- Backtest Expectancy: {format_number(report.get('backtest_expectancy'))}%",
        f"- Backtest Sample Size: {report.get('backtest_sample_size') if report.get('backtest_sample_size') is not None else 'n/a'}",
        f"- Failed-Long / Fade Signal: {format_failed_long_signal(report.get('failed_long_signal'))}",
        f"- Overnight News Items: {report.get('news_items_count')}",
        f"- Thesis Rating: {thesis.get('rating') or 'n/a'}",
        f"- Thesis Score: {thesis.get('overall_score') or 'n/a'}",
        "",
        "## Trade Plan",
        f"- Action: {plan['action']}",
        f"- Entry Trigger: {format_number(plan['entry_trigger'])}",
        f"- Alternative Entry: {format_number(plan.get('alternative_entry'))}",
        f"- Suggested Entry: {format_number(plan.get('suggested_entry'))}",
        f"- Condition: {plan.get('condition') or 'n/a'}",
        f"- Stop: {format_number(plan['stop'])}",
        f"- Target 1: {format_number(plan['target_1'])}",
        f"- Original / Stretch Target 1: {format_number(plan.get('original_target_1'))}",
        f"- Partial-Win Level: {format_number(plan.get('partial_win_level'))}",
        f"- Target 2: {format_number(plan.get('target_2'))}",
        f"- Target 3: {format_number(plan.get('target_3'))}",
        f"- Tradability: {plan.get('tradability') or 'n/a'}",
        f"- Failed-Long / Fade Signal: {format_failed_long_signal(plan.get('failed_long_signal'))}",
        f"- Position Size: {plan['position_size_shares'] if plan['position_size_shares'] is not None else 'n/a'} shares",
        f"- Max Dollar Risk: {format_number(plan['max_dollar_risk'])}",
        f"- Time Horizon: {plan['time_horizon'] or 'n/a'}",
        f"- Review Date: {plan['review_date']}",
        f"- Other Trade Options: {format_vehicle_options(plan.get('trade_vehicle_options'))}",
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


def format_vehicle_options(options):
    if not options:
        return "n/a"
    return "; ".join(
        f"{item.get('vehicle')} ({item.get('status')})"
        for item in options[:4]
    )


def format_failed_long_signal(signal):
    if not signal:
        return "n/a"
    return f"{signal.get('status', 'n/a')} ({format_number(signal.get('score'))}/100) - {signal.get('summary', 'n/a')}"
