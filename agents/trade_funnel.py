import json
from datetime import datetime
from pathlib import Path

from data.options_journal import load_options_journal, normalize_status as normalize_option_status
from data.trade_journal import load_trade_journal, normalize_status, to_float


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MORNING_BRIEF_DIR = PROJECT_ROOT / "reports" / "morning_brief"
INTRADAY_DISCOVERY_DIR = PROJECT_ROOT / "reports" / "intraday_discovery"
REPORTS_DIR = PROJECT_ROOT / "reports" / "trade_funnel"


def generate_trade_funnel_report(save=True):
    discovered = {}
    reviewed = {}
    qualified = {}
    equity_records = autonomous_equity_records(load_trade_journal())
    option_records = autonomous_option_records(load_options_journal())
    trade_records = equity_records + option_records
    tracking_start = funnel_tracking_start(trade_records)

    for path in archived_json_files(MORNING_BRIEF_DIR, "morning_brief_*.json"):
        payload = load_json(path)
        if not payload:
            continue
        day = report_day(payload, path)
        if tracking_start and day < tracking_start:
            continue
        for symbol in payload.get("symbols_scanned") or []:
            add_stage_record(discovered, day, symbol, "morning_brief", path)
        for summary in payload.get("committee_summaries") or []:
            symbol = summary.get("symbol")
            add_stage_record(discovered, day, symbol, "morning_brief", path)
            add_stage_record(reviewed, day, symbol, "morning_committee", path, summary.get("run_id"))
        for bucket in ("approved_simulated_trades", "conditional_setups"):
            for idea in payload.get(bucket) or []:
                add_stage_record(
                    qualified,
                    day,
                    idea.get("symbol"),
                    bucket,
                    path,
                    idea.get("run_id"),
                    score=idea.get("score"),
                )

    for path in archived_json_files(INTRADAY_DISCOVERY_DIR, "intraday_discovery_*.json"):
        payload = load_json(path)
        if not payload:
            continue
        day = report_day(payload, path)
        if tracking_start and day < tracking_start:
            continue
        for row in payload.get("material_events") or []:
            add_stage_record(discovered, day, row.get("symbol"), "intraday_material", path)
        for review in payload.get("committee_reviews") or []:
            symbol = review.get("symbol")
            add_stage_record(discovered, day, symbol, "intraday_material", path)
            add_stage_record(reviewed, day, symbol, "intraday_committee", path)
        for idea in payload.get("qualified_candidates") or []:
            add_stage_record(
                qualified,
                day,
                idea.get("symbol"),
                "intraday_qualified",
                path,
                idea.get("run_id"),
                score=idea.get("score"),
            )

    planned = trade_records
    filled = [row for row in trade_records if row["status"] in {"open", "closed"}]
    closed = [row for row in trade_records if row["status"] == "closed"]
    profitable = [row for row in closed if row["realized_pnl"] > 0]
    realized_pnl = round(sum(row["realized_pnl"] for row in closed), 2)

    counts = {
        "discovered": len(discovered),
        "committee_reviewed": len(reviewed),
        "qualified": len(qualified),
        "orders_planned": len(planned),
        "orders_filled": len(filled),
        "trades_closed": len(closed),
        "profitable_trades": len(profitable),
    }
    report = {
        "agent": "Trade Funnel",
        "system_role": "measurement_layer",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "scope": "autonomous_tactical_paper_trading",
        "tracking_start": tracking_start,
        "stage_counts": counts,
        "conversion_rates": {
            "discovery_to_review_pct": pct(counts["committee_reviewed"], counts["discovered"]),
            "review_to_qualified_pct": pct(counts["qualified"], counts["committee_reviewed"]),
            "qualified_to_planned_pct": pct(counts["orders_planned"], counts["qualified"]),
            "planned_to_filled_pct": pct(counts["orders_filled"], counts["orders_planned"]),
            "filled_to_closed_pct": pct(counts["trades_closed"], counts["orders_filled"]),
            "closed_win_rate_pct": pct(counts["profitable_trades"], counts["trades_closed"]),
        },
        "realized_pnl": realized_pnl,
        "completed_autonomous_tactical_trades": counts["trades_closed"],
        "minimum_evidence_target": 30,
        "trades_remaining_to_evidence_target": max(0, 30 - counts["trades_closed"]),
        "risk_expansion_unlocked": counts["trades_closed"] >= 30,
        "active_trade_records": [row for row in trade_records if row["status"] in {"planned", "open"}],
        "closed_trade_records": closed,
        "recent_qualified": list(qualified.values())[-25:],
        "bottleneck": identify_bottleneck(counts),
    }
    if save:
        report["report_path"] = str(save_trade_funnel_report(report))
    return report


def autonomous_equity_records(frame):
    if frame is None or frame.empty:
        return []
    records = []
    for _, row in frame.iterrows():
        source = str(row.get("source") or "").lower()
        setup_type = str(row.get("setup_type") or "").lower()
        if "autonomous paper" not in source or "core" in setup_type:
            continue
        records.append({
            "trade_id": str(row.get("id") or ""),
            "instrument": "equity",
            "symbol": str(row.get("symbol") or "").upper(),
            "strategy": str(row.get("setup_type") or ""),
            "run_id": str(row.get("agent_run_id") or ""),
            "status": normalize_status(row.get("status")),
            "planned_at": str(row.get("opened_at") or ""),
            "closed_at": str(row.get("closed_at") or ""),
            "realized_pnl": to_float(row.get("realized_pnl")),
        })
    return records


def autonomous_option_records(frame):
    if frame is None or frame.empty:
        return []
    records = []
    for _, row in frame.iterrows():
        if "autonomous paper" not in str(row.get("source") or "").lower():
            continue
        records.append({
            "trade_id": str(row.get("id") or ""),
            "instrument": "option",
            "symbol": str(row.get("symbol") or "").upper(),
            "strategy": str(row.get("strategy") or ""),
            "run_id": str(row.get("agent_run_id") or ""),
            "status": normalize_option_status(row.get("status")),
            "planned_at": str(row.get("opened_at") or ""),
            "closed_at": str(row.get("closed_at") or ""),
            "realized_pnl": to_float(row.get("realized_pnl")),
        })
    return records


def add_stage_record(container, day, symbol, source, path, run_id=None, score=None):
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return
    key = f"{day}|{symbol}"
    container[key] = {
        "date": day,
        "symbol": symbol,
        "source": source,
        "run_id": str(run_id or ""),
        "score": score,
        "report": str(path),
    }


def archived_json_files(directory, pattern):
    if not directory.exists():
        return []
    return sorted(path for path in directory.glob(pattern) if path.name not in {
        "daily_morning_brief.json", "intraday_discovery.json"
    })


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def report_day(payload, path):
    created_at = str(payload.get("created_at") or "")
    if len(created_at) >= 10:
        return created_at[:10]
    digits = "".join(character for character in path.stem if character.isdigit())
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else "unknown"


def funnel_tracking_start(records):
    dates = sorted(
        str(row.get("planned_at") or "")[:10]
        for row in records
        if len(str(row.get("planned_at") or "")) >= 10
    )
    return dates[0] if dates else None


def pct(numerator, denominator):
    return round(100 * numerator / denominator, 1) if denominator else 0.0


def identify_bottleneck(counts):
    stages = [
        ("discovery_to_review", counts["committee_reviewed"], counts["discovered"]),
        ("review_to_qualified", counts["qualified"], counts["committee_reviewed"]),
        ("qualified_to_planned", counts["orders_planned"], counts["qualified"]),
        ("planned_to_filled", counts["orders_filled"], counts["orders_planned"]),
        ("filled_to_closed", counts["trades_closed"], counts["orders_filled"]),
    ]
    eligible = [(name, pct(after, before)) for name, after, before in stages if before]
    if not eligible:
        return {"stage": "insufficient_history", "conversion_pct": 0.0}
    name, rate = min(eligible, key=lambda item: item[1])
    return {"stage": name, "conversion_pct": rate}


def format_trade_funnel_report(report):
    counts = report.get("stage_counts") or {}
    rates = report.get("conversion_rates") or {}
    lines = [
        "# Autonomous Tactical Trade Funnel",
        "",
        f"Created At: {report.get('created_at')}",
        f"Tracking Start: {report.get('tracking_start') or 'No autonomous order history'}",
        f"Risk Gate: {report.get('completed_autonomous_tactical_trades', 0)}/30 completed; "
        f"{'unlocked' if report.get('risk_expansion_unlocked') else 'constrained'}",
        f"Realized P&L: ${float(report.get('realized_pnl') or 0):,.2f}",
        "",
        "## Stages",
        f"- Discovered: {counts.get('discovered', 0)}",
        f"- Committee reviewed: {counts.get('committee_reviewed', 0)} ({rates.get('discovery_to_review_pct', 0):.1f}%)",
        f"- Qualified: {counts.get('qualified', 0)} ({rates.get('review_to_qualified_pct', 0):.1f}%)",
        f"- Orders planned: {counts.get('orders_planned', 0)} ({rates.get('qualified_to_planned_pct', 0):.1f}%)",
        f"- Orders filled: {counts.get('orders_filled', 0)} ({rates.get('planned_to_filled_pct', 0):.1f}%)",
        f"- Trades closed: {counts.get('trades_closed', 0)} ({rates.get('filled_to_closed_pct', 0):.1f}%)",
        f"- Profitable trades: {counts.get('profitable_trades', 0)} ({rates.get('closed_win_rate_pct', 0):.1f}%)",
        "",
        f"Bottleneck: {(report.get('bottleneck') or {}).get('stage')} at "
        f"{float((report.get('bottleneck') or {}).get('conversion_pct') or 0):.1f}% conversion.",
    ]
    return "\n".join(lines) + "\n"


def save_trade_funnel_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    latest_json = REPORTS_DIR / "trade_funnel.json"
    latest_md = REPORTS_DIR / "trade_funnel.md"
    archive_json = REPORTS_DIR / f"trade_funnel_{timestamp}.json"
    archive_md = REPORTS_DIR / f"trade_funnel_{timestamp}.md"
    payload = json.dumps(report, indent=2, default=str)
    markdown = format_trade_funnel_report(report)
    latest_json.write_text(payload, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
    archive_json.write_text(payload, encoding="utf-8")
    archive_md.write_text(markdown, encoding="utf-8")
    return latest_md
