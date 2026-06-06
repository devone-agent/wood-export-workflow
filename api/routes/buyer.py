"""
Buyer-facing API routes — Steps 1, 5, 6.
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.buyer.form import validate_buyer_form, form_to_rfq
from src.orchestrator import WorkflowOrchestrator, WorkflowContext

router = APIRouter()

# In-memory store (replace with Airtable/DB in production)
_contexts: dict[str, WorkflowContext] = {}


def _get_orchestrator() -> WorkflowOrchestrator:
    """Build orchestrator from environment config."""
    import os
    from src.integrations.gmail import GmailClient
    from src.integrations.whatsapp import WhatsAppClient

    email_client = None
    whatsapp_client = None

    if os.getenv("GMAIL_CREDENTIALS") and os.getenv("GMAIL_SENDER"):
        try:
            email_client = GmailClient(
                os.getenv("GMAIL_CREDENTIALS"),
                os.getenv("GMAIL_SENDER"),
            )
        except Exception:
            pass

    if all(os.getenv(k) for k in ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM"]):
        try:
            whatsapp_client = WhatsAppClient(
                os.getenv("TWILIO_ACCOUNT_SID"),
                os.getenv("TWILIO_AUTH_TOKEN"),
                os.getenv("TWILIO_WHATSAPP_FROM"),
            )
        except Exception:
            pass

    return WorkflowOrchestrator(
        email_client=email_client,
        whatsapp_client=whatsapp_client,
    )


# ── POST /rfq ─────────────────────────────────────────────────────────────────

@router.post("")
async def submit_rfq(body: dict):
    """Step 1 — Submit and validate buyer demand form."""
    form, errors = validate_buyer_form(body)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    rfq = form_to_rfq(form)
    ctx = WorkflowContext(rfq=rfq)
    _contexts[rfq.id] = ctx

    return {
        "rfq_id": rfq.id,
        "status": rfq.status.value,
        "line_items": len(rfq.line_items),
        "message": "RFQ created. Use /rfq/{id}/dispatch to send to suppliers.",
    }


# ── POST /rfq/{id}/dispatch ───────────────────────────────────────────────────

@router.post("/{rfq_id}/dispatch")
async def dispatch_rfq(rfq_id: str):
    """Step 2 — Dispatch RFQ to suppliers."""
    ctx = _contexts.get(rfq_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="RFQ not found")

    from src.integrations.airtable import get_airtable_client
    airtable = get_airtable_client()
    suppliers = (
        airtable.get_all_active_suppliers() if airtable
        else []
    )
    if not suppliers:
        raise HTTPException(status_code=503, detail="No suppliers available (check Airtable config)")

    orchestrator = _get_orchestrator()
    ctx = await orchestrator.dispatch_to_suppliers(ctx, suppliers)
    _contexts[rfq_id] = ctx

    return {
        "rfq_id": rfq_id,
        "status": ctx.rfq.status.value,
        "stage": ctx.stage.value,
        "errors": ctx.errors,
    }


# ── GET /rfq/{id} ─────────────────────────────────────────────────────────────

@router.get("/{rfq_id}")
async def get_rfq(rfq_id: str):
    ctx = _contexts.get(rfq_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="RFQ not found")
    return {
        "rfq_id": rfq_id,
        "status": ctx.rfq.status.value,
        "stage": ctx.stage.value,
        "line_items": len(ctx.rfq.line_items),
        "supplier_responses": len(ctx.supplier_quotes),
        "has_quote": ctx.current_quote is not None,
        "negotiation_rounds": ctx.rfq.negotiation_rounds,
        "errors": ctx.errors,
    }


# ── POST /rfq/{id}/quote ──────────────────────────────────────────────────────

class QuoteRequest(BaseModel):
    freight_usd: Optional[float] = None
    freight_origin_port: Optional[str] = None
    freight_destination_port: Optional[str] = None
    freight_container_size: Optional[str] = "20ft"
    freight_logistics_partner: Optional[str] = None


@router.post("/{rfq_id}/quote")
async def generate_quote(rfq_id: str, body: QuoteRequest):
    """Step 5 — Generate buyer quote from collected supplier responses."""
    ctx = _contexts.get(rfq_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if not ctx.supplier_quotes:
        raise HTTPException(status_code=400, detail="No supplier responses received yet")

    orchestrator = _get_orchestrator()
    ctx = orchestrator.build_matrix(ctx)

    freight = None
    if body.freight_usd:
        from src.freight.footnote import build_freight_footnote
        freight = build_freight_footnote(
            origin_port=body.freight_origin_port or ctx.rfq.origin_port,
            destination_port=body.freight_destination_port or ctx.rfq.destination_port,
            container_size=body.freight_container_size,
            freight_usd=body.freight_usd,
            logistics_partner=body.freight_logistics_partner,
        )

    ctx = orchestrator.generate_quote(ctx, freight=freight)
    _contexts[rfq_id] = ctx

    if ctx.errors:
        raise HTTPException(status_code=422, detail=ctx.errors)

    return ctx.current_quote.to_dict()


# ── POST /rfq/{id}/negotiate ──────────────────────────────────────────────────

class NegotiationRequest(BaseModel):
    target_rate: float
    currency: str = "USD"


@router.post("/{rfq_id}/negotiate")
async def negotiate(rfq_id: str, body: NegotiationRequest):
    """Step 6 — Submit buyer counter-offer rate."""
    ctx = _contexts.get(rfq_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="RFQ not found")

    orchestrator = _get_orchestrator()
    ctx, result = orchestrator.handle_negotiation(
        ctx,
        buyer_target_rate=body.target_rate,
        buyer_target_currency=body.currency,
    )
    _contexts[rfq_id] = ctx

    response = {
        "rfq_id": rfq_id,
        "round": result.round_number,
        "status": result.status.value,
        "message": result.message,
        "buyer_target_usd": result.buyer_target_usd,
        "minimum_achievable_usd": result.minimum_achievable_usd,
    }
    if result.status.value == "feasible" and ctx.current_quote:
        response["revised_quote"] = ctx.current_quote.to_dict()

    return response
