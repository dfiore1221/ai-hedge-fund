import json
from datetime import datetime, timedelta
from pathlib import Path

from agents.feedback_loop import generate_feedback_report
from delivery.email_delivery import send_email
from delivery.email_retry import queue_email
from memory.research_memory import save_agent_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PROJECT_ROOT / "framework" / "human_escalation_policy.json"
MORNING_BRIEF_PATH = PROJECT_ROOT / "reports" / "morning_brief" / "daily_morning_brief.json"
WATCHDOG_DIR = PROJECT_ROOT / "reports" / "automation_watchdog"
AUTONOMY_DIR = PROJECT_ROOT / "reports" / "autonomy"
REPORTS_DIR = PROJECT_ROOT / "reports" / "human_escalation"
STATE_PATH = REPORTS_DIR / "notification_state.json"


def run_human_escalation_check(morning_brief=None, notify=False, save_memory=True):
    policy = load_policy()
    brief = morning_brief if morning_brief is not None else load_json(MORNING_BRIEF_PATH)
    feedback = generate_feedback_report()
    report = evaluate_human_escalations(brief, feedback, policy)
    if notify:
        report["notification"] = notify_new_escalations(report, policy)
    output_path = save_human_escalation_report(report)
    report["report_path"] = str(output_path)
    should_save_memory = save_memory and (
        not notify or int((report.get("notification") or {}).get("new_event_count") or 0) > 0
    )
    if should_save_memory:
        save_agent_report(
            run_id=report["run_id"],
            agent_name="Human Escalation Monitor",
            output=report,
            symbol="PORTFOLIO",
            stance=report["status"],
            confidence=95,
        )
    return report


def evaluate_human_escalations(brief, feedback, policy):
    now = datetime.now()
    events = []
    feedback = feedback or generate_feedback_report()
    if not policy.get("enabled", True):
        return base_report(now, "disabled", events, policy)

    setup = feedback.get("setup_review_learning") or {}
    source_policy = policy.get("source_code_review") or {}
    reviewed = int(setup.get("reviewed_setups") or 0)
    learning_score = float(setup.get("learning_score") or 0)
    automation_failures = count_non_email_automation_failures(
        int(source_policy.get("recent_watchdog_reports", 10))
    )
    if (
        reviewed >= int(source_policy.get("minimum_reviewed_setups", 50))
        and learning_score < float(source_policy.get("learning_score_below", 60))
    ) or automation_failures >= int(source_policy.get("minimum_non_email_automation_failures", 3)):
        events.append(build_event(
            "source_code_review",
            "Source-code or strategy-logic review may be beneficial",
            "high",
            [
                f"Setup reviews: {reviewed}",
                f"Committee learning score: {learning_score:.1f}/100",
                f"Recent non-email automation failures: {automation_failures}",
            ],
            "Ask the human operator to review repeated failure patterns and approve a scoped code or strategy change with tests.",
            "Do not modify production source or strategy rules autonomously.",
        ))

    data_event = build_data_subscription_event(brief, policy)
    if data_event:
        events.append(data_event)

    expectancy = feedback.get("trade_expectancy") or {}
    active_return = primary_active_return(brief)
    gate_rejections = count_risk_gate_rejections()
    risk_policy = policy.get("risk_control_review") or {}
    if active_return is not None and all([
        int(expectancy.get("count") or 0) >= int(risk_policy.get("minimum_closed_trades", 30)),
        float(expectancy.get("avg_r") or 0) >= float(risk_policy.get("minimum_average_r", 0.25)),
        float(expectancy.get("win_rate") or 0) >= float(risk_policy.get("minimum_win_rate", 50)),
        gate_rejections >= int(risk_policy.get("minimum_risk_gate_rejections", 10)),
        active_return >= float(risk_policy.get("minimum_active_return_pct", 0)),
    ]):
        events.append(build_event(
            "risk_control_review",
            "A risk-control counterfactual review may be beneficial",
            "high",
            [
                f"Closed trades: {int(expectancy.get('count') or 0)}",
                f"Average R: {float(expectancy.get('avg_r') or 0):.2f}",
                f"Win rate: {float(expectancy.get('win_rate') or 0):.1f}%",
                f"Active return: {active_return:.2f}%",
                f"Risk-gate rejections reviewed: {gate_rejections}",
            ],
            "Run a paper-only counterfactual comparing current controls with a proposed change, then request explicit human approval.",
            "Do not remove or weaken any hard risk control autonomously.",
        ))

    real_policy = policy.get("real_money_readiness_review") or {}
    data_score = float((brief.get("data_health") or {}).get("data_quality_score") or 0)
    if active_return is not None and all([
        int(expectancy.get("count") or 0) >= int(real_policy.get("minimum_closed_trades", 50)),
        float(expectancy.get("avg_r") or 0) >= float(real_policy.get("minimum_average_r", 0.2)),
        float(expectancy.get("win_rate") or 0) >= float(real_policy.get("minimum_win_rate", 50)),
        float(expectancy.get("total_pnl") or 0) >= float(real_policy.get("minimum_total_pnl", 1)),
        data_score >= float(real_policy.get("minimum_data_quality_score", 95)),
        active_return >= float(real_policy.get("minimum_active_return_pct", 0)),
    ]):
        events.append(build_event(
            "real_money_readiness_review",
            "Paper results may justify a real-money readiness review",
            "critical",
            [
                f"Closed trades: {int(expectancy.get('count') or 0)}",
                f"Average R: {float(expectancy.get('avg_r') or 0):.2f}",
                f"Win rate: {float(expectancy.get('win_rate') or 0):.1f}%",
                f"Paper P&L: ${float(expectancy.get('total_pnl') or 0):,.2f}",
                f"Data quality: {data_score:.0f}/100",
                f"Active return: {active_return:.2f}%",
            ],
            "Notify the human operator to begin a separate legal, operational, security, broker, tax, and suitability review.",
            "Do not connect a broker, transfer money, or place a live order autonomously.",
        ))

    return base_report(now, "attention_requested" if events else "no_escalation", events, policy)


def build_data_subscription_event(brief, policy):
    data_policy = policy.get("data_subscription_review") or {}
    domains = (brief.get("data_health") or {}).get("domain_scores") or {}
    weak_domains = []
    minimum_ratio = float(data_policy.get("minimum_domain_coverage_ratio", 0.8))
    for name, row in domains.items():
        maximum = float(row.get("max_score") or 0)
        ratio = float(row.get("score") or 0) / maximum if maximum else 0
        if ratio < minimum_ratio:
            weak_domains.append(f"{name}: {float(row.get('score') or 0):g}/{maximum:g} ({row.get('status')})")

    keywords = [str(item).lower() for item in data_policy.get("keywords", [])]
    relevant_gaps = []
    for item in brief.get("missing_information", []) or []:
        lowered = str(item).lower()
        if any(keyword in lowered for keyword in keywords):
            relevant_gaps.append(str(item))

    if not weak_domains and not relevant_gaps:
        return None
    return build_event(
        "data_subscription_review",
        "A data subscription or provider trial may be beneficial",
        "medium",
        [*weak_domains[:5], *relevant_gaps[:5]],
        "Ask the human operator to compare cost, coverage, licensing, and measured decision impact before starting a trial or subscription.",
        "Do not purchase a plan, accept commercial terms, or expose payment credentials autonomously.",
    )


def notify_new_escalations(report, policy):
    state = load_state()
    cooldown = timedelta(days=int(policy.get("notification_cooldown_days", 30)))
    now = datetime.now()
    new_events = []
    for event in report.get("events", []):
        prior = parse_datetime((state.get("notified") or {}).get(event["event_id"]))
        if prior is None or now - prior >= cooldown:
            new_events.append(event)
    if not new_events:
        return {"status": "deduplicated", "new_event_count": 0}

    subject = f"AIFundOS human review requested: {len(new_events)} item(s)"
    body = format_escalation_email(new_events, policy)
    try:
        delivery = send_email(subject, body)
        result = {"status": "sent", "new_event_count": len(new_events), "delivery": delivery}
    except Exception as exc:
        pending_path = queue_email(
            subject,
            body,
            kind="human_escalation",
            error=exc,
            expiry_hours=168,
        )
        result = {
            "status": "queued",
            "new_event_count": len(new_events),
            "pending_path": str(pending_path),
            "error": str(exc),
        }

    notified = state.setdefault("notified", {})
    for event in new_events:
        notified[event["event_id"]] = now.isoformat(timespec="seconds")
    save_state(state)
    return result


def build_event(event_id, title, severity, evidence, recommended_action, prohibited_action):
    return {
        "event_id": event_id,
        "title": title,
        "severity": severity,
        "evidence": evidence,
        "recommended_action": recommended_action,
        "prohibited_action": prohibited_action,
    }


def base_report(now, status, events, policy):
    return {
        "agent": "Human Escalation Monitor",
        "system_role": "governance_layer",
        "created_at": now.isoformat(timespec="seconds"),
        "run_id": f"{now.date().isoformat()}-human-escalation",
        "status": status,
        "event_count": len(events),
        "events": events,
        "immutable_rules": policy.get("immutable_rules", []),
        "notification": None,
    }


def count_non_email_automation_failures(limit):
    failures = 0
    for payload in recent_json_reports(WATCHDOG_DIR, "automation_watchdog_*.json", limit):
        for action in payload.get("actions", []):
            if action.get("label") in {"morning-email", "email-retry", "intraday-monitor"}:
                continue
            if action.get("returncode") not in {0, None} or action.get("operational_status") == "needs_attention":
                failures += 1
    return failures


def count_risk_gate_rejections(limit=20):
    count = 0
    markers = ("risk manager", "reward/risk", "not authorized", "risk budget")
    for payload in recent_json_reports(AUTONOMY_DIR, "autonomy_*.json", limit):
        for item in (payload.get("planning") or {}).get("rejected_candidates", []) or []:
            if any(marker in str(item.get("reason") or "").lower() for marker in markers):
                count += 1
    return count


def primary_active_return(brief):
    primary = ((brief.get("benchmark_attribution") or {}).get("since_inception") or {}).get("primary") or {}
    value = primary.get("active_return_pct")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def recent_json_reports(directory, pattern, limit):
    if not directory.exists():
        return []
    paths = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]
    return [payload for payload in (load_json(path) for path in paths) if payload]


def load_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_state():
    state = load_json(STATE_PATH)
    return state if isinstance(state, dict) else {"notified": {}}


def save_state(state):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def save_human_escalation_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    latest_json = REPORTS_DIR / "human_escalation.json"
    latest_md = REPORTS_DIR / "human_escalation.md"
    archive_json = REPORTS_DIR / f"human_escalation_{stamp}.json"
    archive_md = REPORTS_DIR / f"human_escalation_{stamp}.md"
    payload = json.dumps(report, indent=2, default=str)
    markdown = format_human_escalation_report(report)
    for path in (latest_json, archive_json):
        path.write_text(payload, encoding="utf-8")
    for path in (latest_md, archive_md):
        path.write_text(markdown, encoding="utf-8")
    return latest_md


def format_human_escalation_report(report):
    lines = [
        "# AIFundOS Human Escalation Monitor",
        "",
        f"Created At: {report.get('created_at')}",
        f"Status: {report.get('status')}",
        f"Review Requests: {report.get('event_count', 0)}",
    ]
    for event in report.get("events", []):
        lines.extend([
            "",
            f"## {event.get('title')}",
            f"- Severity: {event.get('severity')}",
            f"- Recommended next step: {event.get('recommended_action')}",
            f"- Boundary: {event.get('prohibited_action')}",
            "- Evidence:",
        ])
        lines.extend([f"  - {item}" for item in event.get("evidence", [])])
    notification = report.get("notification") or {}
    if notification:
        lines.extend(["", "## Notification", f"- Status: {notification.get('status')}"])
    lines.extend(["", "## Immutable Rules"])
    lines.extend([f"- {item}" for item in report.get("immutable_rules", [])])
    return "\n".join(lines) + "\n"


def format_escalation_email(events, policy):
    lines = [
        "AIFundOS identified changes that may benefit from your review.",
        "No restricted action was taken.",
    ]
    for event in events:
        lines.extend([
            "",
            event["title"],
            f"Severity: {event['severity']}",
            f"Recommended next step: {event['recommended_action']}",
            f"Boundary: {event['prohibited_action']}",
            "Evidence:",
        ])
        lines.extend([f"- {item}" for item in event.get("evidence", [])])
    lines.extend(["", "Standing boundaries:"])
    lines.extend([f"- {item}" for item in policy.get("immutable_rules", [])])
    return "\n".join(lines) + "\n"
