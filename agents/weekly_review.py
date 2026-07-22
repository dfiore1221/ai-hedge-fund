import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from data.trade_journal import CLOSED_STATUS, enrich_trade_metrics, load_trade_journal
from memory.research_memory import get_recent_daily_setup_reviews, save_agent_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "weekly_review"


def generate_weekly_review(end_day=None):
    end_date = parse_end_date(end_day)
    start_date = end_date - timedelta(days=6)
    setup_reviews = [
        item for item in get_recent_daily_setup_reviews(limit=500)
        if start_date <= datetime.fromisoformat(item["review_date"]).date() <= end_date
    ]
    trades = enrich_trade_metrics(load_trade_journal())
    closed_trades = trades[trades["status"] == CLOSED_STATUS].copy() if not trades.empty else trades
    open_trades = trades[trades["status"].astype(str).str.lower().isin({"open", "planned"})].copy() if not trades.empty else trades

    report = {
        "agent": "Weekly Review",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "week_start": start_date.isoformat(),
        "week_end": end_date.isoformat(),
        "setup_summary": summarize_setups(setup_reviews),
        "trade_summary": summarize_trades(closed_trades, open_trades, start_date, end_date),
        "target_stop_review": summarize_targets_and_stops(setup_reviews, closed_trades),
        "accuracy_review": build_accuracy_review(setup_reviews),
        "lessons": build_weekly_lessons(setup_reviews, closed_trades),
        "reviewed_setups": setup_reviews,
    }
    save_agent_report(
        run_id=f"{end_date.isoformat()}-weekly-review",
        agent_name="Weekly Review",
        output=report,
        symbol="MARKET",
        stance=report["accuracy_review"]["dominant_read"],
        confidence=100,
    )
    return report


def summarize_setups(setups):
    total = len(setups)
    entries = [item for item in setups if item.get("entered")]
    target_hits = [item for item in setups if item.get("hit_target_1")]
    stop_hits = [item for item in setups if item.get("hit_stop")]
    active = [item for item in setups if item.get("result") in {"OPEN / NO TARGET 1", "TARGET 1 HIT", "STOP FIRST", "STOP TOUCHED"}]
    pnl_values = [item.get("pnl_pct") for item in entries if item.get("pnl_pct") is not None]

    return {
        "setups_reviewed": total,
        "entries_triggered": len(entries),
        "target_1_hits": len(target_hits),
        "stop_hits": len(stop_hits),
        "active_or_resolved_setups": len(active),
        "entry_rate_pct": pct(len(entries), total),
        "target_hit_rate_on_entries_pct": pct(len(target_hits), len(entries)),
        "stop_hit_rate_on_entries_pct": pct(len(stop_hits), len(entries)),
        "avg_entered_pnl_pct": average(pnl_values),
    }


def summarize_trades(closed_trades, open_trades, start_date, end_date):
    if closed_trades is None or closed_trades.empty:
        week_closed = pd.DataFrame()
    else:
        week_closed = closed_trades.copy()
        week_closed["closed_date"] = pd.to_datetime(week_closed["closed_at"], errors="coerce").dt.date
        week_closed = week_closed[
            (week_closed["closed_date"] >= start_date)
            & (week_closed["closed_date"] <= end_date)
        ]

    realized = numeric_sum(week_closed, "realized_pnl")
    r_values = pd.to_numeric(week_closed.get("r_multiple"), errors="coerce").fillna(0) if not week_closed.empty else []
    wins = int((week_closed.get("outcome", pd.Series(dtype=str)).astype(str).str.lower() == "win").sum()) if not week_closed.empty else 0

    return {
        "closed_trades_this_week": len(week_closed),
        "open_or_planned_trades": len(open_trades) if open_trades is not None else 0,
        "realized_pnl": round(realized, 2),
        "win_rate_pct": pct(wins, len(week_closed)),
        "avg_r": round(float(r_values.mean()), 2) if len(week_closed) else 0,
        "total_r": round(float(r_values.sum()), 2) if len(week_closed) else 0,
    }


def summarize_targets_and_stops(setups, closed_trades):
    by_symbol = {}
    for setup in setups:
        symbol = setup.get("symbol")
        if not symbol:
            continue
        row = by_symbol.setdefault(symbol, {
            "symbol": symbol,
            "setups": 0,
            "entries": 0,
            "target_1_hits": 0,
            "stop_hits": 0,
            "avg_pnl_pct": [],
        })
        row["setups"] += 1
        row["entries"] += int(bool(setup.get("entered")))
        row["target_1_hits"] += int(bool(setup.get("hit_target_1")))
        row["stop_hits"] += int(bool(setup.get("hit_stop")))
        if setup.get("pnl_pct") is not None:
            row["avg_pnl_pct"].append(setup["pnl_pct"])

    rows = []
    for row in by_symbol.values():
        values = row.pop("avg_pnl_pct")
        row["avg_pnl_pct"] = average(values)
        rows.append(row)

    return sorted(rows, key=lambda item: (item["target_1_hits"], item["avg_pnl_pct"] or -999), reverse=True)


def build_accuracy_review(setups):
    if not setups:
        return {
            "dominant_read": "NO DATA",
            "accurate": [],
            "way_off": [],
        }

    accurate = []
    way_off = []
    for item in setups:
        symbol = item.get("symbol")
        pnl = item.get("pnl_pct")
        if item.get("hit_target_1"):
            accurate.append(f"{symbol}: hit Target 1 after entry.")
        elif item.get("hit_stop"):
            way_off.append(f"{symbol}: hit stop after entry.")
        elif pnl is not None and pnl >= 2:
            accurate.append(f"{symbol}: entered and closed review window up {pnl:.2f}% from entry.")
        elif pnl is not None and pnl <= -2:
            way_off.append(f"{symbol}: entered and closed review window down {pnl:.2f}% from entry.")

    dominant = "MIXED"
    if len(accurate) > len(way_off) * 1.5:
        dominant = "MORE ACCURATE THAN MISSED"
    elif len(way_off) > len(accurate) * 1.5:
        dominant = "MORE MISSES THAN ACCURATE"

    return {
        "dominant_read": dominant,
        "accurate": accurate[:10],
        "way_off": way_off[:10],
    }


def build_weekly_lessons(setups, closed_trades):
    summary = summarize_setups(setups)
    lessons = []

    if summary["setups_reviewed"] == 0:
        return ["No setup reviews were available for the week."]
    if summary["target_hit_rate_on_entries_pct"] == 0 and summary["entries_triggered"] > 0:
        lessons.append("No entered setup hit Target 1 this week; evaluate whether targets are too ambitious or the review window is too short.")
    if summary["stop_hit_rate_on_entries_pct"] > 25:
        lessons.append("More than a quarter of entered setups hit stops; review entry quality and stop placement.")
    if summary["avg_entered_pnl_pct"] is not None and summary["avg_entered_pnl_pct"] < 0:
        lessons.append("Average entered setup P&L was negative; check whether morning entries are triggering too easily.")
    if closed_trades is None or closed_trades.empty:
        lessons.append("No closed paper trades yet; weekly accuracy still relies mostly on setup tracking rather than realized trade outcomes.")

    lessons.append("Use Friday review to tune entries, stop distance, target distance, and holding-period assumptions for the next week.")
    return lessons


def format_weekly_review(report):
    setup = report["setup_summary"]
    trades = report["trade_summary"]
    accuracy = report["accuracy_review"]
    lines = [
        "# Weekly AIFundOS Review",
        "",
        f"Created At: {report['created_at']}",
        f"Week: {report['week_start']} to {report['week_end']}",
        "",
        "## Setup Accuracy",
        f"- Setups Reviewed: {setup['setups_reviewed']}",
        f"- Entries Triggered: {setup['entries_triggered']} ({fmt_pct(setup['entry_rate_pct'])})",
        f"- Target 1 Hit Rate On Entries: {fmt_pct(setup['target_hit_rate_on_entries_pct'])}",
        f"- Stop Hit Rate On Entries: {fmt_pct(setup['stop_hit_rate_on_entries_pct'])}",
        f"- Average Entered P&L: {fmt_pct(setup['avg_entered_pnl_pct'])}",
        f"- Dominant Read: {accuracy['dominant_read']}",
        "",
        "## Paper Trade Outcomes",
        f"- Closed Trades This Week: {trades['closed_trades_this_week']}",
        f"- Open / Planned Trades: {trades['open_or_planned_trades']}",
        f"- Realized P&L: {fmt_money(trades['realized_pnl'])}",
        f"- Win Rate: {fmt_pct(trades['win_rate_pct'])}",
        f"- Avg R: {trades['avg_r']:.2f}",
        f"- Total R: {trades['total_r']:.2f}",
        "",
        "## Accurate Calls",
    ]
    lines.extend([f"- {item}" for item in accuracy["accurate"]] or ["- None flagged yet."])
    lines.append("")
    lines.append("## Way Off / Needs Review")
    lines.extend([f"- {item}" for item in accuracy["way_off"]] or ["- None flagged yet."])
    lines.append("")
    lines.append("## Target / Stop Table")
    lines.extend([
        "| Symbol | Setups | Entries | Target 1 Hits | Stop Hits | Avg P&L |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for row in report["target_stop_review"][:25]:
        lines.append(
            f"| {row['symbol']} | {row['setups']} | {row['entries']} | {row['target_1_hits']} | "
            f"{row['stop_hits']} | {fmt_pct(row['avg_pnl_pct'])} |"
        )

    lines.append("")
    lines.append("## Lessons For Next Week")
    lines.extend([f"- {lesson}" for lesson in report["lessons"]])
    return "\n".join(lines) + "\n"


def save_weekly_review_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = report["week_end"].replace("-", "")
    latest_md = REPORTS_DIR / "weekly_review.md"
    latest_json = REPORTS_DIR / "weekly_review.json"
    archive_md = REPORTS_DIR / f"weekly_review_{stamp}.md"
    archive_json = REPORTS_DIR / f"weekly_review_{stamp}.json"
    markdown = format_weekly_review(report)
    latest_md.write_text(markdown, encoding="utf-8")
    latest_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    archive_md.write_text(markdown, encoding="utf-8")
    archive_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return latest_md


def parse_end_date(end_day):
    if not end_day or str(end_day).lower() in {"today", "week"}:
        return date.today()
    return datetime.strptime(str(end_day), "%Y-%m-%d").date()


def average(values):
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 2)


def pct(numerator, denominator):
    if not denominator:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def numeric_sum(frame, column):
    if frame is None or frame.empty or column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())


def fmt_pct(value):
    if value is None:
        return "n/a"
    return f"{float(value):.2f}%"


def fmt_money(value):
    if value is None:
        return "n/a"
    return f"${float(value):,.2f}"
