"""
Step 6 — Negotiation loop engine.

Rules:
  1. Receive buyer's revised target rate
  2. Check if target >= best_supplier_price + 3% markup
  3. YES → confirm, generate revised quote
  4. NO  → show minimum achievable rate, optionally re-query suppliers
  5. Max 3 negotiation rounds; flag if exceeded
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from src.calculations.fx import get_fx_rates, FXRates
from src.calculations.pricing import apply_markup, check_margin_feasibility, DEFAULT_MARKUP
from src.models.negotiation import NegotiationRound, NegotiationStatus
from src.models.rfq import RFQ, Currency

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3


@dataclass
class NegotiationResult:
    rfq_id: str
    round_number: int
    status: NegotiationStatus
    buyer_target_usd: float
    minimum_achievable_usd: float
    gap_usd: float  # buyer_target - minimum (negative = infeasible)
    message: str  # human-readable outcome message
    # Multi-currency minimum achievable rate
    minimum_inr: Optional[float] = None
    minimum_idr: Optional[float] = None
    revised_quote: Optional[dict] = None  # populated by orchestrator if feasible


class NegotiationEngine:
    def __init__(self, markup_pct: float = DEFAULT_MARKUP):
        self.markup_pct = markup_pct

    def evaluate(
        self,
        rfq: RFQ,
        buyer_target_rate: float,
        buyer_target_currency: str,
        best_supplier_price_usd_per_cbm: float,
        best_supplier_id: Optional[str] = None,
        fx: Optional[FXRates] = None,
    ) -> NegotiationResult:
        """
        Evaluate whether the buyer's counter-offer is feasible.

        Returns a NegotiationResult with status and messaging.
        """
        if fx is None:
            fx = get_fx_rates()

        # Normalise buyer target to USD
        buyer_target_usd = fx.to_usd(buyer_target_rate, buyer_target_currency)

        # Check round limit first
        if rfq.negotiation_rounds >= MAX_ROUNDS:
            return NegotiationResult(
                rfq_id=rfq.id,
                round_number=rfq.negotiation_rounds + 1,
                status=NegotiationStatus.ROUND_LIMIT_REACHED,
                buyer_target_usd=buyer_target_usd,
                minimum_achievable_usd=apply_markup(best_supplier_price_usd_per_cbm, self.markup_pct),
                gap_usd=0,
                message=(
                    f"Maximum negotiation rounds ({MAX_ROUNDS}) reached. "
                    "Please contact us directly to discuss further."
                ),
            )

        feasible, minimum_usd = check_margin_feasibility(
            buyer_target_usd_per_cbm=buyer_target_usd,
            best_supplier_usd_per_cbm=best_supplier_price_usd_per_cbm,
            markup_pct=self.markup_pct,
        )

        gap = buyer_target_usd - minimum_usd
        mc = fx.multi_currency(minimum_usd)

        if feasible:
            status = NegotiationStatus.FEASIBLE
            message = (
                f"Target rate of {buyer_target_rate:.2f} {buyer_target_currency}/CBM is accepted. "
                f"Generating revised quote."
            )
        else:
            status = NegotiationStatus.INFEASIBLE
            message = (
                f"Target rate of {buyer_target_rate:.2f} {buyer_target_currency}/CBM "
                f"is below our minimum achievable rate of "
                f"USD {minimum_usd:.2f} / INR {mc['INR']:,.0f} / IDR {mc['IDR']:,.0f} per CBM. "
                f"We are {abs(gap):.2f} USD/CBM apart. "
                f"We can re-approach suppliers for a lower bid if you wish to proceed."
            )

        logger.info(
            "Negotiation round %d for RFQ %s: %s (target=%.2f USD, min=%.2f USD)",
            rfq.negotiation_rounds + 1, rfq.id,
            status.value if hasattr(status, "value") else status,
            buyer_target_usd, minimum_usd,
        )

        return NegotiationResult(
            rfq_id=rfq.id,
            round_number=rfq.negotiation_rounds + 1,
            status=status,
            buyer_target_usd=buyer_target_usd,
            minimum_achievable_usd=minimum_usd,
            gap_usd=gap,
            minimum_inr=mc["INR"],
            minimum_idr=mc["IDR"],
            message=message,
        )

    def record_round(
        self,
        rfq: RFQ,
        result: NegotiationResult,
        best_supplier_id: Optional[str] = None,
    ) -> NegotiationRound:
        """Persist the negotiation round and increment rfq counter."""
        rfq.negotiation_rounds += 1

        return NegotiationRound(
            rfq_id=rfq.id,
            round_number=result.round_number,
            buyer_target_rate=result.buyer_target_usd,
            buyer_target_currency=Currency.USD,
            buyer_target_rate_usd=result.buyer_target_usd,
            minimum_achievable_usd=result.minimum_achievable_usd,
            best_supplier_id=best_supplier_id,
            status=result.status,
            notes=result.message,
        )
