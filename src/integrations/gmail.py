"""
Gmail integration — send RFQs and receive supplier replies.

Uses the Gmail MCP connector when running in Claude/Cowork context,
or falls back to the Gmail API directly via google-auth + googleapiclient.

Webhook ingestion (inbound replies) is handled by:
  api/routes/webhook.py → POST /webhooks/email
which receives Gmail push notifications via Google Cloud Pub/Sub.
"""
from __future__ import annotations

import base64
import email
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)


class GmailClient:
    """Thin wrapper around the Gmail API for sending and reading messages."""

    def __init__(self, credentials_json: str, sender_address: str):
        """
        credentials_json: path to OAuth2 credentials file or JSON string
        sender_address:   the From: address (must be authorised in Gmail)
        """
        self.sender = sender_address
        self._service = self._build_service(credentials_json)

    def _build_service(self, credentials_json: str):
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            import json

            if credentials_json.strip().startswith("{"):
                creds_data = json.loads(credentials_json)
            else:
                with open(credentials_json) as f:
                    creds_data = json.load(f)

            creds = Credentials(
                token=creds_data.get("token"),
                refresh_token=creds_data.get("refresh_token"),
                token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=creds_data.get("client_id"),
                client_secret=creds_data.get("client_secret"),
                scopes=["https://www.googleapis.com/auth/gmail.modify"],
            )
            return build("gmail", "v1", credentials=creds, cache_discovery=False)
        except ImportError:
            logger.warning(
                "google-auth / google-api-python-client not installed. "
                "Gmail integration unavailable."
            )
            return None

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> Optional[str]:
        """Send an email and return the Gmail message ID."""
        if not self._service:
            raise RuntimeError("Gmail service not initialised")

        msg = MIMEMultipart("alternative")
        msg["From"] = self.sender
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.attach(MIMEText(body, "plain"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        body_payload: dict = {"raw": raw}
        if thread_id:
            body_payload["threadId"] = thread_id

        result = (
            self._service.users()
            .messages()
            .send(userId="me", body=body_payload)
            .execute()
        )
        logger.info("Gmail sent: id=%s to=%s", result.get("id"), to)
        return result.get("id")

    def parse_inbound(self, raw_message: dict) -> dict:
        """
        Parse a Gmail message dict (from the API) into a normalised dict:
        {from, subject, body_text, thread_id, message_id, attachments: []}
        """
        headers = {
            h["name"].lower(): h["value"]
            for h in raw_message.get("payload", {}).get("headers", [])
        }
        body_text = self._extract_body(raw_message.get("payload", {}))
        return {
            "from": headers.get("from", ""),
            "subject": headers.get("subject", ""),
            "body_text": body_text,
            "thread_id": raw_message.get("threadId"),
            "message_id": raw_message.get("id"),
            "attachments": self._extract_attachment_ids(raw_message.get("payload", {})),
        }

    def _extract_body(self, payload: dict) -> str:
        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data", "")
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        for part in payload.get("parts", []):
            text = self._extract_body(part)
            if text:
                return text
        return ""

    def _extract_attachment_ids(self, payload: dict) -> list[dict]:
        attachments = []
        for part in payload.get("parts", []):
            if part.get("filename") and part.get("body", {}).get("attachmentId"):
                attachments.append({
                    "filename": part["filename"],
                    "mime_type": part.get("mimeType"),
                    "attachment_id": part["body"]["attachmentId"],
                })
        return attachments
