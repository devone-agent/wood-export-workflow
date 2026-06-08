"""
Email integration — Resend API (outbound) + IMAP polling (inbound).

Outbound:
  Resend HTTP API — works on all cloud platforms (no SMTP port restrictions).
  Sign up at resend.com, verify your domain, get an API key.

Inbound (supplier replies):
  imap.titan.email:993 (SSL)
  Poll via poll_new_replies() — called on a background loop.

Env vars:
  RESEND_API_KEY      re_xxxx...
  EMAIL_USER          pav@instructset.com   (used as From address)
  EMAIL_SENDER_NAME   Wood Export Bot       (optional)
  IMAP_HOST           imap.titan.email
  IMAP_PORT           993
"""
from __future__ import annotations

import email as email_lib
import imaplib
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class EmailClient:
    """Outbound email via Resend HTTP API — no SMTP port restrictions."""

    def __init__(
        self,
        api_key: str,
        from_address: str,
        sender_name: str = "Wood Export Bot",
    ):
        self.api_key = api_key
        self.from_address = from_address
        self.sender = f"{sender_name} <{from_address}>"

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to: Optional[str] = None,
        to_name: Optional[str] = None,
        html_body: Optional[str] = None,
    ) -> Optional[str]:
        """Send via Resend API. Returns message ID on success."""
        import httpx

        to_formatted = f"{to_name} <{to}>" if to_name else to

        payload: dict = {
            "from": self.sender,
            "to": [to_formatted],
            "subject": subject,
            "text": body,
        }
        if html_body:
            payload["html"] = html_body
        if reply_to:
            payload["reply_to"] = [reply_to]

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Resend API error {resp.status_code}: {resp.text}")

        msg_id = resp.json().get("id", "sent")
        logger.info("Email sent via Resend to=%s subject='%s' id=%s", to, subject, msg_id)
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
    """Build an EmailClient from environment variables (uses Resend API)."""
    api_key = os.getenv("RESEND_API_KEY")
    from_address = os.getenv("EMAIL_USER")
    sender_name = os.getenv("EMAIL_SENDER_NAME", "Wood Export Bot")

    if not api_key:
        logger.warning("RESEND_API_KEY not set — outbound email disabled")
        return None
    if not from_address:
        logger.warning("EMAIL_USER not set — outbound email disabled")
        return None
    return EmailClient(api_key=api_key, from_address=from_address, sender_name=sender_name)


def get_imap_client() -> Optional[IMAPClient]:
    """Build an IMAPClient from environment variables."""
    user = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASSWORD")
    host = os.getenv("IMAP_HOST", "imap.hostinger.com")
    port = int(os.getenv("IMAP_PORT", "993"))

    if not user or not password:
        return None
    return IMAPClient(host=host, port=port, user=user, password=password)
