"""
Email integration — Hostinger SMTP (outbound) + IMAP polling (inbound).

No third-party email SDK needed — uses Python's built-in smtplib / imaplib.

Outbound:
  smtp.hostinger.com:587 (STARTTLS)

Inbound (supplier replies):
  imap.hostinger.com:993 (SSL)
  Poll via poll_new_replies() — call this on a background loop or scheduled task.

Env vars:
  EMAIL_HOST          smtp.hostinger.com
  EMAIL_PORT          587
  EMAIL_USER          pav@instructset.com
  EMAIL_PASSWORD      <password>
  IMAP_HOST           imap.hostinger.com
  IMAP_PORT           993
  EMAIL_SENDER_NAME   Wood Export Bot   (optional)
"""
from __future__ import annotations

import email as email_lib
import imaplib
import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)


class EmailClient:
    """SMTP email client for outbound RFQ dispatch (Hostinger)."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        sender_name: str = "Wood Export Bot",
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = f"{sender_name} <{user}>"

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to: Optional[str] = None,
        to_name: Optional[str] = None,
        html_body: Optional[str] = None,
    ) -> Optional[str]:
        """
        Send a plain-text email via SMTP.
        Returns the Message-ID on success.
        Runs synchronously inside an async wrapper (SMTP is blocking but fast).
        """
        import asyncio
        return await asyncio.get_event_loop().run_in_executor(
            None, self._send_sync, to, subject, body, reply_to, to_name, html_body
        )

    def _send_sync(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to: Optional[str],
        to_name: Optional[str],
        html_body: Optional[str] = None,
    ) -> str:
        msg = MIMEMultipart("alternative")
        msg["From"] = self.sender
        msg["To"] = f"{to_name} <{to}>" if to_name else to
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to
        # Plain text first (fallback), then HTML (preferred by clients that support it)
        msg.attach(MIMEText(body, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=15) as smtp:
            smtp.login(self.user, self.password)
            smtp.sendmail(self.user, [to], msg.as_bytes())

        msg_id = msg.get("Message-ID", "sent")
        logger.info("Email sent to=%s subject='%s'", to, subject)
        return msg_id


class IMAPClient:
    """IMAP client for polling inbound supplier replies (Hostinger)."""

    def __init__(self, host: str, port: int, user: str, password: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password

    def poll_new_replies(self, folder: str = "INBOX", mark_seen: bool = True) -> list[dict]:
        """
        Fetch all UNSEEN messages from the inbox.
        Returns a list of normalised dicts:
          {from, to, subject, body_text, message_id, date}
        """
        results = []
        try:
            with imaplib.IMAP4_SSL(self.host, self.port) as imap:
                imap.login(self.user, self.password)
                imap.select(folder)

                _, data = imap.search(None, "UNSEEN")
                uids = data[0].split()
                logger.info("IMAP: %d unseen message(s) in %s", len(uids), folder)

                for uid in uids:
                    _, msg_data = imap.fetch(uid, "(RFC822)")
                    raw = msg_data[0][1]
                    parsed = _parse_raw_email(raw)
                    results.append(parsed)

                    if mark_seen:
                        imap.store(uid, "+FLAGS", "\\Seen")

        except Exception as exc:
            logger.error("IMAP poll failed: %s", exc)

        return results


def _parse_raw_email(raw: bytes) -> dict:
    """Parse a raw RFC822 email into a normalised dict."""
    msg = email_lib.message_from_bytes(raw)

    body_text = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                charset = part.get_content_charset() or "utf-8"
                body_text = part.get_payload(decode=True).decode(charset, errors="replace")
                break
    else:
        charset = msg.get_content_charset() or "utf-8"
        body_text = msg.get_payload(decode=True).decode(charset, errors="replace")

    return {
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "subject": msg.get("Subject", ""),
        "body_text": body_text,
        "message_id": msg.get("Message-ID", ""),
        "date": msg.get("Date", ""),
    }


def parse_sendgrid_inbound(form_data: dict) -> dict:
    """
    Compatibility shim — parses a form-data dict into the same normalised
    format as _parse_raw_email. Used by the /webhooks/email route for
    any forwarded/webhook-based delivery.
    """
    return {
        "from": form_data.get("from", ""),
        "to": form_data.get("to", ""),
        "subject": form_data.get("subject", ""),
        "body_text": form_data.get("text", ""),
        "message_id": form_data.get("Message-ID", ""),
        "date": "",
    }


def get_email_client() -> Optional[EmailClient]:
    """Build an EmailClient from environment variables."""
    user = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASSWORD")
    host = os.getenv("EMAIL_HOST", "smtp.hostinger.com")
    port = int(os.getenv("EMAIL_PORT", "587"))
    sender_name = os.getenv("EMAIL_SENDER_NAME", "Wood Export Bot")

    if not user or not password:
        logger.warning("EMAIL_USER or EMAIL_PASSWORD not set — email disabled")
        return None
    return EmailClient(host=host, port=port, user=user, password=password, sender_name=sender_name)


def get_imap_client() -> Optional[IMAPClient]:
    """Build an IMAPClient from environment variables."""
    user = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASSWORD")
    host = os.getenv("IMAP_HOST", "imap.hostinger.com")
    port = int(os.getenv("IMAP_PORT", "993"))

    if not user or not password:
        return None
    return IMAPClient(host=host, port=port, user=user, password=password)
