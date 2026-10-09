import json
import subprocess
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "automation_watchdog"
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


def run_automation_watchdog(now=None):
    now = now or datetime.now()
    report = {
        "agent": "Automation Watchdog",
        "system_role": "operations_layer",
        "created_at": now.isoformat(timespec="seconds"),
        "date": now.date().isoformat(),
        "weekday": now.isoweekday(),
        "actions": [],
        "status": "ok",
        "notes": [],
    }

    if not PYTHON.exists():
        report["status"] = "blocked"
        report["notes"].append(f"Python environment missing at {PYTHON}.")
        return report

    if now.isoweekday() > 5:
        report["notes"].append("Weekend detected; watchdog did not run market-day tasks.")
        return report

    hhmm = int(now.strftime("%H%M"))

    if 445 <= hhmm <= 1130 and not has_today_file("reports/morning_brief", f"morning_brief_{now:%Y%m%d}_*.json"):
        report["actions"].append(run_command("morning-email", ["morning-email", "today"], timeout=1800))

    if (
        445 <= hhmm <= 1130
        and has_today_file("reports/morning_brief", f"morning_brief_{now:%Y%m%d}_*.json")
        and not has_today_autonomy_action(now, "plan")
    ):
        report["actions"].append(run_command("autonomy-plan", ["autonomy", "plan"], timeout=300))

    if 450 <= hhmm <= 1200:
        report["actions"].append(run_command("email-retry", ["email-retry", "all"], timeout=300))

    if 930 <= hhmm <= 1605:
        report["actions"].append(run_command("intraday-discovery", ["intraday-discovery", "now"], timeout=600))
        report["actions"].append(run_command("autonomy-plan-intraday", ["autonomy", "plan"], timeout=300))
        report["actions"].append(run_command("autonomy-execute", ["autonomy", "execute"], timeout=300))
        report["actions"].append(run_command("intraday-monitor", ["intraday-monitor", "now"], timeout=300))
        report["actions"].append(run_command("trade-funnel", ["funnel", "status"], timeout=60))

    if hhmm >= 1510 and not has_today_file("reports/setup_review", f"setup_review_{now:%Y%m%d}.json"):
        report["actions"].append(run_command("daily-setup-review", ["review", "today"], timeout=1200))
        report["actions"].append(run_command("trade-funnel-eod", ["funnel", "status"], timeout=60))

    if now.isoweekday() == 5 and hhmm >= 1615 and not has_today_file("reports/weekly_review", f"weekly_review_*_{now:%Y%m%d}.json"):
        report["actions"].append(run_command("weekly-review", ["weekly-review", "today"], timeout=1200))

    if 500 <= hhmm <= 1800:
        report["actions"].append(run_command("human-escalations", ["human-escalations", "notify"], timeout=300))

    failures = [
        action for action in report["actions"]
        if action.get("returncode") not in {0, None}
        or action.get("operational_status") == "needs_attention"
    ]
    if failures:
        report["status"] = "needs_attention"
        report["notes"].append("One or more automation tasks failed; check action details and logs.")
    elif not report["actions"]:
        report["notes"].append("No catch-up action was due at this time.")
    else:
        report["notes"].append("Watchdog completed due automation checks.")

    return report


def has_today_file(relative_dir, pattern):
    directory = PROJECT_ROOT / relative_dir
    return directory.exists() and any(directory.glob(pattern))


def has_today_autonomy_action(now, action):
    directory = PROJECT_ROOT / "reports" / "autonomy"
    if not directory.exists():
        return False
    for path in directory.glob(f"autonomy_{now:%Y%m%d}_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("action") == action:
            return True
    return False


def run_command(label, args, timeout):
    started_at = datetime.now()
    command = [str(PYTHON), "main.py", *args]
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        result = {
            "label": label,
            "command": " ".join(["python", "main.py", *args]),
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "returncode": completed.returncode,
            "stdout_tail": tail_text(completed.stdout),
            "stderr_tail": tail_text(completed.stderr),
        }
        result["operational_status"] = classify_operational_status(label, completed.stdout, completed.stderr)
        return result
    except subprocess.TimeoutExpired as exc:
        return {
            "label": label,
            "command": " ".join(["python", "main.py", *args]),
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "returncode": 124,
            "stdout_tail": tail_text(exc.stdout),
            "stderr_tail": tail_text(exc.stderr),
        }


def tail_text(value, limit=1200):
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    value = str(value).strip()
    return value[-limit:]


def classify_operational_status(label, stdout, stderr):
    text = f"{stdout or ''}\n{stderr or ''}".lower()
    if label in {"morning-email", "email-retry", "intraday-discovery", "intraday-monitor", "human-escalations", "autonomy-plan-intraday"} and any(marker in text for marker in [
        "queued for retry",
        "status: queued",
        "retry_failed",
        "smtpauthenticationerror",
        "username and password not accepted",
        "could not resolve host",
        "nodename nor servname provided",
    ]):
        return "needs_attention"
    return "ok"


def format_automation_watchdog_report(report):
    lines = [
        "# Automation Watchdog",
        "",
        f"Created At: {report['created_at']}",
        f"Status: {report['status']}",
        "",
        "## Actions",
    ]
    actions = report.get("actions") or []
    if not actions:
        lines.append("- None.")
    else:
        for action in actions:
            lines.append(
                f"- {action['label']}: return code {action['returncode']} "
                f"({action['started_at']} to {action['finished_at']})"
            )
            if action.get("stderr_tail"):
                lines.append(f"  stderr: {action['stderr_tail']}")
            if action.get("operational_status") == "needs_attention":
                lines.append("  operational status: needs attention")

    lines.extend(["", "## Notes"])
    lines.extend([f"- {note}" for note in report.get("notes", [])] or ["- None."])
    return "\n".join(lines) + "\n"


def save_automation_watchdog_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    latest_json = REPORTS_DIR / "automation_watchdog.json"
    latest_md = REPORTS_DIR / "automation_watchdog.md"
    archive_json = REPORTS_DIR / f"automation_watchdog_{datetime.now():%Y%m%d_%H%M%S}.json"
    archive_md = REPORTS_DIR / f"automation_watchdog_{datetime.now():%Y%m%d_%H%M%S}.md"
    markdown = format_automation_watchdog_report(report)
    latest_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
    archive_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    archive_md.write_text(markdown, encoding="utf-8")
    return latest_md
