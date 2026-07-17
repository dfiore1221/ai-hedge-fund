import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv


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
        smtp_password=required["SMTP_PASSWORD"],
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
            server.starttls()
        server.login(config.smtp_username, config.smtp_password)
        server.send_message(message)

    return {
        "to": config.email_to,
        "from": config.email_from,
        "subject": subject,
        "attachment": str(attachment_path) if attachment_path else None,
    }


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
