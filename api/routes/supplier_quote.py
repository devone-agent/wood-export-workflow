"""
Supplier quote submission routes.

GET  /supplier-quote/{rfq_id}/{token}        — serve the response form (HTML)
GET  /supplier-quote/{rfq_id}/{token}/data   — RFQ data as JSON (consumed by the form)
POST /supplier-quote/{rfq_id}/{token}        — receive structured quote submission
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.supplier.token import verify_token
from src.store import get_context_store

logger = logging.getLogger(__name__)
router = APIRouter()

_FORM_PATH = Path(__file__).parent.parent.parent / "supplier_response_form.html"


# ── Serve the form ─────────────────────────────────────────────────────────────

@router.get("/{rfq_id}/{token}", include_in_schema=False)
async def serve_supplier_form(rfq_id: str, token: str):
    """Serve the supplier response HTML form."""
    # Light validation — full check happens on POST
    store = get_context_store()
    ctx = store.load(rfq_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="RFQ not found or expired")
    return FileResponse(_FORM_PATH, media_type="text/html")


# ── RFQ data endpoint (called by the form on load) ────────────────────────────

@router.get("/{rfq_id}/{token}/data")
async def get_rfq_data(rfq_id: str, token: str):
    """Return RFQ details for pre-populating the form."""
    store = get_context_store()
    ctx = store.load(rfq_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="RFQ not found or expired")

    rfq = ctx.rfq

    # Find the supplier matching this token
    supplier_id = _resolve_supplier(rfq_id, token, ctx)
    if not supplier_id:
        raise HTTPException(status_code=403, detail="Invalid or expired link")

    lines = []
    for item in rfq.line_items:
        lines.append({
            "line_item_id": item.id,
            "product_type": item.product_type.value.replace("_", " ").title(),
            "wood_species": item.wood_species.value.replace("_", " ").title(),
            "quality_grade": item.quality_grade.value,
            "dimensions": f"{item.dimensions.length} × {item.dimensions.width} × {item.dimensions.height} {item.dimensions.unit.value}",
            "quantity": item.quantity,
            "quantity_unit": item.quantity_unit.value,
            "container_size": item.container_size.value,
            "expected_rate": item.expected_rate,
            "expected_rate_currency": item.expected_rate_currency.value,
        })

    return {
        "rfq_id": rfq_id,
        "rfq_ref": rfq_id[:8].upper(),
        "destination": f"{rfq.destination_country} ({rfq.destination_port or ''})".strip(" ()"),
        "origin_port": rfq.origin_port or "Indonesia",
        "line_items": lines,
    }


# ── Submission model ──────────────────────────────────────────────────────────

class LineItemQuote(BaseModel):
    line_item_id: str
    price_per_unit: float
    price_currency: str = "USD"
    price_unit: str = "cbm"       # cbm | piece | set
    lead_time_days: Optional[int] = None
    quality_notes: Optional[str] = None


class SupplierQuoteSubmission(BaseModel):
    supplier_name: Optional[str] = None
    line_items: list[LineItemQuote]
    notes: Optional[str] = None


# ── Receive submission ────────────────────────────────────────────────────────

@router.post("/{rfq_id}/{token}")
async def submit_supplier_quote(rfq_id: str, token: str, body: SupplierQuoteSubmission):
    """Receive a structured supplier quote and trigger auto-quote if ready."""
    store = get_context_store()
    ctx = store.load(rfq_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="RFQ not found or expired")

    supplier_id = _resolve_supplier(rfq_id, token, ctx)
    if not supplier_id:
        raise HTTPException(status_code=403, detail="Invalid or expired link")

    if not body.line_items:
        raise HTTPException(status_code=422, detail="At least one line item price is required")

    # Build a ParsedResponse directly — no AI parsing needed
    from src.supplier.parser import ParsedResponse, ParsedLineItem

    line_items = [
        ParsedLineItem(
            description=f"Line item {li.line_item_id[:8]}",
            price_per_unit=li.price_per_unit,
            price_currency=li.price_currency,
            price_unit=li.price_unit,
            lead_time_days=li.lead_time_days,
            notes=li.quality_notes,
        )
        for li in body.line_items
    ]

    parsed = ParsedResponse(
        supplier_id=supplier_id,
        rfq_id=rfq_id,
        raw_text=f"Form submission from {body.supplier_name or supplier_id}",
        source="form",
        line_items=line_items,
        parse_confidence=1.0,
        parse_method="form",
    )

    ctx.supplier_quotes.append(parsed)

    from src.models.rfq import RFQStatus
    ctx.rfq.status = RFQStatus.RESPONSES_RECEIVED

    store.save(rfq_id, ctx)
    logger.info(
        "Supplier form submission: rfq=%s supplier=%s items=%d",
        rfq_id, supplier_id, len(line_items),
    )

    # Trigger auto-quote (same logic as IMAP/webhook)
    from api.routes.webhook import _auto_quote_if_ready
    await _auto_quote_if_ready(rfq_id, ctx)

    return {
        "status": "received",
        "rfq_id": rfq_id,
        "items_submitted": len(line_items),
        "message": "Thank you — your quote has been received. We will be in touch shortly.",
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_supplier(rfq_id: str, token: str, ctx) -> Optional[str]:
    """
    Find which supplier this token belongs to by checking all suppliers
    that were dispatched to for this RFQ.
    Uses Airtable to look up active suppliers and verify the token.
    Falls back to accepting any valid-looking token in dev mode.
    """
    try:
        from src.integrations.airtable import get_airtable_client
        from src.supplier.token import verify_token

        at = get_airtable_client()
        if not at:
            # Dev fallback — accept token if it matches rfq_id alone
            from src.supplier.token import make_token
            if verify_token(rfq_id, "dev", token):
                return "dev"
            return None

        suppliers = at.get_all_active_suppliers()
        for s in suppliers:
            if verify_token(rfq_id, s["id"], token):
                return s["id"]

        return None
    except Exception as exc:
        logger.error("_resolve_supplier error: %s", exc)
        return None
