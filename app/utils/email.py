"""
Simple SMTP email utility.
Falls back to logging when SMTP is not configured.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings

logger = logging.getLogger(__name__)


def send_email(
    to_email: str, subject: str, html_body: str, text_body: str | None = None
) -> bool:
    """Send an email via SMTP. Returns True on success, False otherwise.

    If SMTP settings are missing, logs the email content and returns True to avoid blocking flows in dev.
    """
    if not settings.SMTP_HOST or not settings.SMTP_PORT or not settings.SMTP_FROM:
        logger.warning(
            "SMTP not configured. Email to %s with subject '%s' would be sent.\nBody: %s",
            to_email,
            subject,
            html_body,
        )
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to_email

        if text_body:
            msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM, [to_email], msg.as_string())
        return True
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to_email, exc)
        return False
