from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Optional


def send_email(subject: str, html_body: str, recipient: Optional[str] = None) -> None:
    """Send an email using the shared SMTP sender.

    ``recipient`` is optional for backwards compatibility.  Multi-user runs
    pass each user's own destination explicitly while keeping SMTP credentials
    in the process environment.
    """

    recipient = recipient or os.environ.get("ALERT_EMAIL", "")
    host = os.environ.get("SMTP_HOST", "")
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    port = int(os.environ.get("SMTP_PORT", "465"))
    use_ssl = os.environ.get("SMTP_USE_SSL", "true").lower() in {"1", "true", "yes"}
    missing = [
        name
        for name, value in {
            "ALERT_EMAIL": recipient,
            "SMTP_HOST": host,
            "SMTP_USERNAME": username,
            "SMTP_PASSWORD": password,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError("邮件配置不完整: {}".format(", ".join(missing)))

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = username
    message["To"] = recipient
    message.set_content("请使用支持 HTML 的邮件客户端查看 CampusJobRadar 摘要。")
    message.add_alternative(html_body, subtype="html")

    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_class(host, port, timeout=30) as client:
        if not use_ssl:
            client.starttls()
        client.login(username, password)
        client.send_message(message)
