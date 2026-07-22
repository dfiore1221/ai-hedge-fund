import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from delivery.email_delivery import load_email_config, send_email


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUEUE_DIR = PROJECT_ROOT / "reports" / "email_queue"
DEFAULT_EXPIRY_HOURS = 8


def queue_email(subject, body, attachment_path=None, kind="general", error=None, expiry_hours=DEFAULT_EXPIRY_HOURS):
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now()
    payload = {
        "id": build_email_id(kind, subject, created_at),
        "kind": kind,
        "status": "pending",
        "created_at": created_at.isoformat(timespec="seconds"),
        "expires_at": (created_at + timedelta(hours=expiry_hours)).isoformat(timespec="seconds"),
        "attempts": 0,
        "last_attempt_at": None,
        "last_error": str(error) if error else "",
        "subject": subject,
        "body": body,
        "attachment_path": str(attachment_path) if attachment_path else None,
    }
    path = QUEUE_DIR / f"{payload['id']}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def retry_pending_emails(kind=None, max_attempts=12):
    load_email_config()
    results = []
    for path in pending_email_paths():
        payload = load_payload(path)
        if not payload:
            continue
        if kind and payload.get("kind") != kind:
            continue

        if is_expired(payload):
            payload["status"] = "expired"
            payload["last_error"] = "Pending email expired before it could be sent."
            save_payload(path, payload)
            results.append(build_result(path, payload, "expired"))
            continue

        if int(payload.get("attempts") or 0) >= max_attempts:
            payload["status"] = "failed"
            payload["last_error"] = "Maximum retry attempts reached."
            save_payload(path, payload)
            results.append(build_result(path, payload, "failed"))
            continue

        payload["attempts"] = int(payload.get("attempts") or 0) + 1
        payload["last_attempt_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            delivery = send_email(
                payload["subject"],
                payload["body"],
                attachment_path=payload.get("attachment_path"),
            )
        except Exception as exc:
            payload["last_error"] = str(exc)
            save_payload(path, payload)
            results.append(build_result(path, payload, "retry_failed", error=exc))
            continue

        payload["status"] = "sent"
        payload["last_error"] = ""
        payload["delivery"] = delivery
        save_payload(path, payload)
        results.append(build_result(path, payload, "sent"))

    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "results": results,
        "sent_count": len([item for item in results if item["status"] == "sent"]),
        "pending_count": pending_count(kind=kind),
    }


def format_retry_report(report):
    lines = [
        "# Pending Email Retry",
        "",
        f"Checked At: {report['checked_at']}",
        f"Kind: {report.get('kind') or 'all'}",
        f"Sent: {report['sent_count']}",
        f"Pending Remaining: {report['pending_count']}",
        "",
        "## Results",
    ]
    if not report["results"]:
        lines.append("- No pending emails matched.")
    for item in report["results"]:
        line = f"- {item['status']}: {item['subject']} ({item['path']})"
        if item.get("error"):
            line += f" - {item['error']}"
        lines.append(line)
    return "\n".join(lines)


def pending_email_paths():
    if not QUEUE_DIR.exists():
        return []
    return sorted(QUEUE_DIR.glob("*.json"))


def pending_count(kind=None):
    count = 0
    for path in pending_email_paths():
        payload = load_payload(path)
        if not payload or payload.get("status") != "pending":
            continue
        if kind and payload.get("kind") != kind:
            continue
        if is_expired(payload):
            continue
        count += 1
    return count


def load_payload(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_payload(path, payload):
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def is_expired(payload):
    try:
        expires_at = datetime.fromisoformat(payload.get("expires_at", ""))
    except ValueError:
        return False
    return datetime.now() > expires_at


def build_result(path, payload, status, error=None):
    return {
        "path": str(path),
        "id": payload.get("id"),
        "kind": payload.get("kind"),
        "status": status,
        "subject": payload.get("subject"),
        "attempts": payload.get("attempts"),
        "error": str(error) if error else payload.get("last_error"),
    }


def build_email_id(kind, subject, created_at):
    digest = hashlib.sha256(f"{kind}|{subject}|{created_at.isoformat()}".encode("utf-8")).hexdigest()[:12]
    return f"{kind}-{created_at.strftime('%Y%m%d-%H%M%S')}-{digest}"
