import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

logger = logging.getLogger("app.email")
FROM_ADDRESS = "no-reply@widget-platform.local"


class EmailSendError(Exception):
    pass


def mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def send_email(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    if settings.force_email_failure:
        raise EmailSendError("forced failure (FORCE_EMAIL_FAILURE=true)")

    if settings.email_mode == "smtp":
        message = EmailMessage()
        message["From"] = FROM_ADDRESS
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as smtp:
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailSendError(f"smtp send failed: {type(exc).__name__}") from exc
        logger.info("email_sent mode=smtp to=%s", mask_email(to))
        return

    logger.info("email_sent mode=console to=%s subject=%r", mask_email(to), subject)