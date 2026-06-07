"""
Inbound webhook routes — Step 3 ingestion.

POST /webhooks/whatsapp  — Twilio WhatsApp incoming message
POST /webhooks/email     — SendGrid Inbound Parse webhook
"""
from __future__ import annotations

import logging
import re
from fastapi import APIRouter, Request, Response

from src.integrations.whatsapp import parse_twilio_webhook
from src.integrations.email import parse_sendgrid_inbound
from src.orchestrator import WorkflowContext
from src.supplier.parser import parse_supplier_response
from src.store import get_context_store

logger = logging.getLogger(__name__)
router = APIRouter()


async def _auto_quote_if_ready(rfq_id: str, ctx) -> None:
    """
    Called after every inbound supplier reply.
    Once at least one response exists and no quote has been generated yet,
    build the comparison matrix, generate the buyer quote, and email it.
    Best-effort — failures are logged and never bubble up to the caller.
    """
    if not ctx.supplier_quotes or ctx.current_quote is not None:
        return

    # Only proceed if at least one response contains actual priced line items
    total_priced_items = sum(len(q.line_items) for q in ctx.supplier_quotes)
    if total_priced_items == 0:
        logger.warning(
            "Auto-quote: %d supplier response(s) received for RFQ %s but no prices parsed — "
            "check that the reply includes a price with a currency or unit (e.g. 'USD 800/CBM')",
            len(ctx.supplier_quotes), rfq_id,
        )
        return

    logger.info("Auto-quote: %d response(s), %d priced items for RFQ %s — generating",
                len(ctx.supplier_quotes), total_priced_items, rfq_id)
    try:
        from src.integrations.email import get_email_client
        from src.orchestrator import WorkflowOrchestrator

        orchestrator = WorkflowOrchestrator(email_client=get_email_client())
        ctx = orchestrator.build_matrix(ctx)
        ctx = await orchestrator.generate_quote(ctx)   # emails buyer on success
        get_context_store().save(rfq_id, ctx)
        logger.info("Auto-quote: quote generated and emailed to buyer for RFQ %s", rfq_id)
    except Exception as exc:
        logger.error("Auto-quote failed for RFQ %s: %s", rfq_id, exc)


def _find_rfq_for_supplier(identifier: str) -> tuple[str | None, WorkflowContext | None]:
    """
    Find the most recent active RFQ for a given supplier.
    In production this should query Airtable by supplier phone/email.
    Current implementation: load from store by scanning active RFQs
    via Airtable's Suppliers table lookup.
    """
    try:
        from src.integrations.airtable import get_airtable_client
        at = get_airtable_client()
        if not at:
            return None, None

        # Find all active RFQs waiting for supplier responses
        records = at.list_records(
            "RFQs",
            filter_formula="OR({Status}='dispatched', {Status}='responses_received')",
            max_records=50,
        )
        if not records:
            return None, None

        # Sort by createdTime descending — match the most recently dispatched RFQ
        records.sort(key=lambda r: r.get("createdTime", ""), reverse=True)
        rfq_id = records[0]["fields"].get("RFQ ID")
        if not rfq_id:
            return None, None

        store = get_context_store()
        ctx = store.load(rfq_id)
        return rfq_id, ctx

    except Exception as exc:
        logger.error("_find_rfq_for_supplier error: %s", exc)
        return None, None


def _ingest(ctx: WorkflowContext, text: str, supplier_id: str, source: str) -> WorkflowContext:
    """Parse incoming text and add parsed response to context."""
    try:
        import os, anthropic
        client = (
            anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            if os.getenv("ANTHROPIC_API_KEY") else None
        )
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
        return Response(content="<Response/>", media_type="application/xml")

    supplier_id = parsed_msg["from_number"].replace("+", "")
    combined = parsed_msg["body"]
    if parsed_msg["media_urls"]:
        combined += "\n[Media attached: " + ", ".join(parsed_msg["media_urls"]) + "]"

    ctx = _ingest(ctx, combined, supplier_id, "whatsapp")
    get_context_store().save(rfq_id, ctx)
    await _auto_quote_if_ready(rfq_id, ctx)

    return Response(content="<Response/>", media_type="application/xml")


# ── SendGrid Inbound Parse webhook ────────────────────────────────────────────

@router.post("/email")
async def email_webhook(request: Request):
    """
    Receive inbound supplier emails.
    Accepts plain form-data (from, to, subject, text) — same keys as SendGrid Inbound Parse.
    """
    import traceback
    try:
        # Accept either JSON body or form-data (requires python-multipart for form)
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            form = await request.json()
        else:
            try:
                form = dict(await request.form())
            except Exception:
                form = await request.json()
        parsed = parse_sendgrid_inbound(form)

        logger.info(
            "Email inbound from=%s subject='%s'",
            parsed["from"],
            parsed["subject"][:80],
        )

        # Allow caller to pin the RFQ (test mode / forwarding with reference)
        explicit_rfq_id = form.get("rfq_id") or parsed.get("rfq_id")
        if explicit_rfq_id:
            store = get_context_store()
            ctx = store.load(explicit_rfq_id)
            rfq_id = explicit_rfq_id if ctx else None
        else:
            rfq_id, ctx = _find_rfq_for_supplier(parsed["from"])

        if not ctx:
            logger.warning("No active RFQ found for sender %s", parsed["from"])
            return {"status": "ignored"}

        match = re.search(r"[\w.+-]+@[\w-]+\.[a-z]+", parsed["from"])
        supplier_id = match.group(0) if match else parsed["from"]

        body = parsed.get("body_text") or ""
        if not body.strip():
            logger.warning("Empty body from %s — ignoring", parsed["from"])
            return {"status": "ignored", "reason": "empty body"}

        ctx = _ingest(ctx, body, supplier_id, "email")

        # Update RFQ status so the IMAP lookup keeps finding it
        from src.models.rfq import RFQStatus
        ctx.rfq.status = RFQStatus.RESPONSES_RECEIVED

        get_context_store().save(rfq_id, ctx)
        logger.info("Ingested reply from %s → RFQ %s (total responses: %d)",
                    supplier_id, rfq_id, len(ctx.supplier_quotes))

        await _auto_quote_if_ready(rfq_id, ctx)

        return {"status": "ok", "rfq_id": rfq_id, "supplier_id": supplier_id}

    except Exception as exc:
        logger.error("email_webhook error: %s\n%s", exc, traceback.format_exc())
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")
