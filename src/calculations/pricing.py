"""
Pricing engine — Steps 5 & 7B/7C.

Responsibilities:
  - Apply 3% markup over best supplier price
  - Calculate rate per CBM in all currencies
  - Build per-line-item priced output rows
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .fx import FXRates, get_fx_rates
from .units import cbm_for_quantity

DEFAULT_MARKUP = 0.03  # 3%


@dataclass
class PricedLineItem:
    """Internal representation of a line item with pricing applied."""
    rfq_line_item_id: str
    supplier_id: str
    total_cbm: float

    # Supplier's price (USD/CBM, pre-markup)
    supplier_price_usd_per_cbm: float

    # Buyer price (USD/CBM, post-markup)
    buyer_price_usd_per_cbm: float
    markup_pct: float

    # Multi-currency totals
    total_usd: float
    total_inr: float
    total_idr: float

    # Rate per m³ in all currencies
    rate_per_cbm_usd: float
    rate_per_cbm_inr: float
    rate_per_cbm_idr: float


@dataclass
class BuyerQuoteRow:
    """One row in the buyer-facing quote grid."""
    line_item_id: str
    product: str
    wood_species: str
    quality_grade: str
    dimensions_display: str       # e.g. "240 × 120 × 18 cm"
    cbm: float
    quantity: float
    quantity_unit: str

    rate_per_cbm_usd: float
    rate_per_cbm_inr: float
    rate_per_cbm_idr: float

    total_usd: float
    total_inr: float
    total_idr: float

    media_urls: list[str] = field(default_factory=list)
    notes: Optional[str] = None


def apply_markup(price_usd_per_cbm: float, markup_pct: float = DEFAULT_MARKUP) -> float:
    """Return price with markup applied: price × (1 + markup_pct)."""
    return price_usd_per_cbm * (1 + markup_pct)


def calculate_rate_per_cbm(
    total_price_usd: float,
    total_cbm: float,
) -> float:
    """Rate/m³ (USD) = Total USD Price ÷ Total CBM."""
    if total_cbm <= 0:
        raise ValueError("total_cbm must be > 0")
    return total_price_usd / total_cbm


def price_line_item(
    rfq_line_item_id: str,
    supplier_id: str,
    supplier_price_usd_per_cbm: float,
    total_cbm: float,
    markup_pct: float = DEFAULT_MARKUP,
    fx: Optional[FXRates] = None,
) -> PricedLineItem:
    """
    Apply markup and compute multi-currency totals for one line item.
    """
    if fx is None:
        fx = get_fx_rates()

    buyer_rate_usd = apply_markup(supplier_price_usd_per_cbm, markup_pct)
    total_usd = buyer_rate_usd * total_cbm

    mc = fx.multi_currency(total_usd)
    mc_rate = fx.multi_currency(buyer_rate_usd)

    return PricedLineItem(
        rfq_line_item_id=rfq_line_item_id,
        supplier_id=supplier_id,
        total_cbm=total_cbm,
        supplier_price_usd_per_cbm=supplier_price_usd_per_cbm,
        buyer_price_usd_per_cbm=buyer_rate_usd,
        markup_pct=markup_pct,
        total_usd=mc["USD"],
        total_inr=mc["INR"],
        total_idr=mc["IDR"],
        rate_per_cbm_usd=mc_rate["USD"],
        rate_per_cbm_inr=mc_rate["INR"],
        rate_per_cbm_idr=mc_rate["IDR"],
    )


def select_best_supplier_per_line(
    line_item_id: str,
    supplier_responses: list[dict],  # [{supplier_id, price_usd_per_cbm, ...}]
) -> Optional[dict]:
    """
    Return the supplier with the lowest price_usd_per_cbm for the given line item.
    supplier_responses: list of dicts with keys: supplier_id, price_usd_per_cbm, ...
    """
    valid = [r for r in supplier_responses if r.get("price_usd_per_cbm") is not None]
    if not valid:
        return None
    return min(valid, key=lambda r: r["price_usd_per_cbm"])


def check_margin_feasibility(
    buyer_target_usd_per_cbm: float,
    best_supplier_usd_per_cbm: float,
    markup_pct: float = DEFAULT_MARKUP,
) -> tuple[bool, float]:
    """
    Check if buyer's target rate is >= best supplier + minimum markup.

    Returns (feasible: bool, minimum_achievable_rate: float).
    """
    minimum = apply_markup(best_supplier_usd_per_cbm, markup_pct)
    return buyer_target_usd_per_cbm >= minimum, minimum
