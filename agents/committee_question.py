import json
import re
from datetime import datetime
from pathlib import Path

from agents.cio import create_cio_summary, save_cio_report
from agents.market_intelligence import generate_daily_market_intelligence
from agents.feedback_loop import generate_feedback_report
from data.data_quality import generate_data_health_report
from data.paper_ledger import build_paper_ledger
from data.trade_journal import OPEN_STATUSES, enrich_trade_metrics, load_trade_journal, normalize_status, to_float
from memory.research_memory import save_agent_report, save_committee_question


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "committee_questions"
MORNING_BRIEF_JSON_PATH = PROJECT_ROOT / "reports" / "morning_brief" / "daily_morning_brief.json"


def ask_committee(question, symbol=None, scope="ticker"):
    question = str(question or "").strip()
    if not question:
        raise ValueError("Committee question cannot be blank.")

    scope = normalize_scope(scope, symbol)
    if scope == "portfolio" and not symbol:
        inferred_symbol = infer_symbol_from_question(question)
        if inferred_symbol:
            symbol = inferred_symbol
            scope = "ticker"
    topic = infer_topic(question)

    if scope == "ticker":
        if not symbol:
            raise ValueError("Ticker committee questions require a symbol.")
        report = answer_ticker_question(question, symbol, topic)
    else:
        report = answer_portfolio_question(question, topic)

    question_id = save_committee_question(report)
    report["question_id"] = question_id
    save_committee_question_report(report)
    save_agent_report(
        run_id=report["run_id"],
        agent_name="Committee Question",
        output=report,
        symbol=report.get("symbol"),
        stance=report.get("status"),
        confidence=report.get("confidence"),
    )
    return report


def answer_ticker_question(question, symbol, topic):
    symbol = symbol.upper().strip()
    cio_report = create_cio_summary(symbol)
    save_cio_report(cio_report)
    position_context = build_position_context(symbol)

    decision = cio_report.get("final_decision") or {}
    trade_plan = cio_report.get("trade_plan") or {}
    learning_notes = build_ticker_learning_notes(cio_report, topic)
    if position_context.get("has_open_position"):
        learning_notes.insert(
            0,
            "This question involved an open simulated position, so future review should score the management decision separately from the original entry call.",
        )
    answer = format_ticker_committee_answer(question, cio_report, learning_notes, position_context)
    status = "MANAGE OPEN POSITION" if position_context.get("has_open_position") else decision.get("status")

    return {
        "agent": "Committee Question",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "scope": "ticker",
        "symbol": symbol,
        "question": question,
        "topic": topic,
        "run_id": cio_report["run_id"],
        "status": status,
        "confidence": decision.get("confidence"),
        "answer_markdown": answer,
        "learning_notes": learning_notes,
        "committee_snapshot": compact_cio_snapshot(cio_report),
        "position_context": position_context,
        "suggested_trade_plan": trade_plan,
    }


def answer_portfolio_question(question, topic):
    run_id = f"{datetime.now().strftime('%Y-%m-%d')}-portfolio-committee-question"
    macro_report = generate_daily_market_intelligence()
    ledger = build_paper_ledger(load_trade_journal())
    data_health = generate_data_health_report(live_checks=False)
    feedback = generate_feedback_report()
    morning = load_latest_morning_brief_json()
    learning_notes = build_portfolio_learning_notes(macro_report, ledger, data_health, feedback, topic)
    answer = format_portfolio_committee_answer(
        question,
        run_id,
        macro_report,
        ledger,
        data_health,
        feedback,
        morning,
        learning_notes,
    )

    macro_assessment = macro_report.get("assessment") or {}
    gate = data_health.get("gate") or {}
    status = "REVIEW ONLY"
    if gate.get("status") == "Pass" and macro_assessment.get("market_regime") == "Risk-On":
        status = "READY FOR SELECTIVE PAPER REVIEW"
    elif gate.get("status") in {"Watch Only", "Blocked"}:
        status = "WATCH ONLY"

    return {
        "agent": "Committee Question",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "scope": "portfolio",
        "symbol": "PORTFOLIO",
        "question": question,
        "topic": topic,
        "run_id": run_id,
        "status": status,
        "confidence": portfolio_confidence(data_health, macro_report),
        "answer_markdown": answer,
        "learning_notes": learning_notes,
        "portfolio_snapshot": {
            "macro": macro_assessment,
            "data_quality": {
                "score": data_health.get("data_quality_score"),
                "gate": gate,
            },
            "account": ledger.get("account"),
            "feedback": feedback.get("trade_expectancy"),
            "morning_created_at": morning.get("created_at"),
        },
    }


def format_ticker_committee_answer(question, cio_report, learning_notes, position_context=None):
    decision = cio_report.get("final_decision") or {}
    plan = cio_report.get("trade_plan") or {}
    conflict_memo = cio_report.get("conflict_memo") or {}
    devils = cio_report.get("devils_advocate") or {}
    position_context = position_context or {}
    management = build_position_management_view(position_context, cio_report)

    lines = [
        "# Committee Answer",
        "",
        f"Question: {question}",
        f"Run ID: {cio_report['run_id']}",
        f"Symbol: {cio_report['symbol']}",
        "",
        "## Collective View",
    ]

    if position_context.get("has_open_position"):
        lines.extend([
            f"- Status: {management['status']}",
            f"- Confidence: {format_number(decision.get('confidence'))}",
            f"- Answer: {management['answer']}",
            f"- Invalidation: {management['invalidation']}",
            f"- Committee setup status: {decision.get('status') or 'n/a'}",
            "",
        ])
        lines.extend(format_position_context(position_context))
        lines.append("")
    else:
        lines.extend([
            f"- Status: {decision.get('status') or 'n/a'}",
            f"- Confidence: {format_number(decision.get('confidence'))}",
            f"- Reason: {decision.get('reason') or 'n/a'}",
            "",
        ])

    lines.extend([
        "## Agent Votes",
        f"- Macro: {cio_report.get('market_regime')} ({cio_report.get('macro_score')}/100)",
        f"- Technical: {cio_report.get('technical_stance')} (confidence {format_number(cio_report.get('technical_confidence'))})",
        f"- Risk: {cio_report.get('risk_decision')}",
        f"- News: {cio_report.get('news_stance') or 'n/a'}; top headline: {cio_report.get('news_top_headline') or 'n/a'}",
        f"- Options: {cio_report.get('options_stance') or 'n/a'}",
        f"- Quant: expectancy {format_number(cio_report.get('backtest_expectancy'))}%, sample {cio_report.get('backtest_sample_size') or 'n/a'}",
        f"- Devil's Advocate: {conflict_memo.get('conflict_count', 0)} conflict(s)",
        "",
        "## Trade/Action Map",
        f"- Action: {plan.get('action') or 'n/a'}",
        f"- Entry trigger: {format_number(plan.get('entry_trigger'))}",
        f"- Suggested entry: {format_number(plan.get('suggested_entry'))}",
        f"- Stop: {format_number(plan.get('stop'))}",
        f"- Target 1: {format_number(plan.get('target_1'))}",
        f"- Target 2: {format_number(plan.get('target_2'))}",
        f"- Target 3: {format_number(plan.get('target_3'))}",
        f"- Position size: {plan.get('position_size_shares') if plan.get('position_size_shares') is not None else 'n/a'} shares",
        f"- Max dollar risk: {format_money(plan.get('max_dollar_risk'))}",
        "",
        "## Main Objections",
    ])
    lines.extend([f"- {item}" for item in devils.get("countercase", [])] or ["- None."])
    lines.append("")
    lines.append("## What This Teaches The System")
    lines.extend([f"- {item}" for item in learning_notes] or ["- No learning notes generated."])
    return "\n".join(lines) + "\n"


def format_portfolio_committee_answer(
    question,
    run_id,
    macro_report,
    ledger,
    data_health,
    feedback,
    morning,
    learning_notes,
):
    assessment = macro_report.get("assessment") or {}
    account = ledger.get("account") or {}
    gate = data_health.get("gate") or {}
    expectancy = feedback.get("trade_expectancy") or {}
    morning_candidates = morning.get("summary", {}) if isinstance(morning.get("summary"), dict) else {}

    lines = [
        "# Committee Answer",
        "",
        f"Question: {question}",
        f"Run ID: {run_id}",
        "",
        "## Collective View",
        f"- Market regime: {assessment.get('market_regime')} ({assessment.get('macro_score')}/100)",
        f"- Data gate: {gate.get('status')} - {gate.get('decision')}",
        f"- Net liquidation: {format_money(account.get('net_liquidation_value'))}",
        f"- Cash: {format_money(account.get('cash_balance'))}",
        f"- Market value: {format_money(account.get('market_value'))}",
        f"- Open risk: {format_money(account.get('open_risk'))}",
        "",
        "## Agent Read",
        f"- Macro: {assessment.get('market_regime')} with confidence {assessment.get('confidence_score')}/100.",
        f"- Risk: open risk is {format_money(account.get('open_risk'))}; buying power is {format_money(account.get('buying_power'))}.",
        f"- Data Quality: score {data_health.get('data_quality_score')}/100; gate {gate.get('status')}.",
        f"- Feedback Loop: closed trades {feedback.get('closed_trades_count')}; win rate {format_number(expectancy.get('win_rate'))}%; average R {format_number(expectancy.get('avg_r'))}.",
        f"- Morning Brief: paper candidates {morning_candidates.get('paper_trade_candidates', 'n/a')}; conditional setups {morning_candidates.get('conditional_setups', 'n/a')}.",
        "",
        "## What This Teaches The System",
    ]
    lines.extend([f"- {item}" for item in learning_notes] or ["- No learning notes generated."])
    return "\n".join(lines) + "\n"


def build_ticker_learning_notes(cio_report, topic):
    decision = cio_report.get("final_decision") or {}
    plan = cio_report.get("trade_plan") or {}
    notes = [
        f"Tag this question as `{topic}` so later outcomes can be compared against this kind of committee judgment.",
        f"Track whether the final status `{decision.get('status')}` was too conservative, too aggressive, or useful.",
    ]

    if plan.get("suggested_entry"):
        notes.append(
            "Monitor whether price reached the suggested entry before target or stop; this helps score patience versus chasing."
        )
    if cio_report.get("backtest_sample_size", 0) and cio_report.get("backtest_sample_size", 0) < 20:
        notes.append("Backtest sample is small, so the review should discount expectancy until more examples accumulate.")
    if cio_report.get("missing_information"):
        notes.append("Use the missing-information list as a data-quality improvement queue.")
    return notes


def build_portfolio_learning_notes(macro_report, ledger, data_health, feedback, topic):
    gate = data_health.get("gate") or {}
    account = ledger.get("account") or {}
    notes = [
        f"Tag this as `{topic}` so future reviews can compare portfolio-level judgment separately from ticker calls.",
        "Save whether the human found the answer helpful; that becomes a lightweight reward signal for the committee.",
    ]
    if gate.get("status") != "Pass":
        notes.append("Data gate is not fully passing, so the committee should explain uncertainty before suggesting action.")
    if account.get("open_risk", 0) > 0:
        notes.append("Compare open risk against later realized/unrealized P&L to improve portfolio sizing discipline.")
    if feedback.get("closed_trades_count", 0) == 0:
        notes.append("No closed simulated trades yet, so feedback is still mostly setup-review based rather than trade-outcome based.")
    return notes


def build_position_context(symbol):
    journal = enrich_trade_metrics(load_trade_journal(), refresh_prices=True)
    if journal.empty:
        return {"has_open_position": False, "symbol": symbol}

    symbol = symbol.upper().strip()
    rows = journal[
        (journal["symbol"].astype(str).str.upper() == symbol)
        & (journal["status"].map(normalize_status).isin(OPEN_STATUSES))
    ].copy()
    if rows.empty:
        return {"has_open_position": False, "symbol": symbol}

    ledger = build_paper_ledger(journal)
    position = next((item for item in ledger.get("positions", []) if item.get("symbol") == symbol), None)
    primary = rows.iloc[0]
    entry = to_float(primary.get("entry"))
    stop = to_float(primary.get("stop"))
    target = to_float(primary.get("target"))
    current_price = to_float(primary.get("current_price")) or (position or {}).get("last_price") or entry
    shares = to_float(primary.get("shares"))
    remaining_risk = max(0, current_price - stop) * shares if stop and current_price and shares else 0
    upside_to_target = max(0, target - current_price) * shares if target and current_price and shares else 0
    distance_to_stop_pct = ((current_price - stop) / current_price * 100) if stop and current_price else None
    distance_to_target_pct = ((target - current_price) / current_price * 100) if target and current_price else None

    return {
        "has_open_position": True,
        "symbol": symbol,
        "trade_id": str(primary.get("id", "")),
        "side": str(primary.get("side", "")),
        "setup_type": str(primary.get("setup_type", "")),
        "source": str(primary.get("source", "")),
        "agent_run_id": str(primary.get("agent_run_id", "")),
        "entry": entry,
        "stop": stop,
        "target": target,
        "shares": shares,
        "current_price": current_price,
        "unrealized_pnl": to_float(primary.get("unrealized_pnl")),
        "planned_risk": to_float(primary.get("planned_risk")),
        "remaining_risk": round(remaining_risk, 2),
        "upside_to_target": round(upside_to_target, 2),
        "distance_to_stop_pct": round(distance_to_stop_pct, 2) if distance_to_stop_pct is not None else None,
        "distance_to_target_pct": round(distance_to_target_pct, 2) if distance_to_target_pct is not None else None,
        "position": position,
        "notes": str(primary.get("notes", "")),
        "thesis": str(primary.get("thesis", "")),
    }


def build_position_management_view(position_context, cio_report):
    if not position_context.get("has_open_position"):
        return {
            "status": "NO OPEN POSITION",
            "answer": "No open simulated position was found for this symbol.",
            "invalidation": "n/a",
        }

    current_price = position_context.get("current_price")
    stop = position_context.get("stop")
    target = position_context.get("target")
    technical = str(cio_report.get("technical_stance") or "").lower()
    risk_decision = str(cio_report.get("risk_decision") or "").lower()
    news = str(cio_report.get("news_stance") or "").lower()

    if stop and current_price and current_price <= stop:
        return {
            "status": "EXIT / STOP HIT",
            "answer": "The position has traded at or below the stop. The paper-trade rule says exit rather than debate the loss.",
            "invalidation": f"Stop was {format_money(stop)} and current price is {format_money(current_price)}.",
        }

    if target and current_price and current_price >= target:
        return {
            "status": "TAKE PROFIT / TARGET HIT",
            "answer": "Target 1 has been reached. The planned paper-trade action is to take profit unless the human explicitly converts it to a trailing-stop plan.",
            "invalidation": f"Target was {format_money(target)} and current price is {format_money(current_price)}.",
        }

    if technical in {"bullish", "neutral"} and risk_decision in {"conditional_setup", "approved_for_paper_trade"} and news != "negative_catalyst":
        return {
            "status": "HOLD WITH ORIGINAL STOP",
            "answer": "Keep holding for now. The position is open, the stop has not been hit, target has not been hit, and the current committee read does not show a fresh veto.",
            "invalidation": f"Cut/exit if price hits the stop near {format_money(stop)}, if news turns clearly negative, or if Risk moves to a veto.",
        }

    return {
        "status": "REVIEW / TIGHTEN RISK",
        "answer": "Do not add to the position. Keep it under review because at least one committee input weakened.",
        "invalidation": f"Use the existing stop near {format_money(stop)} as the hard paper-trade exit unless the human closes it earlier.",
    }


def format_position_context(position_context):
    return [
        "## Current Paper Position",
        f"- Trade ID: {position_context.get('trade_id')}",
        f"- Side / shares: {position_context.get('side')} {format_number(position_context.get('shares'))}",
        f"- Entry: {format_money(position_context.get('entry'))}",
        f"- Current price: {format_money(position_context.get('current_price'))}",
        f"- Stop: {format_money(position_context.get('stop'))}",
        f"- Target 1: {format_money(position_context.get('target'))}",
        f"- Unrealized P&L: {format_money(position_context.get('unrealized_pnl'))}",
        f"- Original planned risk: {format_money(position_context.get('planned_risk'))}",
        f"- Remaining risk to stop: {format_money(position_context.get('remaining_risk'))}",
        f"- Upside to target 1: {format_money(position_context.get('upside_to_target'))}",
        f"- Distance to stop: {format_number(position_context.get('distance_to_stop_pct'))}%",
        f"- Distance to target 1: {format_number(position_context.get('distance_to_target_pct'))}%",
    ]


def compact_cio_snapshot(cio_report):
    return {
        "run_id": cio_report.get("run_id"),
        "symbol": cio_report.get("symbol"),
        "created_at": cio_report.get("created_at"),
        "market_regime": cio_report.get("market_regime"),
        "macro_score": cio_report.get("macro_score"),
        "technical_stance": cio_report.get("technical_stance"),
        "risk_decision": cio_report.get("risk_decision"),
        "news_stance": cio_report.get("news_stance"),
        "options_stance": cio_report.get("options_stance"),
        "backtest_expectancy": cio_report.get("backtest_expectancy"),
        "final_decision": cio_report.get("final_decision"),
        "trade_plan": cio_report.get("trade_plan"),
        "disagreements": cio_report.get("disagreements"),
        "missing_information": cio_report.get("missing_information"),
    }


def save_committee_question_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    symbol = report.get("symbol") or report.get("scope", "portfolio")
    question_id = report.get("question_id", "latest")
    path = REPORTS_DIR / f"{report['created_at'][:10]}_{symbol}_{question_id}.md"
    path.write_text(report["answer_markdown"], encoding="utf-8")
    return path


def load_latest_morning_brief_json():
    if not MORNING_BRIEF_JSON_PATH.exists():
        return {}
    try:
        return json.loads(MORNING_BRIEF_JSON_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def normalize_scope(scope, symbol):
    scope = str(scope or "").strip().lower()
    if scope in {"portfolio", "market", "account", "macro"}:
        return "portfolio"
    if symbol:
        return "ticker"
    return "portfolio"


def infer_symbol_from_question(question):
    text = f" {str(question or '').upper()} "
    candidates = []

    journal = load_trade_journal()
    if not journal.empty:
        for _, row in journal.iterrows():
            symbol = str(row.get("symbol", "")).upper().strip()
            if symbol and normalize_status(row.get("status")) in OPEN_STATUSES:
                candidates.append(symbol)

    candidates.extend(load_watchlist_symbols())
    for symbol in sorted(set(candidates), key=len, reverse=True):
        escaped = re.escape(symbol)
        if re.search(rf"(?<![A-Z0-9.]){escaped}(?![A-Z0-9.])", text):
            return symbol
    return None


def load_watchlist_symbols():
    path = PROJECT_ROOT / "framework" / "watchlist.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    symbols = []
    for item in data.get("symbols", []):
        if isinstance(item, str):
            symbols.append(item.upper())
        else:
            symbol = str(item.get("symbol", "")).upper().strip()
            display_symbol = str(item.get("display_symbol", "")).upper().strip()
            if symbol:
                symbols.append(symbol)
            if display_symbol:
                symbols.append(display_symbol)
    return symbols


def infer_topic(question):
    text = question.lower()
    patterns = {
        "entry_timing": ["entry", "buy zone", "pullback", "breakout", "fill"],
        "risk_sizing": ["risk", "size", "shares", "stop", "position"],
        "portfolio_allocation": ["portfolio", "allocation", "sleeve", "rebalance", "cash"],
        "macro_regime": ["macro", "regime", "fed", "rates", "inflation", "vix"],
        "exit_plan": ["exit", "target", "sell", "take profit"],
        "data_quality": ["data", "confidence", "missing", "source"],
    }
    for topic, words in patterns.items():
        if any(word in text for word in words):
            return topic
    return "general_investment_committee"


def portfolio_confidence(data_health, macro_report):
    data_score = float(data_health.get("data_quality_score") or 0) / 100
    macro_confidence = float((macro_report.get("assessment") or {}).get("confidence_score") or 0) / 100
    return round((data_score * 0.6) + (macro_confidence * 0.4), 2)


def format_number(value):
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def format_money(value):
    if value is None:
        return "n/a"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"
