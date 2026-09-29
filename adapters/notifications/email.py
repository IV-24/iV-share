"""Gmail SMTP email notifications. One thin adapter — a caller only ever
sees EmailConfig and send_email()'s parameters, never smtplib directly,
so swapping to a different provider (SES, Postmark, ...) later doesn't
touch anything upstream. Migrated from backend/app/sleep_cycle.py's
_send_email().

Config lives here, not in core/configuration, for the same reason
StorageConfig has no Supabase-specific fields: which email service (if
any) sends a notification is an adapter's business, not core's."""

import os
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText


@dataclass
class EmailConfig:
    address: str | None = None
    app_password: str | None = None
    notify_to: str | None = None


def load_email_config(env: dict[str, str] | None = None) -> EmailConfig:
    source = env if env is not None else os.environ
    address = source.get("GMAIL_ADDRESS")
    return EmailConfig(
        address=address,
        app_password=source.get("GMAIL_APP_PASSWORD"),
        notify_to=source.get("NOTIFY_EMAIL", address),
    )


def is_configured(config: EmailConfig) -> bool:
    return bool(config.address and config.app_password and config.notify_to)


def send_email(*, config: EmailConfig, subject: str, html_body: str) -> None:
    """No-ops (rather than raising) if config is incomplete — matches
    the original's "skip send, don't fail the job" behavior. Callers that
    need to know whether a send will actually happen should check
    is_configured() themselves first."""
    if not is_configured(config):
        return

    msg = MIMEText(html_body, "html")
    msg["Subject"] = subject
    msg["From"] = config.address
    msg["To"] = config.notify_to

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(config.address, config.app_password)
        server.send_message(msg)
