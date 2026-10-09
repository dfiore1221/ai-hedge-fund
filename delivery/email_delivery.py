import os
import socket
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

try:
    import certifi
except ImportError:  # pragma: no cover - certifi is included in the project environment.
    certifi = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class EmailConfig:
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    email_from: str
    email_to: str
    approved_recipients: list[str]
    use_tls: bool = True


def load_email_config():
    load_dotenv(PROJECT_ROOT / ".env")

    required = {
        "SMTP_HOST": os.getenv("SMTP_HOST"),
        "SMTP_USERNAME": os.getenv("SMTP_USERNAME"),
        "SMTP_PASSWORD": os.getenv("SMTP_PASSWORD"),
        "EMAIL_FROM": os.getenv("EMAIL_FROM"),
        "MORNING_BRIEF_EMAIL_TO": os.getenv("MORNING_BRIEF_EMAIL_TO"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "Missing email settings in .env: "
            + ", ".join(missing)
            + ". Add these before sending the morning brief email."
        )

    config = EmailConfig(
        smtp_host=required["SMTP_HOST"],
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=required["SMTP_USERNAME"],
        smtp_password=normalize_app_password(required["SMTP_PASSWORD"]),
        email_from=required["EMAIL_FROM"],
        email_to=required["MORNING_BRIEF_EMAIL_TO"],
        approved_recipients=parse_csv(os.getenv("APPROVED_EMAIL_RECIPIENTS", "")),
        use_tls=os.getenv("SMTP_USE_TLS", "true").lower() != "false",
    )
    validate_email_recipients(config)
    return config


def send_email(subject, body, attachment_path=None, config=None):
    config = config or load_email_config()
    validate_email_recipients(config)

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.email_from
    message["To"] = config.email_to
    message.set_content(body)

    if attachment_path:
        path = Path(attachment_path)
        message.add_attachment(
            path.read_bytes(),
            maintype="text",
            subtype="markdown",
            filename=path.name,
        )

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as server:
        if config.use_tls:
            server.starttls(context=build_tls_context())
        server.login(config.smtp_username, config.smtp_password)
        server.send_message(message)

    return {
        "sent": True,
        "to": config.email_to,
        "from": config.email_from,
        "subject": subject,
        "attachment": str(attachment_path) if attachment_path else None,
    }


def check_email_health(config=None):
    """Validate SMTP connectivity and authentication without sending a message."""
    try:
        config = config or load_email_config()
    except Exception as exc:
        return email_health_failure("configuration_error", exc)

    try:
        server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=20)
        if config.use_tls:
            server.starttls(context=build_tls_context())
    except (socket.gaierror, TimeoutError, OSError, smtplib.SMTPException) as exc:
        return email_health_failure(
            "connection_failed",
            exc,
            action="Confirm internet access, SMTP_HOST, SMTP_PORT, and firewall/network availability.",
        )

    try:
        server.login(config.smtp_username, config.smtp_password)
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected) as exc:
        return email_health_failure(
            "authentication_failed",
            exc,
            action=(
                "Replace SMTP_PASSWORD with a current Google app password. "
                "A normal Google account password will not work when two-step verification is enabled."
            ),
        )
    except smtplib.SMTPException as exc:
        return email_health_failure(
            "authentication_failed",
            exc,
            action="Create a new Google app password and replace SMTP_PASSWORD in .env.",
        )
    finally:
        try:
            server.quit()
        except (OSError, smtplib.SMTPException):
            pass

    return {
        "status": "ok",
        "host": config.smtp_host,
        "port": config.smtp_port,
        "username": mask_email(config.smtp_username),
        "recipient_count": len(parse_csv(config.email_to)),
        "action": "No action required.",
    }


def email_health_failure(status, error, action="Review the local email settings."):
    return {
        "status": status,
        "error_type": type(error).__name__,
        "error": sanitize_email_error(error),
        "action": action,
    }


def build_tls_context():
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def sanitize_email_error(error):
    text = str(error or "")
    return text[:500]


def mask_email(value):
    value = str(value or "")
    if "@" not in value:
        return "configured" if value else "missing"
    name, domain = value.split("@", 1)
    visible = name[:2] if name else ""
    return f"{visible}***@{domain}"


def validate_email_recipients(config):
    recipients = parse_csv(config.email_to)
    approved = [item.lower() for item in config.approved_recipients]
    if not approved:
        return

    unapproved = sorted(set(item.lower() for item in recipients) - set(approved))
    if unapproved:
        raise RuntimeError(
            "Email recipient is not in APPROVED_EMAIL_RECIPIENTS: "
            + ", ".join(unapproved)
        )


def parse_csv(value):
    return [item.strip() for item in value.split(",") if item.strip()]


def normalize_app_password(value):
    """Gmail displays app passwords in four groups; SMTP expects the 16 raw characters."""
    return "".join(str(value or "").split())
