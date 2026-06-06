"""
Inbound webhook routes — Steps 3 ingestion.

POST /webhooks/whatsapp  — Twilio WhatsApp incoming message
POST /webhooks/email     — Gmail push notification (Pub/Sub)
"""
from __future__ import annotations

import base64
import json
import logging
from fastapi import APIRouter, Request, Response, HTTPException

from src.integrations.whatsapp import parse_twilio_webhook
from src.orchestrator import WorkflowContext
from src.supplier.parser import parse_supplier_response

logger = logging.getLogger(__name__)
router = APIRouter()

# Shared context store — imported from buyer.py in production; kept separate here for clarity
# In production, use a proper persistent store (Redis, Airtable, Postgres)
from api.routes.buyer import _contexts


def _find_rfq_for_supplier(whatsapp_number: str) -> tuple[str | None, WorkflowContext | None]:
    """Find the most recent dispatched RFQ for a given supplier WhatsApp number."""
    # In production: query Airtable for supplier by number, then active RFQ
    for rfq_id, ctx in reversed(list(_contexts.items())):
        return rfq_id, ctx  # placeholder — return first active RFQ
    return None, None


# ── Twilio WhatsApp webhook ────────────────────────────────────────────────────

@router.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Receive inbound WhatsApp messages from suppliers via Twilio.
    Twilio sends form-encoded POST bodies.
    """
    form = dict(await request.form())
    parsed_msg = parse_twilio_webhook(form)

    logger.info(
        "WhatsApp inbound from %s: body='%s...' media=%d",
        parsed_msg["from_number"],
        parsed_msg["body"][:60],
        parsed_msg["num_media"],
    )

    rfq_id, ctx = _find_rfq_for_supplier(parsed_msg["from_number"])
    if not ctx:
        logger.warning("No active RFQ found for %s", parsed_msg["from_number"])
        # Return 200 to Twilio regardless — don't retry unmatched messages
        return Response(content="<Response/>", media_type="application/xml")

    # Build supplier ID from number (lookup in production)
    supplier_id = parsed_msg["from_number"].replace("+", "")

    # Combine text + media URLs into a single parseable string
    combined = parsed_msg["body"]
    if parsed_msg["media_urls"]:
        combined += "\n[Media attached: " + ", ".join(parsed_msg["media_urls"]) + "]"

    ctx = _find_and_ingest(ctx, combined, supplier_id, "whatsapp")
    _contexts[rfq_id] = ctx

    return Response(content="<Response/>", media_type="application/xml")


# ── Gmail push notification webhook ───────────────────────────────────────────

@router.post("/email")
async def email_webhook(request: Request):
    """
    Receive Gmail push notifications via Google Cloud Pub/Sub.
    Google sends base64-encoded JSON bodies.
    """
    body = await request.json()
    message = body.get("message", {})
    data_b64 = message.get("data", "")

    try:
        data = json.loads(base64.urlsafe_b64decode(data_b64 + "=="))
    except Exception as exc:
        logger.warning("Could not decode Pub/Sub message: %s", exc)
        return {"status": "ignored"}

    email_address = data.get("emailAddress", "")
    history_id = data.get("historyId")

    logger.info("Gmail push notification: address=%s historyId=%s", email_address, history_id)

    # In production: use history_id to fetch new messages from Gmail API,
    # then parse and ingest each one. Placeholder acknowledgement here.
    return {"status": "acknowledged", "historyId": history_id}


def _find_and_ingest(ctx, text: str, supplier_id: str, source: str) -> WorkflowContext:
    """Parse incoming text and add to context."""
    try:
        import os, anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY")) if os.getenv("ANTHROPIC_API_KEY") else None
    except ImportError:
        client = None

    ctx.supplier_quotes.append(
        parse_supplier_response(
            raw_text=text,
            supplier_id=supplier_id,
            rfq_id=ctx.rfq.id,
            source=source,
            anthropic_client=client,
        )
    )
    return ctx
