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
from data.paper_fills import format_paper_fill_report, process_paper_fills
from data.trade_journal import load_trade_journal, normalize_status
from delivery.email_delivery import load_email_config, send_email
from memory.research_memory import save_agent_report
from security.checks import redact_text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "intraday_monitor"
ALERT_STATE_PATH = REPORTS_DIR / "alert_state.json"
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

    fill_result = process_paper_fills(apply=apply_fills)
    position_report = generate_position_manager_report(use_llm=False, save_memory=False)
    market_report = generate_daily_market_intelligence()
    symbols = active_symbols()
    news_reports = collect_news_for_symbols(symbols)

    alerts = []
    alerts.extend(build_fill_alerts(fill_result))
    alerts.extend(build_position_alerts(position_report))
    alerts.extend(build_market_alerts(market_report))
    alerts.extend(build_news_alerts(news_reports))
    alerts = dedupe_alerts(alerts)

    state = load_alert_state()
    new_alerts = [alert for alert in alerts if alert["alert_id"] not in state["sent_alert_ids"]]
    email_result = None

    report = {
        "agent": "Intraday Monitor",
        "run_id": run_id,
        "created_at": created_at,
        "send_alert": send_alert,
        "dry_run": dry_run,
        "apply_fills": apply_fills,
        "checked_symbols": symbols,
        "alert_count": len(alerts),
        "new_alert_count": len(new_alerts),
        "alerts": alerts,
        "new_alerts": new_alerts,
        "position_manager": position_report,
        "paper_fill_check": {
            "applied": fill_result.get("applied"),
            "events": fill_result.get("events", []),
            "checked_symbols": fill_result.get("checked_symbols", []),
        },
        "market": market_report,
        "news": news_reports,
        "email_result": None,
    }

    output_path = save_intraday_monitor_report(report)
    report["report_path"] = str(output_path)

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
            email_result = send_email(subject, body, attachment_path=output_path)
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


def build_alert(kind, severity, symbol, title, message, payload):
    raw_id = "|".join([
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
        "Guardrails",
        "- This is a watch-only/paper-trading alert, not a live trade instruction.",
        "- Review the dashboard before acting.",
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

    return "\n".join(lines)


def format_email_status(report):
    result = report.get("email_result")
    if not result:
        return "not sent"
    if result.get("dry_run"):
        return "dry run"
    return f"sent to {result.get('to')}"


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
        return {"date": today, "sent_alert_ids": []}
    try:
        state = json.loads(ALERT_STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"date": today, "sent_alert_ids": []}
    if state.get("date") != today:
        return {"date": today, "sent_alert_ids": []}
    return {
        "date": today,
        "sent_alert_ids": list(state.get("sent_alert_ids", []))[-500:],
    }


def mark_alerts_sent(state, alerts):
    existing = list(state.get("sent_alert_ids", []))
    existing.extend(alert["alert_id"] for alert in alerts)
    state["date"] = date.today().isoformat()
    state["sent_alert_ids"] = sorted(set(existing))[-500:]


def save_alert_state(state):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ALERT_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def summarize_for_memory(report):
    return {
        "agent": report["agent"],
        "run_id": report["run_id"],
        "created_at": report["created_at"],
        "checked_symbols": report["checked_symbols"],
        "alert_count": report["alert_count"],
        "new_alert_count": report["new_alert_count"],
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
