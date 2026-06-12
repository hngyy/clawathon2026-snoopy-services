"""Email delivery.

⚠️  Real delivery is NOT wired yet — see the TODO in `EmailSender.send`.
For now every send is appended to the outbox file so the whole flow works
end-to-end locally without SMTP credentials. When ready, implement the real
branch (SMTP / transactional API / MCP email target) — callers only use
`EmailSender.send(...)`, so nothing else changes.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("tour_bot.mailer")


class EmailSender:
    def __init__(self, from_address: str, from_name: str, outbox_path: Path):
        self._from = f"{from_name} <{from_address}>"
        self._outbox_path = outbox_path

    def send(self, to: str, subject: str, body: str, cc: list | None = None) -> dict:
        """Send (currently: record) an email. Returns a delivery record dict."""
        record = {
            "timestamp": datetime.now().isoformat(),
            "from": self._from,
            "to": to,
            "cc": cc or [],
            "subject": subject,
            "body": body,
        }
        # ── TODO: wire real delivery here later ───────────────────────
        # Example (SMTP):
        #   import smtplib, os
        #   from email.message import EmailMessage
        #   msg = EmailMessage(); msg["From"]=self._from; msg["To"]=to
        #   msg["Subject"]=subject; msg.set_content(body)
        #   with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"])) as s:
        #       s.starttls(); s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        #       s.send_message(msg)
        #   return {"delivered": True, **record}
        self._append_outbox(record)
        logger.info("EMAIL (stub) -> %s | %s", to, subject)
        return {"delivered": False, "stubbed": True, **record}

    def _append_outbox(self, record: dict) -> None:
        line = (
            f"\n=== {record['timestamp']} ===\n"
            f"From: {record['from']}\n"
            f"To: {record['to']}\n"
            f"Cc: {', '.join(record['cc']) if record['cc'] else '-'}\n"
            f"Subject: {record['subject']}\n\n"
            f"{record['body']}\n"
            f"{'-' * 50}\n"
        )
        with open(self._outbox_path, "a", encoding="utf-8") as f:
            f.write(line)
