"""Email delivery via SMTP (Mailtrap-compatible for testing, any SMTP for production).

Env vars:
  SMTP_HOST      — e.g. sandbox.smtp.mailtrap.io
  SMTP_PORT      — e.g. 587
  SMTP_USER      — Mailtrap or production SMTP username
  SMTP_PASSWORD  — Mailtrap or production SMTP password
  SMTP_FROM      — From address (default: noreply@domi.co.ke)

If SMTP_HOST / SMTP_USER / SMTP_PASSWORD are not set, send_email() returns
(False, 'SMTP not configured') and the caller should mark the outbox entry
as 'simulated' rather than 'sent'.
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_email(to_email, subject, body, to_name=None):
    """Send a plain-text email. Returns (success: bool, error: str | None)."""
    host = os.environ.get('SMTP_HOST', '').strip()
    port = int(os.environ.get('SMTP_PORT', 587))
    user = os.environ.get('SMTP_USER', '').strip()
    password = os.environ.get('SMTP_PASSWORD', '').strip()
    from_addr = os.environ.get('SMTP_FROM', 'noreply@domi.co.ke').strip()

    if not (host and user and password):
        return False, 'SMTP not configured'

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = from_addr
        msg['To'] = f'{to_name} <{to_email}>' if to_name else to_email
        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, [to_email], msg.as_string())
        return True, None
    except Exception as exc:
        return False, str(exc)
