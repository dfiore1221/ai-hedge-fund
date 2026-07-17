import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"

REQUIRED_LOCAL_IGNORES = [
    ".env",
    "*.db",
    "portfolio/holdings.json",
    "portfolio/trade_journal.csv",
    "reports/morning_brief/",
    "reports/feedback/",
]

ENV_CHECKS = [
    ("OPENAI_API_KEY", "OpenAI API access", "required_for_research"),
    ("SEC_USER_AGENT", "SEC EDGAR user agent", "required_for_sec"),
    ("DASHBOARD_PASSCODE", "Dashboard passcode", "recommended"),
    ("SMTP_HOST", "Email SMTP host", "required_for_email"),
    ("SMTP_USERNAME", "Email SMTP username", "required_for_email"),
    ("SMTP_PASSWORD", "Email SMTP app password", "required_for_email"),
    ("EMAIL_FROM", "Email sender", "required_for_email"),
    ("MORNING_BRIEF_EMAIL_TO", "Morning brief recipient", "required_for_email"),
    ("APPROVED_EMAIL_RECIPIENTS", "Approved email recipient allowlist", "recommended"),
]

PLACEHOLDER_TOKENS = {"", "your_email@example.com", "your_openai_api_key_here", "your_email_app_password_here"}


def build_security_report():
    load_dotenv(ENV_PATH)
    env_status = [check_env_var(key, label, severity) for key, label, severity in ENV_CHECKS]
    ignore_status = check_gitignore()
    email_status = check_email_allowlist()

    blockers = [
        item for item in env_status
        if item["severity"].startswith("required") and not item["present"]
    ]
    warnings = [
        item for item in env_status
        if item["severity"] == "recommended" and not item["present"]
    ]
    warnings.extend(email_status["warnings"])
    warnings.extend(ignore_status["warnings"])

    return {
        "env_path_present": ENV_PATH.exists(),
        "env_status": env_status,
        "gitignore": ignore_status,
        "email": email_status,
        "passed": not blockers,
        "blockers": blockers,
        "warnings": warnings,
    }


def check_env_var(key, label, severity):
    value = os.getenv(key, "")
    present = bool(value and value.strip() and value.strip() not in PLACEHOLDER_TOKENS)
    return {
        "key": key,
        "label": label,
        "severity": severity,
        "present": present,
        "display": "set" if present else "missing",
    }


def check_gitignore():
    text = GITIGNORE_PATH.read_text(encoding="utf-8") if GITIGNORE_PATH.exists() else ""
    missing = [entry for entry in REQUIRED_LOCAL_IGNORES if entry not in text]
    return {
        "path": str(GITIGNORE_PATH),
        "passed": not missing,
        "missing": missing,
        "warnings": [
            f"Missing local-only ignore rule: {entry}"
            for entry in missing
        ],
    }


def check_email_allowlist():
    recipients = parse_csv_env("MORNING_BRIEF_EMAIL_TO")
    approved = parse_csv_env("APPROVED_EMAIL_RECIPIENTS")
    warnings = []

    if recipients and not approved:
        warnings.append("APPROVED_EMAIL_RECIPIENTS is not set; email sends are not allowlist-checked.")
    elif recipients and approved:
        unapproved = sorted(set(recipients) - set(approved))
        warnings.extend([f"Morning brief recipient is not in allowlist: {item}" for item in unapproved])

    return {
        "recipients_configured": bool(recipients),
        "allowlist_configured": bool(approved),
        "recipient_count": len(recipients),
        "approved_count": len(approved),
        "warnings": warnings,
    }


def parse_csv_env(key):
    value = os.getenv(key, "")
    return [
        item.strip().lower()
        for item in value.split(",")
        if item.strip()
    ]


def format_security_report(report):
    lines = [
        "# Security Check",
        "",
        f".env present: {report['env_path_present']}",
        f"Passed required checks: {report['passed']}",
        "",
        "## Environment",
    ]

    for item in report["env_status"]:
        lines.append(f"- {item['key']}: {item['display']} ({item['severity']})")

    lines.extend([
        "",
        "## Git Ignore",
        f"- Passed: {report['gitignore']['passed']}",
    ])
    if report["gitignore"]["missing"]:
        lines.extend([f"- Missing: {item}" for item in report["gitignore"]["missing"]])
    else:
        lines.append("- Local-only files are ignored.")

    lines.extend([
        "",
        "## Email Safety",
        f"- Recipients configured: {report['email']['recipients_configured']}",
        f"- Recipient count: {report['email']['recipient_count']}",
        f"- Allowlist configured: {report['email']['allowlist_configured']}",
        f"- Approved recipient count: {report['email']['approved_count']}",
        "",
        "## Blockers",
    ])
    lines.extend([f"- {item['key']}: {item['label']}" for item in report["blockers"]] or ["- None."])

    lines.extend(["", "## Warnings"])
    lines.extend([f"- {item}" if isinstance(item, str) else f"- {item['key']}: {item['label']}" for item in report["warnings"]] or ["- None."])

    return "\n".join(lines) + "\n"


def redact_text(text):
    if not text:
        return text

    load_dotenv(ENV_PATH)
    redacted = str(text)
    sensitive_keys = [
        "OPENAI_API_KEY",
        "SMTP_PASSWORD",
        "SMTP_USERNAME",
        "EMAIL_FROM",
        "MORNING_BRIEF_EMAIL_TO",
    ]
    for key in sensitive_keys:
        value = os.getenv(key, "")
        if value and len(value) >= 4:
            redacted = redacted.replace(value, f"<redacted:{key}>")
    return redacted
