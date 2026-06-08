"""
Main workflow orchestrator.

Ties together all 8 steps of the wood export bot workflow:

  Step 1  →  Receive & validate buyer form
  Step 2  →  Dispatch RFQs to suppliers (email + WhatsApp)
  Step 3  →  Collect & parse supplier responses
  Step 4  →  Build comparison matrix
  Step 5  →  Generate buyer quote (best price + 3% markup)
  Step 6  →  Handle negotiation rounds (max 3)
  Step 7  →  Calculations engine (units, FX, pricing) — called inline
  Step 8  →  Freight footnote — appended to quote

State is persisted to Airtable. All integration clients are injected
so the orchestrator can be unit-tested with mocks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.buyer.form import BuyerFormInput, form_to_rfq, validate_buyer_form
from src.buyer.quote_formatter import format_buyer_quote, BuyerQuote
from src.calculations.fx import get_fx_rates
from src.calculations.pricing import price_line_item
from src.freight.footnote import FreightFootnote
from src.models.rfq import RFQ, RFQStatus
from src.negotiation.engine import NegotiationEngine, NegotiationResult
from src.models.negotiation import NegotiationStatus
from src.supplier.comparator import build_comparison_matrix, normalise_response_price
from src.supplier.dispatcher import dispatch_rfq_to_suppliers, DispatchChannel
from src.supplier.rfq_builder import build_supplier_rfq

logger = logging.getLogger(__name__)


class WorkflowStage(str, Enum):
    CREATED = "created"
    DISPATCHED = "dispatched"
    AWAITING_RESPONSES = "awaiting_responses"
    ANALYSED = "analysed"
    QUOTED = "quoted"
    NEGOTIATING = "negotiating"
    ACCEPTED = "accepted"
    CLOSED = "closed"


@dataclass
class WorkflowContext:
    rfq: RFQ
    stage: WorkflowStage = WorkflowStage.CREATED
    supplier_quotes: list = field(default_factory=list)
    comparison_matrix: Optional[object] = None
    current_quote: Optional[BuyerQuote] = None
    negotiation_rounds: list = field(default_factory=list)
    freight: Optional[FreightFootnote] = None
    errors: list[str] = field(default_factory=list)


class WorkflowOrchestrator:
    def __init__(
        self,
        email_client=None,
        whatsapp_client=None,
        airtable_client=None,
        anthropic_client=None,
        dispatch_channel: DispatchChannel = DispatchChannel.BOTH,
        markup_pct: float = 0.03,
    ):
        self.email_client = email_client
        self.whatsapp_client = whatsapp_client
        self.airtable_client = airtable_client
        self.anthropic_client = anthropic_client
        self.dispatch_channel = dispatch_channel
        self.negotiation_engine = NegotiationEngine(markup_pct=markup_pct)

    # ── STEP 1 ─────────────────────────────────────────────────────────────────

    def create_rfq_from_form(self, raw_form: dict) -> tuple[Optional[WorkflowContext], list[str]]:
        """Validate buyer form and create a workflow context."""
        form, errors = validate_buyer_form(raw_form)
        if errors:
            return None, errors
        rfq = form_to_rfq(form)
        logger.info("RFQ created: %s (%d line items)", rfq.id, len(rfq.line_items))
        return WorkflowContext(rfq=rfq), []

    # ── STEP 2 ─────────────────────────────────────────────────────────────────

    async def dispatch_to_suppliers(
        self, ctx: WorkflowContext, suppliers: list[dict]
    ) -> WorkflowContext:
        """Build per-supplier RFQ payloads and dispatch them."""
        payloads = [build_supplier_rfq(ctx.rfq, s) for s in suppliers]
        results = await dispatch_rfq_to_suppliers(
            payloads,
            channel=self.dispatch_channel,
            email_client=self.email_client,
            whatsapp_client=self.whatsapp_client,
        )
        failed = [r.supplier_name for r in results if r.all_failed]
        if failed:
            ctx.errors.append(f"Dispatch failed for: {', '.join(failed)}")

        ctx.rfq.status = RFQStatus.SENT_TO_SUPPLIERS
        ctx.stage = WorkflowStage.DISPATCHED
        logger.info("Dispatched to %d suppliers (%d failed)", len(results), len(failed))

        # Brief pause so we don't hit Resend's 5 req/sec rate limit
        import asyncio
        await asyncio.sleep(1)

        # Send buyer confirmation + operator notification
        await self._send_buyer_confirmation(ctx)
        await asyncio.sleep(0.3)
        await self._notify_operator_buyer_rfq(ctx)

        return ctx

    async def _send_buyer_confirmation(self, ctx: WorkflowContext) -> None:
        """Email the buyer confirming their RFQ has been received and sent to suppliers."""
        rfq = ctx.rfq
        if not rfq.buyer_email or not self.email_client:
            return
        n = len(rfq.line_items)
        body = (
            f"Dear {rfq.buyer_name},\n\n"
            f"Thank you for your enquiry. We have received your request for quotation "
            f"({n} line item{'s' if n != 1 else ''}) and our suppliers are being contacted now.\n\n"
            f"RFQ Reference: {rfq.id}\n"
            f"Destination:   {rfq.destination_country} ({rfq.destination_port})\n"
            f"Origin:        {rfq.origin_port}\n\n"
            f"We will come back to you with a formal quotation as soon as supplier "
            f"responses are collected — typically within 24–48 hours.\n\n"
            f"If you have any questions in the meantime, just reply to this email.\n\n"
            f"Kind regards,\n"
            f"Wood Export Team"
        )
        try:
            await self.email_client.send(
                to=rfq.buyer_email,
                to_name=rfq.buyer_name,
                subject=f"RFQ Received — {rfq.id[:8].upper()} | {rfq.destination_country}",
                body=body,
            )
            logger.info("Buyer confirmation sent to %s", rfq.buyer_email)
        except Exception as exc:
            logger.warning("Could not send buyer confirmation: %s", exc)

    async def _notify_operator_buyer_rfq(self, ctx: WorkflowContext) -> None:
        """Notify operator when a buyer submits an RFQ. Operator sees buyer details; suppliers are never mentioned."""
        import os
        operator_email = os.getenv("OPERATOR_EMAIL")
        if not operator_email or not self.email_client:
            return
        rfq = ctx.rfq
        items_text = "\n".join(
            f"  {i+1}. {li.product_type.value.replace('_',' ').title()} — "
            f"{li.wood_species.value.replace('_',' ').title()} Grade {li.quality_grade.value} | "
            f"Qty: {li.quantity} {li.quantity_unit.value}"
            for i, li in enumerate(rfq.line_items)
        )
        body = (
            f"═══════════════════════════════════\n"
            f"  NEW BUYER RFQ RECEIVED\n"
            f"═══════════════════════════════════\n\n"
            f"RFQ Reference : {rfq.id}\n"
            f"Buyer Name    : {rfq.buyer_name}\n"
            f"Buyer Email   : {rfq.buyer_email}\n"
            f"Buyer Phone   : {rfq.buyer_phone or '—'}\n"
            f"Company       : {rfq.buyer_company or '—'}\n\n"
            f"Destination   : {rfq.destination_country} — {rfq.destination_port or ''}\n"
            f"Origin Port   : {rfq.origin_port or 'Indonesia'}\n\n"
            f"ITEMS REQUESTED:\n{items_text}\n\n"
            f"RFQ dispatched to {len(ctx.rfq.line_items and rfq.line_items)} line item(s).\n"
            f"Suppliers have been contacted and are awaiting their responses.\n\n"
            f"— Wood Export Bot"
        )
        try:
            await self.email_client.send(
                to=operator_email,
                subject=f"[BUYER] New RFQ {rfq.id[:8].upper()} — {rfq.buyer_name} | {rfq.destination_country}",
                body=body,
            )
            logger.info("Operator notified of buyer RFQ %s", rfq.id[:8].upper())
        except Exception as exc:
            logger.warning("Could not send operator buyer notification: %s", exc)

    async def _notify_operator_supplier_quote(
        self,
        ctx: WorkflowContext,
        supplier_name: str,
        supplier_email: str,
        line_items_summary: str,
        notes: str = "",
    ) -> None:
        """Notify operator when a supplier submits a quote. Buyer details are never included."""
        import os
        operator_email = os.getenv("OPERATOR_EMAIL")
        if not operator_email or not self.email_client:
            return
        rfq = ctx.rfq
        body = (
            f"═══════════════════════════════════\n"
            f"  SUPPLIER QUOTE RECEIVED\n"
            f"═══════════════════════════════════\n\n"
            f"RFQ Reference  : {rfq.id}\n"
            f"Supplier Name  : {supplier_name}\n"
            f"Supplier Email : {supplier_email}\n\n"
            f"Destination    : {rfq.destination_country} — {rfq.destination_port or ''}\n"
            f"Origin Port    : {rfq.origin_port or 'Indonesia'}\n\n"
            f"QUOTED PRICES:\n{line_items_summary}\n"
            + (f"\nSupplier Notes : {notes}\n" if notes else "")
            + f"\n— Wood Export Bot"
        )
        try:
            await self.email_client.send(
                to=operator_email,
                subject=f"[SUPPLIER] Quote from {supplier_name} — RFQ {rfq.id[:8].upper()}",
                body=body,
            )
            logger.info("Operator notified of supplier quote from %s", supplier_name)
        except Exception as exc:
            logger.warning("Could not send operator supplier notification: %s", exc)

    # ── STEPS 3 & 4 ────────────────────────────────────────────────────────────

    def ingest_supplier_response(
        self,
        ctx: WorkflowContext,
        raw_text: str,
        supplier_id: str,
        source: str = "whatsapp",
    ) -> WorkflowContext:
        """
        Parse one incoming supplier message and add it to the context.
        Call this each time a supplier response arrives.
        """
        from src.supplier.parser import parse_supplier_response
        parsed = parse_supplier_response(
            raw_text=raw_text,
            supplier_id=supplier_id,
            rfq_id=ctx.rfq.id,
            source=source,
            anthropic_client=self.anthropic_client,
        )
        ctx.supplier_quotes.append(parsed)
        ctx.rfq.status = RFQStatus.RESPONSES_RECEIVED
        return ctx

    def build_matrix(
        self,
        ctx: WorkflowContext,
        supplier_ratings: Optional[dict] = None,
    ) -> WorkflowContext:
        """Build comparison matrix from all collected supplier quotes."""
        from src.models.supplier import SupplierQuote, RFQLineResponse
        supplier_quotes_domain = _parsed_to_domain(ctx.supplier_quotes, ctx.rfq)
        ctx.comparison_matrix = build_comparison_matrix(
            rfq_id=ctx.rfq.id,
            supplier_quotes=supplier_quotes_domain,
            supplier_ratings=supplier_ratings or {},
        )
        ctx.stage = WorkflowStage.ANALYSED
        return ctx

    # ── STEP 5 ─────────────────────────────────────────────────────────────────

    async def generate_quote(
        self,
        ctx: WorkflowContext,
        freight: Optional[FreightFootnote] = None,
    ) -> WorkflowContext:
        """Generate buyer quote using best supplier per line item."""
        if ctx.comparison_matrix is None:
            ctx.errors.append("Cannot generate quote: comparison matrix not built")
            return ctx

        fx = get_fx_rates()
        priced_items = []
        media_by_line: dict[str, list[str]] = {}

        for line_item in ctx.rfq.line_items:
            best = ctx.comparison_matrix.best_for_line(line_item.id)
            if not best:
                ctx.errors.append(f"No supplier response for line item {line_item.id}")
                continue

            priced = price_line_item(
                rfq_line_item_id=line_item.id,
                supplier_id=best["supplier_id"],
                supplier_price_usd_per_cbm=best["price_usd_per_cbm"],
                total_cbm=line_item.total_cbm,
                fx=fx,
            )
            priced_items.append(priced)

            # Collect media (photos/videos) — supplier identity hidden
            all_options = ctx.comparison_matrix.line_comparisons[line_item.id].options
            media_by_line[line_item.id] = [
                url
                for opt in all_options
                for url in opt.get("media_urls", [])
            ]

        ctx.freight = freight
        ctx.current_quote = format_buyer_quote(
            rfq=ctx.rfq,
            priced_items=priced_items,
            media_by_line=media_by_line,
            freight_footnote=freight,
            fx=fx,
        )
        ctx.rfq.status = RFQStatus.QUOTE_SENT
        ctx.stage = WorkflowStage.QUOTED
        logger.info("Quote generated: %s", ctx.current_quote.quote_ref)

        # Email the quote to the buyer
        await self._send_buyer_quote(ctx)

        return ctx

    async def _send_buyer_quote(self, ctx: WorkflowContext) -> None:
        """Email the formatted quotation to the buyer."""
        rfq = ctx.rfq
        quote = ctx.current_quote
        if not rfq.buyer_email or not self.email_client or quote is None:
            return
        body = (
            f"Dear {rfq.buyer_name},\n\n"
            f"Please find your quotation below.\n\n"
            f"{quote.to_text()}\n\n"
            f"This quote is valid for {quote.validity_days} days from today.\n\n"
            f"To discuss pricing further, simply reply to this email or contact us directly.\n\n"
            f"Kind regards,\n"
            f"Wood Export Team"
        )
        try:
            await self.email_client.send(
                to=rfq.buyer_email,
                to_name=rfq.buyer_name,
                subject=f"Quotation {quote.quote_ref} — {rfq.destination_country}",
                body=body,
            )
            logger.info("Quote %s emailed to %s", quote.quote_ref, rfq.buyer_email)
        except Exception as exc:
            logger.warning("Could not email quote to buyer: %s", exc)

    # ── STEP 6 ─────────────────────────────────────────────────────────────────

    def handle_negotiation(
        self,
        ctx: WorkflowContext,
        buyer_target_rate: float,
        buyer_target_currency: str,
    ) -> tuple[WorkflowContext, NegotiationResult]:
        """Process one buyer counter-offer."""
        if not ctx.comparison_matrix:
            raise ValueError("No comparison matrix available for negotiation")

        # Find the overall cheapest supplier rate across all line items
        best_prices = [
            comp.cheapest_price_usd
            for comp in ctx.comparison_matrix.line_comparisons.values()
            if comp.cheapest_price_usd is not None
        ]
        if not best_prices:
            raise ValueError("No supplier prices available")

        # Use the average best price across line items as the floor
        avg_best_price = sum(best_prices) / len(best_prices)

        result = self.negotiation_engine.evaluate(
            rfq=ctx.rfq,
            buyer_target_rate=buyer_target_rate,
            buyer_target_currency=buyer_target_currency,
            best_supplier_price_usd_per_cbm=avg_best_price,
        )

        round_record = self.negotiation_engine.record_round(ctx.rfq, result)
        ctx.negotiation_rounds.append(round_record)
        ctx.rfq.status = RFQStatus.NEGOTIATING
        ctx.stage = WorkflowStage.NEGOTIATING

        # If feasible, regenerate the quote at the buyer's target rate
        # Compare against enum or plain string (robust to Pydantic round-trip)
        if result.status == NegotiationStatus.FEASIBLE:
            ctx = self._regenerate_at_target(ctx, result.buyer_target_usd)

        return ctx, result

    def _regenerate_at_target(
        self, ctx: WorkflowContext, buyer_target_usd_per_cbm: float
    ) -> WorkflowContext:
        """Regenerate quote using buyer's accepted target rate (still hides supplier)."""
        fx = get_fx_rates()
        priced_items = []
        media_by_line: dict[str, list[str]] = {}

        for line_item in ctx.rfq.line_items:
            from src.calculations.pricing import PricedLineItem
            mc = fx.multi_currency(buyer_target_usd_per_cbm * line_item.total_cbm)
            mc_rate = fx.multi_currency(buyer_target_usd_per_cbm)
            best = ctx.comparison_matrix.best_for_line(line_item.id)
            priced = PricedLineItem(
                rfq_line_item_id=line_item.id,
                supplier_id=best["supplier_id"] if best else "unknown",
                total_cbm=line_item.total_cbm,
                supplier_price_usd_per_cbm=best["price_usd_per_cbm"] if best else 0,
                buyer_price_usd_per_cbm=buyer_target_usd_per_cbm,
                markup_pct=0,  # custom rate accepted
                total_usd=mc["USD"],
                total_inr=mc["INR"],
                total_idr=mc["IDR"],
                rate_per_cbm_usd=mc_rate["USD"],
                rate_per_cbm_inr=mc_rate["INR"],
                rate_per_cbm_idr=mc_rate["IDR"],
            )
            priced_items.append(priced)
            if ctx.comparison_matrix:
                all_opts = ctx.comparison_matrix.line_comparisons.get(line_item.id)
                media_by_line[line_item.id] = [
                    url for opt in (all_opts.options if all_opts else [])
                    for url in opt.get("media_urls", [])
                ]

        ctx.current_quote = format_buyer_quote(
            rfq=ctx.rfq,
            priced_items=priced_items,
            media_by_line=media_by_line,
            freight_footnote=ctx.freight,
            fx=fx,
        )
        return ctx


def _parsed_to_domain(parsed_responses: list, rfq: RFQ) -> list:
    """
    Convert ParsedResponse objects to SupplierQuote domain objects
    for use in the comparison matrix.
    This is a bridge between the parser output and the domain model.
    """
    from src.models.supplier import SupplierQuote, RFQLineResponse
    from src.models.rfq import Currency

    quotes = []
    for parsed in parsed_responses:
        line_responses = []
        for i, item in enumerate(parsed.line_items):
            # Match to RFQ line items by position (best effort)
            if i < len(rfq.line_items):
                rfq_line_id = rfq.line_items[i].id
            else:
                rfq_line_id = rfq.line_items[-1].id

            try:
                currency = Currency(item.price_currency.upper())
            except ValueError:
                currency = Currency.USD

            lr = RFQLineResponse(
                rfq_id=rfq.id,
                rfq_line_item_id=rfq_line_id,
                supplier_id=parsed.supplier_id,
                price_per_unit=item.price_per_unit,
                price_currency=currency,
                price_unit=item.price_unit,
                quality_grade=item.quality_grade,
                lead_time_days=item.lead_time_days,
                raw_response=parsed.raw_text,
            )
            # Normalise to USD/CBM immediately
            from src.calculations.fx import get_fx_rates
            fx = get_fx_rates()
            price_usd = fx.to_usd(item.price_per_unit, currency.value)
            if item.price_unit == "cbm":
                lr.price_usd_per_cbm = round(price_usd, 4)
            line_responses.append(lr)

        quotes.append(SupplierQuote(
            rfq_id=rfq.id,
            supplier_id=parsed.supplier_id,
            supplier_name=parsed.supplier_id,  # name resolved by caller if needed
            line_responses=line_responses,
        ))
    return quotes
