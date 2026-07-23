import json
import os
from datetime import datetime
from pathlib import Path

from agents.options_flow import analyze_options_flow
from data.data_quality import generate_data_health_report
from data.options_journal import load_options_journal, summarize_options_journal


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PROJECT_ROOT / "framework" / "options_readiness.json"
REPORTS_DIR = PROJECT_ROOT / "reports" / "options_readiness"


def generate_options_readiness_report(symbol=None, include_live_options=False):
    policy = load_options_policy()
    data_health = generate_data_health_report(live_checks=False)
    journal = load_options_journal()
    summary = summarize_options_journal(journal)
    provider_status = build_provider_status()
    starter_snapshot = None
    if symbol and include_live_options:
        starter_snapshot = analyze_options_flow(symbol)

    checks = build_readiness_checks(policy, data_health, provider_status, summary, starter_snapshot)
    score = calculate_score(checks)
    status = classify_status(score, checks)

    return {
        "agent": "Options Readiness",
        "system_role": "governance_layer",
        "layer": "Options Readiness Framework",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "symbol": symbol.upper().strip() if symbol else "PORTFOLIO",
        "status": status,
        "readiness_score": score,
        "provider_status": provider_status,
        "checks": checks,
        "strategy_menu": policy["allowed_beginner_strategies"],
        "blocked_strategies": policy["blocked_strategies"],
        "paper_options_summary": summary,
        "provider_test_plan": policy["provider_test_plan"],
        "education_terms": policy["education_terms"],
        "starter_options_snapshot": starter_snapshot,
        "next_steps": build_next_steps(checks, provider_status),
    }


def load_options_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def build_provider_status():
    providers = {
        "intrinio": {
            "configured": bool(os.getenv("INTRINIO_API_KEY", "").strip()),
            "env_key": "INTRINIO_API_KEY",
            "role": "preferred future options chain, IV, greeks, and historical options provider",
        },
        "tradier": {
            "configured": bool(os.getenv("TRADIER_ACCESS_TOKEN", "").strip()),
            "env_key": "TRADIER_ACCESS_TOKEN",
            "role": "future broker/options chain workflow candidate",
        },
        "orats": {
            "configured": bool(os.getenv("ORATS_TOKEN", "").strip()),
            "env_key": "ORATS_TOKEN",
            "role": "future advanced volatility and options analytics provider",
        },
        "starter_yahoo": {
            "configured": True,
            "env_key": None,
            "role": "current watch-only options-chain context",
        },
    }
    return providers


def build_readiness_checks(policy, data_health, provider_status, summary, starter_snapshot):
    gate = data_health.get("gate") or {}
    has_paid_provider = any(
        provider_status[name]["configured"]
        for name in ("intrinio", "tradier", "orats")
    )
    checks = [
        {
            "name": "Policy framework",
            "status": "pass",
            "detail": "Beginner strategy menu, blocked strategies, checklist, and provider test plan are defined.",
        },
        {
            "name": "Data quality gate",
            "status": "pass" if gate.get("status") in {"Pass", "Conditional"} else "warn",
            "detail": f"Current data gate is {gate.get('status', 'unknown')}: {gate.get('decision', 'n/a')}",
        },
        {
            "name": "Paid options provider",
            "status": "pass" if has_paid_provider else "warn",
            "detail": "Paid options provider configured." if has_paid_provider else "No Intrinio, Tradier, or ORATS key configured yet.",
        },
        {
            "name": "Paper-options journal",
            "status": "pass",
            "detail": (
                f"{summary['planned_or_open']} planned/open ideas, "
                f"{summary['closed']} closed ideas, "
                f"${summary['open_premium_at_risk']:.2f} open premium at risk."
            ),
        },
        {
            "name": "Starter options snapshot",
            "status": classify_starter_snapshot(starter_snapshot),
            "detail": build_starter_snapshot_detail(starter_snapshot),
        },
        {
            "name": "Risk limits",
            "status": "pass",
            "detail": (
                f"Max account risk per options idea: "
                f"{policy['checklist']['risk']['maximum_account_risk_pct']}%; "
                f"max options sleeve: {policy['checklist']['risk']['maximum_options_sleeve_pct']}%."
            ),
        },
    ]
    return checks


def classify_starter_snapshot(snapshot):
    if snapshot is None:
        return "not_checked"
    if snapshot.get("error"):
        return "warn"
    if snapshot.get("liquidity_quality") in {"good", "usable"}:
        return "pass"
    return "warn"


def build_starter_snapshot_detail(snapshot):
    if snapshot is None:
        return "Run with a symbol and live snapshot enabled to check starter Yahoo options context."
    if snapshot.get("error"):
        return snapshot.get("error")
    return (
        f"Starter snapshot from {snapshot.get('provider')}; "
        f"liquidity {snapshot.get('liquidity_quality')}, "
        f"stance {snapshot.get('stance')}, "
        f"confidence {snapshot.get('confidence')}."
    )


def calculate_score(checks):
    weights = {
        "pass": 1.0,
        "warn": 0.55,
        "not_checked": 0.35,
        "fail": 0.0,
    }
    if not checks:
        return 0
    return round(sum(weights.get(item["status"], 0) for item in checks) / len(checks) * 100, 1)


def classify_status(score, checks):
    if any(item["status"] == "fail" for item in checks):
        return "Blocked"
    provider_check = next((item for item in checks if item["name"] == "Paid options provider"), {})
    if provider_check.get("status") == "warn" and score >= 85:
        return "Framework Ready / Provider Pending"
    if score >= 85:
        return "Framework Ready"
    if score >= 70:
        return "Practice Ready"
    return "Needs Work"


def build_next_steps(checks, provider_status):
    steps = []
    if not provider_status["intrinio"]["configured"]:
        steps.append("Wait to start the Intrinio trial until we are ready to test the provider plan immediately.")
    steps.append("Use the paper-options journal to practice only defined-risk strategies.")
    steps.append("Keep options ideas watch-only until liquidity, IV, Greeks, and historical prices are available from a paid provider.")
    steps.append("When Intrinio is activated, run the provider test plan before changing any Committee decision rules.")
    return steps


def format_options_readiness_report(report):
    lines = [
        "# Options Readiness Report",
        "",
        f"Created At: {report['created_at']}",
        f"Scope: {report['symbol']}",
        f"Status: {report['status']}",
        f"Readiness Score: {report['readiness_score']}/100",
        "",
        "## Provider Status",
    ]
    for name, provider in report["provider_status"].items():
        status = "configured" if provider["configured"] else "not configured"
        lines.append(f"- {name}: {status} ({provider['role']})")

    lines.extend(["", "## Readiness Checks"])
    for item in report["checks"]:
        lines.append(f"- {item['name']}: {item['status']} - {item['detail']}")

    summary = report["paper_options_summary"]
    lines.extend([
        "",
        "## Paper Options Journal",
        f"- Planned/Open: {summary['planned_or_open']}",
        f"- Closed: {summary['closed']}",
        f"- Open Premium At Risk: ${summary['open_premium_at_risk']:.2f}",
        f"- Open Unrealized P&L: ${summary['open_unrealized_pnl']:.2f}",
        f"- Total Realized P&L: ${summary['total_realized_pnl']:.2f}",
        "",
        "## Beginner Strategy Menu",
    ])
    for item in report["strategy_menu"]:
        lines.append(f"- {item['strategy']}: {item['direction']}, max loss = {item['max_loss']}")

    lines.extend(["", "## Provider Trial Plan"])
    lines.extend([f"- {item}" for item in report["provider_test_plan"]])

    lines.extend(["", "## Education Terms"])
    for term, definition in report["education_terms"].items():
        lines.append(f"- {term}: {definition}")

    lines.extend(["", "## Next Steps"])
    lines.extend([f"- {item}" for item in report["next_steps"]])
    return "\n".join(lines) + "\n"


def save_options_readiness_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "options_readiness_report.md"
    path.write_text(format_options_readiness_report(report), encoding="utf-8")
    return path
