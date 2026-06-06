"""
WhatsApp integration via Twilio WhatsApp Business API.

Outbound: send RFQ messages and quote responses
Inbound:  receive supplier replies via Twilio webhook → POST /webhooks/whatsapp

Twilio credentials required:
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_WHATSAPP_FROM  (e.g. "whatsapp:+14155238886")

For voice notes: inbound MediaUrl0 pointing to an .ogg file.
Transcription is handled separately by the parser using a speech-to-text service.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class WhatsAppClient:
    """Twilio WhatsApp Business API client."""

    def __init__(self, account_sid: str, auth_token: str, from_number: str):
        """
        from_number: the Twilio WhatsApp sender, e.g. "whatsapp:+14155238886"
        """
        self.from_number = from_number
        self._client = self._build_client(account_sid, auth_token)

    def _build_client(self, account_sid: str, auth_token: str):
        try:
            from twilio.rest import Client
            return Client(account_sid, auth_token)
        except ImportError:
            logger.warning(
                "twilio package not installed. WhatsApp integration unavailable. "
                "Run: pip install twilio"
            )
            return None

    async def send(
        self,
        to: str,
        message: str,
        media_url: Optional[str] = None,
    ) -> Optional[str]:
        """
        Send a WhatsApp message.

        to: recipient number in E.164 format, e.g. "+628123456789"
           (prefix "whatsapp:" is added automatically if missing)
        Returns Twilio message SID.
        """
        if not self._client:
            raise RuntimeError("Twilio client not initialised")

        to_wa = to if to.startswith("whatsapp:") else f"whatsapp:{to}"

        kwargs = {
            "from_": self.from_number,
            "to": to_wa,
            "body": message,
        }
        if media_url:
            kwargs["media_url"] = [media_url]

        msg = self._client.messages.create(**kwargs)
        logger.info("WhatsApp sent: sid=%s to=%s", msg.sid, to)
        return msg.sid


def parse_twilio_webhook(form_data: dict) -> dict:
    """
    Parse an inbound Twilio WhatsApp webhook payload into a normalised dict.

    form_data: the raw form-encoded POST body as a dict (from FastAPI Request.form())

    Returns:
    {
        from_number: str,
        body: str,
        media_urls: [str],   # images, videos, voice notes
        num_media: int,
        message_sid: str,
        profile_name: str,
    }
    """
    num_media = int(form_data.get("NumMedia", 0))
    media_urls = [
        form_data[f"MediaUrl{i}"]
        for i in range(num_media)
        if f"MediaUrl{i}" in form_data
    ]

    return {
        "from_number": form_data.get("From", "").replace("whatsapp:", ""),
        "body": form_data.get("Body", "").strip(),
        "media_urls": media_urls,
        "num_media": num_media,
        "message_sid": form_data.get("MessageSid", ""),
        "profile_name": form_data.get("ProfileName", ""),
        "media_content_types": [
            form_data.get(f"MediaContentType{i}", "")
            for i in range(num_media)
        ],
    }
