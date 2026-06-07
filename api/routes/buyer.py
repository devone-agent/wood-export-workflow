"""
Buyer-facing API routes — Steps 1, 5, 6.
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.buyer.form import validate_buyer_form, form_to_rfq
from src.orchestrator import WorkflowOrchestrator, WorkflowContext
from src.store import get_context_store

router = APIRouter()


def _get_orchestrator() -> WorkflowOrchestrator:
    """Build orchestrator from environment config."""
    import os
    from src.integrations.email import get_email_client
    from src.integrations.whatsapp import WhatsAppClient

    email_client = get_email_client()

    whatsapp_client = None
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

    store = get_context_store()
    store.save(rfq.id, ctx)

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
    store = get_context_store()
    ctx = store.load(rfq_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="RFQ not found")

    from src.integrations.airtable import get_airtable_client
    airtable = get_airtable_client()
    suppliers = airtable.get_all_active_suppliers() if airtable else []
    if not suppliers:
        raise HTTPException(status_code=503, detail="No suppliers available (check Airtable config)")

    orchestrator = _get_orchestrator()
    ctx = await orchestrator.dispatch_to_suppliers(ctx, suppliers)
    store.save(rfq_id, ctx)

    return {
        "rfq_id": rfq_id,
        "status": ctx.rfq.status.value,
        "stage": ctx.stage.value,
        "errors": ctx.errors,
    }


# ── GET /rfq/{id} ─────────────────────────────────────────────────────────────

@router.get("/{rfq_id}")
async def get_rfq(rfq_id: str):
    store = get_context_store()
    ctx = store.load(rfq_id)
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
    store = get_context_store()
    ctx = store.load(rfq_id)
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

    errors_before = set(ctx.errors)
    ctx = await orchestrator.generate_quote(ctx, freight=freight)
    store.save(rfq_id, ctx)

    # Only fail if the quote itself couldn't be built
    if ctx.current_quote is None:
        new_errors = [e for e in ctx.errors if e not in errors_before]
        raise HTTPException(status_code=422, detail=new_errors or ctx.errors)

    return ctx.current_quote.to_dict()


# ── POST /rfq/{id}/negotiate ──────────────────────────────────────────────────

class NegotiationRequest(BaseModel):
    target_rate: float
    currency: str = "USD"


@router.post("/{rfq_id}/negotiate")
async def negotiate(rfq_id: str, body: NegotiationRequest):
    """Step 6 — Submit buyer counter-offer rate."""
    import logging, traceback as _tb
    _log = logging.getLogger(__name__)

    store = get_context_store()
    ctx = store.load(rfq_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="RFQ not found")

    # Rebuild matrix if lost (e.g. after a restart)
    if ctx.comparison_matrix is None and ctx.supplier_quotes:
        orchestrator = _get_orchestrator()
        ctx = orchestrator.build_matrix(ctx)

    if not ctx.comparison_matrix or not ctx.comparison_matrix.line_comparisons:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot negotiate: comparison matrix empty. "
                   f"supplier_quotes={len(ctx.supplier_quotes)}",
        )

    orchestrator = _get_orchestrator()
    try:
        ctx, result = orchestrator.handle_negotiation(
            ctx,
            buyer_target_rate=body.target_rate,
            buyer_target_currency=body.currency,
        )
    except Exception as exc:
        _log.error("handle_negotiation error: %s\n%s", exc, _tb.format_exc())
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")

    store.save(rfq_id, ctx)

    _status_val = result.status.value if hasattr(result.status, "value") else str(result.status)
    response = {
        "rfq_id": rfq_id,
        "round": result.round_number,
        "status": _status_val,
        "message": result.message,
        "buyer_target_usd": result.buyer_target_usd,
        "minimum_achievable_usd": result.minimum_achievable_usd,
    }
    if _status_val == "feasible" and ctx.current_quote:
        response["revised_quote"] = ctx.current_quote.to_dict()

    return response
