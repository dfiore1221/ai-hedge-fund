from datetime import datetime
from pathlib import Path

import pandas as pd

from data.trade_journal import CLOSED_STATUS, enrich_trade_metrics, load_trade_journal
from memory.research_memory import get_agent_reports_for_run


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "feedback"


def generate_feedback_report():
    trades = enrich_trade_metrics(load_trade_journal())
    closed_trades = trades[trades["status"] == CLOSED_STATUS].copy() if not trades.empty else trades
    closed_trades = add_decision_context(closed_trades)

    return {
        "agent": "Feedback Loop",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "closed_trades_count": len(closed_trades),
        "linked_trades_count": count_linked_trades(closed_trades),
        "trade_expectancy": summarize_closed_trades(closed_trades),
        "by_setup_type": summarize_group(closed_trades, "setup_type"),
        "by_source": summarize_group(closed_trades, "source"),
        "by_symbol": summarize_group(closed_trades, "symbol"),
        "by_decision_tier": summarize_group(closed_trades, "decision_tier"),
        "agent_scorecard": build_agent_scorecard(closed_trades),
        "lessons": extract_lessons(closed_trades),
        "missing_information": collect_missing_information(closed_trades),
    }


def add_decision_context(closed_trades):
    if closed_trades is None or closed_trades.empty:
        return pd.DataFrame(columns=list(load_trade_journal().columns) + ["decision_tier"])

    closed_trades = closed_trades.copy()
    closed_trades["decision_tier"] = ""
    closed_trades["cio_confidence"] = ""

    for index, trade in closed_trades.iterrows():
        run_id = str(trade.get("agent_run_id", "")).strip()
        if not run_id:
            continue
        cio = find_agent_output(run_id, "Chief Investment Officer")
        if not cio:
            continue
        final_decision = cio.get("final_decision") or {}
        closed_trades.at[index, "decision_tier"] = final_decision.get("status", "")
        closed_trades.at[index, "cio_confidence"] = final_decision.get("confidence", "")

    return closed_trades


def summarize_closed_trades(closed_trades):
    if closed_trades is None or closed_trades.empty:
        return empty_summary()

    outcomes = closed_trades["outcome"].astype(str).str.lower()
    wins = int((outcomes == "win").sum())
    losses = int((outcomes == "loss").sum())
    count = len(closed_trades)
    r_values = pd.to_numeric(closed_trades.get("r_multiple"), errors="coerce").fillna(0)
    pnl_values = pd.to_numeric(closed_trades.get("realized_pnl"), errors="coerce").fillna(0)

    return {
        "count": count,
        "wins": wins,
        "losses": losses,
        "win_rate": pct(wins, count),
        "avg_r": float(r_values.mean()) if count else 0,
        "total_r": float(r_values.sum()),
        "total_pnl": float(pnl_values.sum()),
    }


def summarize_group(closed_trades, column):
    if closed_trades is None or closed_trades.empty or column not in closed_trades.columns:
        return []

    rows = []
    working = closed_trades.copy()
    working[column] = working[column].fillna("").replace("", "Unclassified")
    for value, group in working.groupby(column, dropna=False):
        summary = summarize_closed_trades(group)
        summary[column] = value
        rows.append(summary)

    return sorted(rows, key=lambda item: (item["count"], item["avg_r"]), reverse=True)


def build_agent_scorecard(closed_trades):
    if closed_trades is None or closed_trades.empty:
        return []

    scores = {}
    for _, trade in closed_trades.iterrows():
        run_id = str(trade.get("agent_run_id", "")).strip()
        if not run_id:
            continue
        for report in get_agent_reports_for_run(run_id):
            agent_name = report["agent_name"]
            score, call = score_agent_call(agent_name, report.get("output") or {}, trade)
            if score is None:
                continue
            current = scores.setdefault(agent_name, {
                "agent_name": agent_name,
                "graded_calls": 0,
                "score": 0,
                "positive_calls": 0,
                "negative_calls": 0,
                "neutral_calls": 0,
                "latest_call": "",
            })
            current["graded_calls"] += 1
            current["score"] += score
            current["latest_call"] = call
            if score > 0:
                current["positive_calls"] += 1
            elif score < 0:
                current["negative_calls"] += 1
            else:
                current["neutral_calls"] += 1

    for row in scores.values():
        row["avg_score"] = row["score"] / row["graded_calls"] if row["graded_calls"] else 0

    return sorted(scores.values(), key=lambda item: item["avg_score"], reverse=True)


def score_agent_call(agent_name, output, trade):
    if not output:
        return None, ""

    outcome = str(trade.get("outcome", "")).lower()
    side = str(trade.get("side", "long")).lower()
    if outcome not in {"win", "loss"}:
        return 0, "breakeven/no graded outcome"

    if agent_name == "Technical Analyst":
        stance = output.get("stance")
        return score_directional_stance(stance, side, outcome), f"stance={stance}"

    if agent_name == "Risk Manager":
        decision = output.get("decision")
        return score_risk_decision(decision, outcome), f"decision={decision}"

    if agent_name == "Chief Investment Officer":
        status = (output.get("final_decision") or {}).get("status")
        return score_cio_decision(status, outcome), f"status={status}"

    if agent_name == "Options Flow":
        stance = output.get("stance")
        return score_directional_stance(stance, side, outcome), f"stance={stance}"

    if agent_name == "Quant Researcher":
        expectancy = output.get("expectancy_pct")
        if expectancy is None:
            return None, "no expectancy"
        positive = float(expectancy) > 0
        return (1 if (positive and outcome == "win") or (not positive and outcome == "loss") else -1), (
            f"expectancy={expectancy}"
        )

    if agent_name == "Market Intelligence":
        regime = (output.get("assessment") or {}).get("market_regime")
        return score_macro_regime(regime, side, outcome), f"regime={regime}"

    return None, ""


def score_directional_stance(stance, side, outcome):
    stance = str(stance or "").lower()
    bullish = stance in {"bullish", "bullish_positioning"}
    bearish = stance in {"bearish", "bearish_positioning", "no_trade"}

    if side == "short":
        bullish, bearish = bearish, bullish

    if outcome == "win":
        if bullish:
            return 1
        if bearish:
            return -1
        return 0

    if bearish:
        return 1
    if bullish:
        return -1
    return 0


def score_risk_decision(decision, outcome):
    decision = str(decision or "").lower()
    if outcome == "win":
        if decision == "approved_for_paper_trade":
            return 1
        if decision == "conditional_setup":
            return 0.5
        if decision in {"watchlist_setup", "veto"}:
            return -1
        return 0

    if decision == "veto":
        return 1
    if decision == "watchlist_setup":
        return 0.5
    if decision == "conditional_setup":
        return 0
    if decision == "approved_for_paper_trade":
        return -1
    return 0


def score_cio_decision(status, outcome):
    status = str(status or "").upper()
    if outcome == "win":
        if status == "PAPER TRADE ONLY":
            return 1
        if status == "CONDITIONAL SETUP":
            return 0.5
        if status in {"WATCHLIST SETUP", "NO TRADE", "NEEDS DATA"}:
            return -1
        return 0

    if status in {"NO TRADE", "NEEDS DATA"}:
        return 1
    if status == "WATCHLIST SETUP":
        return 0.5
    if status == "CONDITIONAL SETUP":
        return 0
    if status == "PAPER TRADE ONLY":
        return -1
    return 0


def score_macro_regime(regime, side, outcome):
    regime = str(regime or "").lower()
    supportive = regime == "risk-on" or (regime == "neutral" and side == "long")
    cautious = regime == "risk-off"
    if side == "short":
        supportive, cautious = cautious, supportive

    if outcome == "win":
        if supportive:
            return 0.5
        if cautious:
            return -0.5
        return 0

    if cautious:
        return 0.5
    if supportive:
        return -0.5
    return 0


def find_agent_output(run_id, agent_name):
    for report in get_agent_reports_for_run(run_id):
        if report["agent_name"] == agent_name:
            return report.get("output")
    return None


def count_linked_trades(closed_trades):
    if closed_trades is None or closed_trades.empty:
        return 0
    return int(closed_trades["agent_run_id"].astype(str).str.strip().ne("").sum())


def extract_lessons(closed_trades, limit=10):
    if closed_trades is None or closed_trades.empty:
        return []

    lessons = []
    for _, trade in closed_trades.sort_values("closed_at", ascending=False).iterrows():
        text = str(trade.get("lessons", "")).strip()
        if text:
            lessons.append({
                "symbol": trade.get("symbol"),
                "closed_at": trade.get("closed_at"),
                "outcome": trade.get("outcome"),
                "r_multiple": trade.get("r_multiple"),
                "lesson": text,
            })
        if len(lessons) >= limit:
            break
    return lessons


def collect_missing_information(closed_trades):
    missing = []
    if closed_trades is None or closed_trades.empty:
        return [
            "No closed simulated trades yet; feedback loop is ready but has no outcomes to learn from.",
            "Close trades with exit price and lessons to build expectancy by setup type and source.",
        ]

    if count_linked_trades(closed_trades) < len(closed_trades):
        missing.append("Some closed trades do not have an agent_run_id, so agent-level scoring is incomplete.")
    if closed_trades["setup_type"].astype(str).str.strip().eq("").any():
        missing.append("Some closed trades are missing setup_type, limiting setup-level expectancy.")
    if closed_trades["lessons"].astype(str).str.strip().eq("").any():
        missing.append("Some closed trades are missing lessons learned.")

    return missing


def format_feedback_report(report):
    lines = [
        "# Decision Feedback Loop",
        "",
        f"Created At: {report['created_at']}",
        f"Closed Trades: {report['closed_trades_count']}",
        f"Linked Agent Decisions: {report['linked_trades_count']}",
        "",
        "## Trade Expectancy",
    ]
    lines.extend(format_summary_lines(report["trade_expectancy"]))

    sections = [
        ("Setup Type", "setup_type", report["by_setup_type"]),
        ("Source", "source", report["by_source"]),
        ("Symbol", "symbol", report["by_symbol"]),
        ("Decision Tier", "decision_tier", report["by_decision_tier"]),
    ]
    for title, key, rows in sections:
        lines.extend(["", f"## By {title}"])
        lines.extend(format_group_table(key, rows))

    lines.extend(["", "## Agent Scorecard"])
    lines.extend(format_agent_table(report["agent_scorecard"]))

    lines.extend(["", "## Recent Lessons"])
    if report["lessons"]:
        for lesson in report["lessons"]:
            lines.append(
                f"- {lesson['symbol']} ({lesson['outcome']}, {lesson['r_multiple']}R): {lesson['lesson']}"
            )
    else:
        lines.append("- None logged yet.")

    lines.extend(["", "## Missing Information"])
    lines.extend([f"- {item}" for item in report["missing_information"]] or ["- None."])

    return "\n".join(lines) + "\n"


def format_summary_lines(summary):
    return [
        f"- Count: {summary['count']}",
        f"- Wins: {summary['wins']}",
        f"- Losses: {summary['losses']}",
        f"- Win Rate: {summary['win_rate']:.1f}%",
        f"- Average R: {summary['avg_r']:.2f}",
        f"- Total R: {summary['total_r']:.2f}",
        f"- Total P&L: {summary['total_pnl']:.2f}",
    ]


def format_group_table(key, rows):
    if not rows:
        return ["- Not enough closed-trade data yet."]

    lines = ["| Group | Trades | Win Rate | Avg R | Total R | Total P&L |", "|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(
            f"| {row[key]} | {row['count']} | {row['win_rate']:.1f}% | "
            f"{row['avg_r']:.2f} | {row['total_r']:.2f} | {row['total_pnl']:.2f} |"
        )
    return lines


def format_agent_table(rows):
    if not rows:
        return ["- No linked closed trades yet."]

    lines = ["| Agent | Calls | Avg Score | Pos | Neg | Neutral | Latest Call |", "|---|---:|---:|---:|---:|---:|---|"]
    for row in rows:
        lines.append(
            f"| {row['agent_name']} | {row['graded_calls']} | {row['avg_score']:.2f} | "
            f"{row['positive_calls']} | {row['negative_calls']} | {row['neutral_calls']} | "
            f"{row['latest_call']} |"
        )
    return lines


def save_feedback_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "decision_feedback_report.md"
    path.write_text(format_feedback_report(report), encoding="utf-8")
    return path


def empty_summary():
    return {
        "count": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0,
        "avg_r": 0,
        "total_r": 0,
        "total_pnl": 0,
    }


def pct(value, total):
    return (value / total * 100) if total else 0
