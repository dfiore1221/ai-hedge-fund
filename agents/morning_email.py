from datetime import datetime
from pathlib import Path

from agents.morning_brief import create_morning_brief, format_morning_brief, save_morning_brief
from delivery.email_delivery import load_email_config, send_email
from delivery.email_retry import queue_email


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "reports" / "morning_brief"


def create_email_body(report):
    assessment = report["macro"]["assessment"]
    approved = report["approved_simulated_trades"]
    conditional = report["conditional_setups"]
    watch = report["worth_watching"]
    rejected = report["rejected_or_avoid"]
    core_sleeve = report.get("core_etf_sleeve") or {}

    lines = [
        "AI Hedge Fund Morning Brief",
        f"Created At: {report['created_at']}",
        "Mode: Watch Only / No Live Trading",
        "",
        f"Market Regime: {assessment['market_regime']} ({assessment['macro_score']}/100)",
        f"Symbols Scanned: {len(report['symbols_scanned'])}",
        f"Approved Simulated Trades: {len(approved)}",
        f"Conditional Setups: {len(conditional)}",
        f"Watchlist Setups: {len(watch)}",
        "",
        "Core ETF Sleeve",
        f"Status: {core_sleeve.get('status', 'n/a')}",
        f"Target Sleeve: {format_pct(core_sleeve.get('target_sleeve_pct'))} / {format_money(core_sleeve.get('target_sleeve_value', 0))}",
        f"Current Sleeve: {format_pct(core_sleeve.get('current_sleeve_pct'))} / {format_money(core_sleeve.get('current_sleeve_value', 0))}",
        "Desired Allocation: " + format_core_allocations(core_sleeve.get("desired_allocations", [])),
        "",
        "Approved Simulated Trades",
    ]

    lines.extend(format_email_ideas(approved, empty_text="None today."))
    lines.extend(["", "Conditional Setups"])
    lines.extend(format_email_ideas(conditional, empty_text="None today."))
    lines.extend(["", "Watchlist Setups"])
    lines.extend(format_email_ideas(watch, empty_text="None today."))
    lines.extend(["", "Top Rejected / Avoid Today"])
    lines.extend(format_email_ideas(rejected[:5], empty_text="None surfaced."))
    lines.extend([
        "",
        "Guardrails",
        "- This is a watch-only research brief, not a live trade instruction.",
        "- Any paper trade still requires human review before action.",
        "- Full markdown report is attached.",
    ])

    return "\n".join(lines) + "\n"


def format_email_ideas(ideas, empty_text):
    if not ideas:
        return [f"- {empty_text}"]

    lines = []
    for idea in ideas:
        label = idea["display_symbol"]
        if idea["display_symbol"] != idea["symbol"]:
            label = f"{idea['display_symbol']} ({idea['symbol']})"
        lines.append(
            f"- {label}: {idea['decision']} | {idea['category']} | "
            f"score {format_number(idea['score'])} | R/R {format_number(idea['reward_to_risk'])}"
        )
        if idea.get("run_id"):
            lines.append(f"  Run ID: {idea['run_id']}")
        lines.append(f"  Why: {idea['reason'] or 'No clear positive setup.'}")

    return lines


def send_morning_brief_email(dry_run=False):
    report = create_morning_brief()
    output_path = save_morning_brief(report)
    full_report = format_morning_brief(report)
    body = create_email_body(report)
    subject = build_subject(report)

    if dry_run:
        load_email_config()
        return {
            "dry_run": True,
            "subject": subject,
            "body": body,
            "report_path": output_path,
        }

    try:
        result = send_email(subject, body, attachment_path=output_path)
    except Exception as exc:
        pending_path = queue_email(
            subject,
            body,
            attachment_path=output_path,
            kind="morning_brief",
            error=exc,
            expiry_hours=8,
        )
        write_failure_log(exc, output_path, pending_path)
        return {
            "dry_run": False,
            "sent": False,
            "queued": True,
            "subject": subject,
            "body": body,
            "report_path": output_path,
            "pending_path": pending_path,
            "error": str(exc),
            "full_report": full_report,
        }

    write_delivery_log(result, output_path)
    return {
        "dry_run": False,
        "sent": True,
        "queued": False,
        "subject": subject,
        "body": body,
        "report_path": output_path,
        "delivery": result,
        "full_report": full_report,
    }


def build_subject(report):
    assessment = report["macro"]["assessment"]
    date = datetime.now().strftime("%Y-%m-%d")
    approved_count = len(report["approved_simulated_trades"])
    conditional_count = len(report["conditional_setups"])
    return (
        f"AI Hedge Fund Morning Brief - {date} - "
        f"{assessment['market_regime']} - {approved_count} simulated / {conditional_count} conditional"
    )


def write_delivery_log(result, report_path):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds")
    log_path = LOG_DIR / "email_delivery.log"
    with log_path.open("a", encoding="utf-8") as file:
        file.write(
            f"{timestamp} sent to {result['to']} subject={result['subject']} "
            f"attachment={report_path}\n"
        )


def write_failure_log(error, report_path, pending_path):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds")
    log_path = LOG_DIR / "email_delivery.log"
    with log_path.open("a", encoding="utf-8") as file:
        file.write(
            f"{timestamp} queued pending email after send failure error={error} "
            f"attachment={report_path} pending={pending_path}\n"
        )


def format_number(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def format_money(value):
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def format_pct(value):
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def format_core_allocations(allocations):
    if not allocations:
        return "n/a"
    return ", ".join(
        f"{item['symbol']} {format_pct(item['target_weight'])}"
        f" (~{item.get('suggested_shares', 0)} sh)"
        for item in allocations
    )
