import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agents.market_intelligence import generate_daily_market_intelligence
from agents.news_intelligence import collect_overnight_news
from agents.position_manager import (
    format_position_manager_report,
    generate_position_manager_report,
)
from data.paper_fills import fetch_price_snapshot, format_paper_fill_report, process_paper_fills
from data.trade_journal import load_trade_journal, normalize_status
from delivery.email_delivery import load_email_config, send_email
from delivery.email_retry import queue_email
from memory.research_memory import save_agent_report
from security.checks import redact_text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "intraday_monitor"
ALERT_STATE_PATH = REPORTS_DIR / "alert_state.json"
ENTRY_TRIGGER_LOG_PATH = REPORTS_DIR / "entry_trigger_log.json"
MORNING_BRIEF_JSON_PATH = PROJECT_ROOT / "reports" / "morning_brief" / "daily_morning_brief.json"
AUTONOMY_REPORTS_DIR = PROJECT_ROOT / "reports" / "autonomy"
EASTERN = ZoneInfo("America/New_York")
ACTION_ALERTS = {
    "EXIT",
    "TAKE PROFIT",
    "REVIEW EXIT",
    "REASSESS",
    "REVIEW PLAN",
    "CHECK LEVELS",
}
MARKET_MOVE_THRESHOLD = 1.5
VIX_SPIKE_THRESHOLD = 8.0
NEWS_SCORE_THRESHOLD = 4
ETF_SYMBOLS = {
    "DIA",
    "IWM",
    "QQQ",
    "SMH",
    "SPY",
    "VOO",
    "XLB",
    "XLC",
    "XLE",
    "XLF",
    "XLI",
    "XLK",
    "XLP",
    "XLRE",
    "XLU",
    "XLV",
    "XLY",
}


def run_intraday_monitor(send_alert=True, dry_run=False, apply_fills=False, save_memory=True):
    created_at = datetime.now(EASTERN).isoformat(timespec="seconds")
    run_id = f"{date.today().isoformat()}-intraday-monitor"

    fill_result = process_paper_fills(apply=apply_fills, allow_entry_fills=False)
    position_report = generate_position_manager_report(use_llm=False, save_memory=False)
    market_report = generate_daily_market_intelligence()
    symbols = active_symbols()
    entry_trigger_check = check_morning_brief_entry_triggers()
    news_reports = collect_news_for_symbols(symbols)

    alerts = []
    alerts.extend(build_fill_alerts(fill_result))
    alerts.extend(entry_trigger_check["alerts"])
    alerts.extend(build_position_alerts(position_report))
    alerts.extend(build_market_alerts(market_report))
    alerts.extend(build_news_alerts(news_reports))
    alerts = dedupe_alerts(alerts)
    checked_symbols = sorted(set(symbols + entry_trigger_check["checked_symbols"]))

    state = load_alert_state()
    acknowledged_ids = set(state["sent_alert_ids"]) | set(state.get("queued_alert_ids", []))
    new_alerts = [alert for alert in alerts if alert["alert_id"] not in acknowledged_ids]
    email_result = None

    report = {
        "agent": "Intraday Monitor",
        "system_role": "tool_workflow",
        "layer": "Intraday Alerting",
        "run_id": run_id,
        "created_at": created_at,
        "send_alert": send_alert,
        "dry_run": dry_run,
        "apply_fills": apply_fills,
        "entry_policy": "autonomous_for_selected_paper_orders; monitor_only_for_unselected_setups",
        "checked_symbols": checked_symbols,
        "alert_count": len(alerts),
        "new_alert_count": len(new_alerts),
        "alerts": alerts,
        "new_alerts": new_alerts,
        "position_manager": position_report,
        "paper_fill_check": {
            "applied": fill_result.get("applied"),
            "events": fill_result.get("events", []),
            "applied_events": fill_result.get("applied_events", []),
            "checked_symbols": fill_result.get("checked_symbols", []),
            "allow_entry_fills": fill_result.get("allow_entry_fills", True),
            "allow_exit_fills": fill_result.get("allow_exit_fills", True),
        },
        "entry_trigger_check": {
            "source": entry_trigger_check["source"],
            "checked_symbols": entry_trigger_check["checked_symbols"],
            "triggered": entry_trigger_check["triggered"],
            "status": entry_trigger_check["status"],
        },
        "market": market_report,
        "news": news_reports,
        "email_result": None,
    }

    output_path = save_intraday_monitor_report(report)
    report["report_path"] = str(output_path)
    save_entry_trigger_log(entry_trigger_check["triggered"])

    if send_alert and new_alerts:
        subject = build_subject(new_alerts)
        body = create_intraday_email_body(report)
        if dry_run:
            load_email_config()
            email_result = {
                "dry_run": True,
                "subject": subject,
                "body_preview": body,
            }
        else:
            try:
                email_result = send_email(subject, body, attachment_path=output_path)
            except Exception as exc:
                pending_path = queue_email(
                    subject,
                    body,
                    attachment_path=output_path,
                    kind="intraday_alert",
                    error=exc,
                    expiry_hours=4,
                    dedupe_key="|".join(sorted(alert["alert_id"] for alert in new_alerts)),
                    metadata={
                        "alert_state_path": str(ALERT_STATE_PATH),
                        "alert_ids": [alert["alert_id"] for alert in new_alerts],
                    },
                )
                email_result = {
                    "sent": False,
                    "queued": True,
                    "pending_path": str(pending_path),
                    "error": str(exc),
                }
                mark_alerts_queued(state, new_alerts)
                save_alert_state(state)
            else:
                mark_alerts_sent(state, new_alerts)
                save_alert_state(state)

    report["email_result"] = email_result
    save_intraday_monitor_report(report)

    if save_memory:
        save_agent_report(
            run_id=run_id,
            agent_name="Intraday Monitor",
            output=summarize_for_memory(report),
            symbol="PORTFOLIO",
            stance="alerts" if new_alerts else "quiet",
            confidence=90,
        )

    return report


def active_symbols():
    frame = load_trade_journal()
    symbols = []
    for _, row in frame.iterrows():
        if normalize_status(row.get("status")) not in {"open", "planned"}:
            continue
        symbol = str(row.get("symbol", "")).upper().strip()
        if symbol:
            symbols.append(symbol)
    return sorted(set(symbols))


def check_morning_brief_entry_triggers():
    ideas = load_morning_brief_entry_ideas()
    if not ideas:
        return {
            "source": str(MORNING_BRIEF_JSON_PATH),
            "status": "no_setups",
            "checked_symbols": [],
            "triggered": [],
            "alerts": [],
        }

    journal_keys = planned_journal_keys()
    filtered = [
        idea for idea in ideas
        if (idea.get("run_id") and idea.get("run_id") not in journal_keys["run_ids"])
        and (idea.get("symbol") and idea.get("symbol") not in journal_keys["symbols"])
    ]
    symbols = sorted({idea["symbol"] for idea in filtered})
    price_snapshot = fetch_price_snapshot(symbols)
    prices = price_snapshot.get("prices", {})
    metadata = price_snapshot.get("metadata", {})
    alerts = []
    triggered = []

    for idea in filtered:
        symbol = idea["symbol"]
        latest = safe_float(prices.get(symbol))
        threshold = safe_float(idea.get("suggested_entry") or idea.get("entry_trigger"))
        if latest <= 0 or threshold <= 0:
            continue

        side = str(idea.get("side") or "long").lower().strip()
        hit = latest <= threshold if side != "short" else latest >= threshold
        if not hit:
            continue

        selection_reason = autonomous_selection_reason(symbol, idea.get("run_id"))

        event = {
            "event_type": "entry_triggered",
            "symbol": symbol,
            "side": side,
            "run_id": idea.get("run_id"),
            "decision": idea.get("decision"),
            "latest_price": round_number(latest),
            "entry_threshold": round_number(threshold),
            "entry_trigger": round_number(idea.get("entry_trigger")),
            "suggested_entry": round_number(idea.get("suggested_entry")),
            "stop": round_number(idea.get("stop")),
            "target_1": round_number(idea.get("target_1")),
            "score": round_number(idea.get("score")),
            "reason": idea.get("reason"),
            "autonomous_selection_reason": selection_reason,
            "price_metadata": metadata.get(symbol, {}),
            "timestamp": datetime.now(EASTERN).isoformat(timespec="seconds"),
            "action_required": (
                "No human approval is required. This setup was not selected as an autonomous working "
                "order, so the trigger is logged for Committee review and learning only."
            ),
        }
        triggered.append(event)
        alerts.append(build_alert(
            kind="entry_trigger",
            severity="medium",
            symbol=symbol,
            title=f"{symbol} entry reached - setup was not selected",
            message=(
                f"{symbol} hit the entry threshold near {threshold:.2f}; latest price {latest:.2f}. "
                f"No paper trade was placed because: {selection_reason}"
            ),
            payload=event,
            dedupe_key=f"{date.today().isoformat()}|entry_trigger|{symbol}|{idea.get('run_id')}|{threshold:.4f}",
        ))

    return {
        "source": str(MORNING_BRIEF_JSON_PATH),
        "status": price_snapshot.get("provider_status", "checked"),
        "checked_symbols": symbols,
        "triggered": triggered,
        "alerts": alerts,
    }


def autonomous_selection_reason(symbol, run_id=None):
    """Return the latest planner disposition for a setup without implying human approval."""
    symbol = str(symbol or "").upper().strip()
    run_id = str(run_id or "").strip()
    if not symbol or not AUTONOMY_REPORTS_DIR.exists():
        return "The Committee did not create an autonomous working order for this setup."

    day_prefix = datetime.now(EASTERN).strftime("autonomy_%Y%m%d_")
    report_paths = sorted(
        AUTONOMY_REPORTS_DIR.glob(f"{day_prefix}*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    latest_path = AUTONOMY_REPORTS_DIR / "autonomy.json"
    if latest_path.exists():
        report_paths.insert(0, latest_path)

    for path in report_paths:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        planning = report.get("planning") or {}
        for order in planning.get("created_orders") or []:
            if setup_matches(order, symbol, run_id, run_id_key="source_run_id"):
                return "The planner created an order, but it is not currently active in the paper journal."
        for rejection in planning.get("rejected_candidates") or []:
            if setup_matches(rejection, symbol, run_id):
                return str(rejection.get("reason") or "The setup did not pass the autonomous mandate.")

    return "The Committee did not select this setup during the autonomous planning cycle."


def setup_matches(record, symbol, run_id, run_id_key="run_id"):
    if str(record.get("symbol") or "").upper().strip() != symbol:
        return False
    record_run_id = str(record.get(run_id_key) or "").strip()
    return not run_id or not record_run_id or record_run_id == run_id


def load_morning_brief_entry_ideas():
    if not MORNING_BRIEF_JSON_PATH.exists():
        return []
    try:
        report = json.loads(MORNING_BRIEF_JSON_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    ideas = []
    seen = set()
    for bucket in ("approved_simulated_trades", "conditional_setups"):
        for idea in report.get(bucket, []) or []:
            symbol = str(idea.get("symbol", "")).upper().strip()
            if not symbol:
                continue
            run_id = str(idea.get("run_id") or f"{bucket}-{symbol}")
            key = (symbol, run_id)
            if key in seen:
                continue
            seen.add(key)
            current = dict(idea)
            current["symbol"] = symbol
            current["run_id"] = run_id
            ideas.append(current)
    return ideas


def planned_journal_keys():
    frame = load_trade_journal()
    run_ids = set()
    symbols = set()
    for _, row in frame.iterrows():
        if normalize_status(row.get("status")) not in {"planned", "open"}:
            continue
        symbol = str(row.get("symbol", "")).upper().strip()
        run_id = str(row.get("agent_run_id", "")).strip()
        if symbol:
            symbols.add(symbol)
        if run_id:
            run_ids.add(run_id)
    return {"symbols": symbols, "run_ids": run_ids}


def collect_news_for_symbols(symbols):
    reports = []
    for symbol in symbols:
        if symbol in ETF_SYMBOLS:
            reports.append({
                "symbol": symbol,
                "status": "skipped_etf_news",
                "items": [],
                "summary": {
                    "stance": "not_applicable",
                    "total_score": 0,
                    "top_headline": None,
                },
            })
            continue
        try:
            reports.append(collect_overnight_news(symbol, limit=8))
        except Exception as exc:
            reports.append({
                "symbol": symbol,
                "error": str(exc),
                "items": [],
                "summary": {"stance": "error", "total_score": 0, "top_headline": None},
            })
    return reports


def build_fill_alerts(fill_result):
    alerts = []
    for event in fill_result.get("events", []):
        if event.get("event_type") == "entry_fill":
            alerts.append(build_alert(
                kind="entry_trigger",
                severity="high",
                symbol=event.get("symbol"),
                title=f"{event.get('symbol')} planned entry eligible for automatic fill",
                message=(
                    f"Planned entry hit at {event.get('fill_price')} "
                    f"(latest {event.get('latest_price')}); the autonomous execution cycle must apply the fill."
                ),
                payload=event,
                dedupe_key=(
                    f"{date.today().isoformat()}|planned_entry_trigger|"
                    f"{event.get('symbol')}|{event.get('trade_id')}|{event.get('fill_price')}"
                ),
            ))
            continue
        severity = "high" if event.get("event_type") == "exit_fill" else "medium"
        alerts.append(build_alert(
            kind="paper_fill",
            severity=severity,
            symbol=event.get("symbol"),
            title=f"{event.get('symbol')} paper {event.get('event_type')}",
            message=(
                f"{event.get('old_status')} -> {event.get('new_status')} at {event.get('fill_price')} "
                f"(latest {event.get('latest_price')}); {event.get('reason')}."
            ),
            payload=event,
        ))
    return alerts


def build_position_alerts(position_report):
    alerts = []
    for action in position_report.get("daily_action_list", []):
        recommendation = action.get("recommendation")
        if recommendation not in ACTION_ALERTS:
            continue
        alerts.append(build_alert(
            kind="position_action",
            severity="high" if recommendation in {"EXIT", "TAKE PROFIT", "REVIEW EXIT"} else "medium",
            symbol=action.get("symbol"),
            title=f"{action.get('symbol')} {recommendation}",
            message=action.get("reason"),
            payload=action,
        ))
    return alerts


def build_market_alerts(market_report):
    snapshot = market_report.get("macro", {})
    assessment = market_report.get("assessment", {})
    alerts = []

    for key, label in [("sp500", "S&P 500"), ("nasdaq", "Nasdaq"), ("russell_2000", "Russell 2000")]:
        item = snapshot.get(key) or {}
        move = item.get("one_day_change_pct")
        if move is not None and abs(move) >= MARKET_MOVE_THRESHOLD:
            alerts.append(build_alert(
                kind="market_move",
                severity="high" if move <= -MARKET_MOVE_THRESHOLD else "medium",
                symbol="MARKET",
                title=f"{label} intraday market move",
                message=f"{label} is moving {move:.2f}% today; reassess position sizing and new entries.",
                payload=item,
            ))

    vix = snapshot.get("vix") or {}
    vix_move = vix.get("one_day_change_pct")
    if vix_move is not None and vix_move >= VIX_SPIKE_THRESHOLD:
        alerts.append(build_alert(
            kind="volatility_spike",
            severity="high",
            symbol="MARKET",
            title="VIX volatility spike",
            message=f"VIX is up {vix_move:.2f}% today; tighten new trade standards.",
            payload=vix,
        ))

    regime = assessment.get("market_regime")
    if regime in {"Risk-Off", "Risk-On"}:
        alerts.append(build_alert(
            kind="regime_watch",
            severity="medium",
            symbol="MARKET",
            title=f"Market regime: {regime}",
            message=f"Macro score is {assessment.get('macro_score')}/100; Committee should account for this backdrop.",
            payload=assessment,
        ))

    return alerts


def build_news_alerts(news_reports):
    alerts = []
    for report in news_reports:
        symbol = report.get("symbol")
        summary = report.get("summary") or {}
        stance = summary.get("stance")
        score = summary.get("total_score") or 0
        if stance not in {"positive_catalyst", "negative_catalyst"} and abs(score) < NEWS_SCORE_THRESHOLD:
            continue

        top_item = best_news_item(report.get("items", []))
        title = summary.get("top_headline") or (top_item or {}).get("title") or f"{symbol} news catalyst"
        severity = "high" if stance == "negative_catalyst" else "medium"
        alerts.append(build_alert(
            kind="news_catalyst",
            severity=severity,
            symbol=symbol,
            title=f"{symbol} news catalyst",
            message=f"{stance}: {title} (score {score}).",
            payload={
                "summary": summary,
                "top_item": top_item,
            },
        ))
    return alerts


def best_news_item(items):
    relevant = [item for item in items if item.get("symbol_relevant")]
    if not relevant:
        return None
    return max(relevant, key=lambda item: item.get("relevance_score", 0))


def build_alert(kind, severity, symbol, title, message, payload, dedupe_key=None):
    raw_id = dedupe_key or "|".join([
        date.today().isoformat(),
        str(kind),
        str(symbol or ""),
        str(title or ""),
        str(message or ""),
    ])
    return {
        "alert_id": hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16],
        "kind": kind,
        "severity": severity,
        "symbol": symbol,
        "title": title,
        "message": message,
        "created_at": datetime.now(EASTERN).isoformat(timespec="seconds"),
        "payload": payload,
    }


def dedupe_alerts(alerts):
    seen = set()
    deduped = []
    severity_order = {"high": 0, "medium": 1, "low": 2}
    for alert in sorted(alerts, key=lambda item: severity_order.get(item["severity"], 9)):
        if alert["alert_id"] in seen:
            continue
        seen.add(alert["alert_id"])
        deduped.append(alert)
    return deduped


def create_intraday_email_body(report):
    lines = [
        "AIFundOS Intraday Alert",
        f"Created At: {report['created_at']}",
        "Mode: Watch Only / Paper Trading",
        "",
        f"New Alerts: {report['new_alert_count']}",
        f"Symbols Checked: {', '.join(report['checked_symbols']) if report['checked_symbols'] else 'None'}",
        "",
        "Alerts",
    ]

    for alert in report.get("new_alerts", []):
        lines.append(
            f"- [{alert['severity'].upper()}] {alert['title']}: {alert['message']}"
        )

    lines.extend([
        "",
        "Position Manager Snapshot",
        format_position_manager_report(report["position_manager"]),
        "",
        "Paper Fill Check",
        format_paper_fill_report(report["paper_fill_check"]),
        "",
        "Entry Trigger Check",
        format_entry_trigger_check(report["entry_trigger_check"]),
        "",
        "Guardrails",
        "- This is a watch-only/paper-trading alert, not a live trade instruction.",
        "- Selected autonomous paper orders can be planned and filled without human approval.",
        "- Unselected setup triggers are logged for learning and do not become trades unless a later Committee cycle selects them.",
        "- No live brokerage or real-money authority exists.",
        "- Alerts are deduped so the same event should not email repeatedly today.",
    ])
    return "\n".join(lines) + "\n"


def build_subject(alerts):
    high_count = len([alert for alert in alerts if alert["severity"] == "high"])
    symbols = sorted({alert.get("symbol") for alert in alerts if alert.get("symbol")})
    symbol_text = ", ".join(symbols[:4])
    if len(symbols) > 4:
        symbol_text += f" +{len(symbols) - 4}"
    prefix = "URGENT" if high_count else "Watch"
    return f"AIFundOS Intraday {prefix}: {len(alerts)} alert(s)" + (f" - {symbol_text}" if symbol_text else "")


def format_intraday_monitor_report(report):
    lines = [
        "# Intraday Monitor Report",
        "",
        f"Created At: {report['created_at']}",
        f"Run ID: {report['run_id']}",
        f"Checked Symbols: {', '.join(report['checked_symbols']) if report['checked_symbols'] else 'None'}",
        f"Alerts: {report['alert_count']}",
        f"New Alerts: {report['new_alert_count']}",
        f"Email: {format_email_status(report)}",
        "",
        "## New Alerts",
    ]

    if not report.get("new_alerts"):
        lines.append("- None.")
    else:
        for alert in report["new_alerts"]:
            lines.append(f"- [{alert['severity'].upper()}] {alert['title']}: {alert['message']}")

    lines.extend(["", "## All Current Alerts"])
    if not report.get("alerts"):
        lines.append("- None.")
    else:
        for alert in report["alerts"]:
            lines.append(f"- [{alert['severity'].upper()}] {alert['kind']} {alert['symbol']}: {alert['message']}")

    lines.extend(["", "## Entry Trigger Check", format_entry_trigger_check(report.get("entry_trigger_check") or {})])

    return "\n".join(lines)


def format_entry_trigger_check(check):
    lines = [
        f"Source: {check.get('source', 'n/a')}",
        f"Status: {check.get('status', 'n/a')}",
        f"Symbols Checked: {len(check.get('checked_symbols', []))}",
        f"Entry Triggers: {len(check.get('triggered', []))}",
    ]
    triggered = check.get("triggered") or []
    if not triggered:
        lines.append("No entry thresholds were hit.")
        return "\n".join(lines)
    for event in triggered:
        lines.append(
            "- {symbol}: latest {latest_price}, threshold {entry_threshold}, stop {stop}, "
            "target {target_1}; not selected for autonomous execution: "
            "{autonomous_selection_reason}".format(**event)
        )
    return "\n".join(lines)


def format_email_status(report):
    result = report.get("email_result")
    if not result:
        return "not sent"
    if result.get("dry_run"):
        return "dry run"
    if result.get("queued"):
        return "queued for retry"
    if result.get("sent"):
        return f"sent to {result.get('to')}"
    return f"failed: {result.get('error') or 'unknown delivery error'}"


def save_intraday_monitor_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    markdown = format_intraday_monitor_report(report)
    latest_md = REPORTS_DIR / "intraday_monitor.md"
    latest_json = REPORTS_DIR / "intraday_monitor.json"
    stamped_md = REPORTS_DIR / f"intraday_monitor_{timestamp}.md"
    stamped_json = REPORTS_DIR / f"intraday_monitor_{timestamp}.json"
    latest_md.write_text(markdown, encoding="utf-8")
    latest_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    stamped_md.write_text(markdown, encoding="utf-8")
    stamped_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return latest_md


def load_alert_state():
    today = date.today().isoformat()
    if not ALERT_STATE_PATH.exists():
        return {"date": today, "sent_alert_ids": [], "queued_alert_ids": []}
    try:
        state = json.loads(ALERT_STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"date": today, "sent_alert_ids": [], "queued_alert_ids": []}
    if state.get("date") != today:
        return {"date": today, "sent_alert_ids": [], "queued_alert_ids": []}
    return {
        "date": today,
        "sent_alert_ids": list(state.get("sent_alert_ids", []))[-500:],
        "queued_alert_ids": list(state.get("queued_alert_ids", []))[-500:],
    }


def mark_alerts_sent(state, alerts):
    existing = list(state.get("sent_alert_ids", []))
    existing.extend(alert["alert_id"] for alert in alerts)
    state["date"] = date.today().isoformat()
    state["sent_alert_ids"] = sorted(set(existing))[-500:]
    queued = set(state.get("queued_alert_ids", []))
    queued.difference_update(alert["alert_id"] for alert in alerts)
    state["queued_alert_ids"] = sorted(queued)[-500:]


def mark_alerts_queued(state, alerts):
    existing = list(state.get("queued_alert_ids", []))
    existing.extend(alert["alert_id"] for alert in alerts)
    state["date"] = date.today().isoformat()
    state["queued_alert_ids"] = sorted(set(existing))[-500:]


def save_alert_state(state):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ALERT_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def save_entry_trigger_log(triggered):
    if not triggered:
        return
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    existing = []
    if ENTRY_TRIGGER_LOG_PATH.exists():
        try:
            existing = json.loads(ENTRY_TRIGGER_LOG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = []
    existing_ids = {
        f"{item.get('symbol')}|{item.get('run_id')}|{item.get('entry_threshold')}|{item.get('timestamp', '')[:10]}"
        for item in existing
    }
    for event in triggered:
        event_id = f"{event.get('symbol')}|{event.get('run_id')}|{event.get('entry_threshold')}|{event.get('timestamp', '')[:10]}"
        if event_id not in existing_ids:
            existing.append(event)
            existing_ids.add(event_id)
    ENTRY_TRIGGER_LOG_PATH.write_text(json.dumps(existing[-1000:], indent=2, default=str), encoding="utf-8")


def safe_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def round_number(value):
    return round(safe_float(value), 4)


def summarize_for_memory(report):
    return {
        "agent": report["agent"],
        "run_id": report["run_id"],
        "created_at": report["created_at"],
        "checked_symbols": report["checked_symbols"],
        "alert_count": report["alert_count"],
        "new_alert_count": report["new_alert_count"],
        "entry_policy": report.get("entry_policy"),
        "entry_triggers": report.get("entry_trigger_check", {}).get("triggered", []),
        "alerts": [
            {
                "kind": alert["kind"],
                "severity": alert["severity"],
                "symbol": alert["symbol"],
                "title": alert["title"],
                "message": redact_text(alert["message"] or ""),
            }
            for alert in report["new_alerts"]
        ],
        "email_sent": bool(report.get("email_result")) and not report["email_result"].get("dry_run"),
    }
