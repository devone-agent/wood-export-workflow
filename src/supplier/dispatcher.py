"""
Step 2 — Dispatch RFQ payloads to suppliers via Email and WhatsApp.

Email:     SendGrid Web API v3 (via src.integrations.email.EmailClient)
WhatsApp:  Twilio WhatsApp Business API
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .rfq_builder import SupplierRFQPayload

logger = logging.getLogger(__name__)


class DispatchChannel(str, Enum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    BOTH = "both"


@dataclass
class DispatchResult:
    supplier_id: str
    supplier_name: str
    email_sent: bool = False
    whatsapp_sent: bool = False
    email_error: Optional[str] = None
    whatsapp_error: Optional[str] = None
    email_message_id: Optional[str] = None
    whatsapp_message_id: Optional[str] = None

    @property
    def any_success(self) -> bool:
        return self.email_sent or self.whatsapp_sent

    @property
    def all_failed(self) -> bool:
        return not self.email_sent and not self.whatsapp_sent


async def dispatch_rfq_to_suppliers(
    payloads: list[SupplierRFQPayload],
    channel: DispatchChannel = DispatchChannel.BOTH,
    email_client=None,      # injected: src.integrations.email.EmailClient
    whatsapp_client=None,   # injected: src.integrations.whatsapp.WhatsAppClient
) -> list[DispatchResult]:
    """
    Dispatch RFQ payloads to all suppliers concurrently.
    Returns one DispatchResult per supplier.
    """
    import asyncio
    tasks = [
        _dispatch_one(p, channel, email_client, whatsapp_client)
        for p in payloads
    ]
    return await asyncio.gather(*tasks)


async def _dispatch_one(
    payload: SupplierRFQPayload,
    channel: DispatchChannel,
    email_client,
    whatsapp_client,
) -> DispatchResult:
    result = DispatchResult(
        supplier_id=payload.supplier_id,
        supplier_name=payload.supplier_name,
    )

    # ── EMAIL ─────────────────────────────────────────────────────────────────
    if channel in (DispatchChannel.EMAIL, DispatchChannel.BOTH):
        if payload.supplier_email and email_client:
            try:
                msg_id = await email_client.send(
                    to=payload.supplier_email,
                    subject=payload.email_subject,
                    body=payload.email_body,
                    html_body=payload.email_html or None,
                )
                result.email_sent = True
                result.email_message_id = msg_id
                logger.info("Email sent to %s (%s)", payload.supplier_name, payload.supplier_email)
            except Exception as exc:
                result.email_error = str(exc)
                logger.error("Email failed for %s: %s", payload.supplier_name, exc)
        elif not payload.supplier_email:
            result.email_error = "No email address on record"
        elif not email_client:
            result.email_error = "Email client not configured"

    # ── WHATSAPP ──────────────────────────────────────────────────────────────
    if channel in (DispatchChannel.WHATSAPP, DispatchChannel.BOTH):
        if payload.supplier_whatsapp and whatsapp_client:
            try:
                msg_id = await whatsapp_client.send(
                    to=payload.supplier_whatsapp,
                    message=payload.whatsapp_message,
                )
                result.whatsapp_sent = True
                result.whatsapp_message_id = msg_id
                logger.info("WhatsApp sent to %s (%s)", payload.supplier_name, payload.supplier_whatsapp)
            except Exception as exc:
                result.whatsapp_error = str(exc)
                logger.error("WhatsApp failed for %s: %s", payload.supplier_name, exc)
        elif not payload.supplier_whatsapp:
            result.whatsapp_error = "No WhatsApp number on record"
        elif not whatsapp_client:
            result.whatsapp_error = "WhatsApp client not configured"

    return result
