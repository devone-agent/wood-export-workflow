"""
Step 4 — Build a comparison matrix from all supplier responses.

Produces:
  - Per-line-item rate table (sorted cheapest → most expensive)
  - Best supplier per line item
  - Aggregated comparison with quality score weighting
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.calculations.fx import get_fx_rates, FXRates
from src.models.supplier import SupplierQuote, RFQLineResponse


@dataclass
class LineItemComparison:
    """All supplier options for a single RFQ line item, sorted by price."""
    rfq_line_item_id: str
    options: list[dict] = field(default_factory=list)
    # Each option: {supplier_id, supplier_name, price_usd_per_cbm,
    #               price_inr_per_cbm, price_idr_per_cbm,
    #               quality_grade, rating, media_count, lead_time_days}

    @property
    def best_option(self) -> Optional[dict]:
        """Cheapest option (primary) with quality as tiebreaker."""
        if not self.options:
            return None
        return self.options[0]  # already sorted by price

    @property
    def cheapest_price_usd(self) -> Optional[float]:
        return self.best_option["price_usd_per_cbm"] if self.best_option else None


@dataclass
class ComparisonMatrix:
    rfq_id: str
    line_comparisons: dict[str, LineItemComparison] = field(default_factory=dict)
    # line_item_id → LineItemComparison

    def best_for_line(self, line_item_id: str) -> Optional[dict]:
        comp = self.line_comparisons.get(line_item_id)
        return comp.best_option if comp else None

    def to_display_table(self) -> list[dict]:
        """Flat list suitable for API response / Excel export."""
        rows = []
        for line_id, comp in self.line_comparisons.items():
            for rank, opt in enumerate(comp.options, 1):
                rows.append({
                    "line_item_id": line_id,
                    "rank": rank,
                    **opt,
                })
        return rows


def build_comparison_matrix(
    rfq_id: str,
    supplier_quotes: list[SupplierQuote],
    supplier_ratings: dict[str, float],  # supplier_id → rating (1–10)
    fx: Optional[FXRates] = None,
) -> ComparisonMatrix:
    """
    Aggregate all supplier quotes into a comparison matrix.

    supplier_ratings: optional dict of historical quality ratings per supplier.
    """
    if fx is None:
        fx = get_fx_rates()

    # Collect all responses grouped by line item
    by_line: dict[str, list[dict]] = {}

    for quote in supplier_quotes:
        for resp in quote.line_responses:
            if resp.price_usd_per_cbm is None:
                continue

            rating = supplier_ratings.get(quote.supplier_id, 5.0)
            entry = {
                "supplier_id": quote.supplier_id,
                "supplier_name": quote.supplier_name,
                "price_usd_per_cbm": resp.price_usd_per_cbm,
                "price_inr_per_cbm": round(fx.convert(resp.price_usd_per_cbm, "INR"), 2),
                "price_idr_per_cbm": round(fx.convert(resp.price_usd_per_cbm, "IDR"), 0),
                "quality_grade": resp.quality_grade,
                "quality_notes": resp.quality_notes,
                "rating": rating,
                "media_count": len(resp.media_items),
                "lead_time_days": resp.lead_time_days,
                "response_id": resp.id,
            }

            by_line.setdefault(resp.rfq_line_item_id, []).append(entry)

    # Sort each line's options cheapest → most expensive, rating as tiebreaker
    matrix = ComparisonMatrix(rfq_id=rfq_id)
    for line_id, options in by_line.items():
        sorted_options = sorted(
            options,
            key=lambda o: (o["price_usd_per_cbm"], -o["rating"]),
        )
        matrix.line_comparisons[line_id] = LineItemComparison(
            rfq_line_item_id=line_id,
            options=sorted_options,
        )

    return matrix


def normalise_response_price(
    resp: RFQLineResponse,
    fx: Optional[FXRates] = None,
) -> float:
    """
    Convert a supplier's quoted price to USD/CBM regardless of
    how they quoted it (per piece, per set, etc.).

    Mutates resp.price_usd_per_cbm in place and returns the value.
    """
    if fx is None:
        fx = get_fx_rates()

    price_usd = fx.to_usd(resp.price_per_unit, resp.price_currency.value)

    # If quoted per piece/set, we need the line item's CBM to normalise —
    # caller must set resp.price_usd_per_cbm themselves for non-CBM units.
    # Here we just handle the direct CBM case.
    if resp.price_unit == "cbm":
        resp.price_usd_per_cbm = round(price_usd, 4)

    return resp.price_usd_per_cbm or price_usd
