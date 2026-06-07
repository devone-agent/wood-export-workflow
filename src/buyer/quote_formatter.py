"""
Step 5 — Format the buyer-facing quote.

Rules:
  - Supplier identity hidden
  - 3% markup applied
  - Multi-currency output (USD, INR, IDR)
  - Freight shown as footnote only — never in unit price
  - Media (photos/videos) attached per line item
  - Grid format: Product | Spec | CBM | Rate/m³ | Total (per line item)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.calculations.pricing import PricedLineItem
from src.calculations.fx import FXRates, get_fx_rates
from src.models.rfq import RFQ, LineItem
from src.freight.footnote import FreightFootnote


@dataclass
class QuoteGridRow:
    """One row in the buyer-facing quote grid."""
    line_num: int
    product: str
    wood_species: str
    quality_grade: str
    dimensions_cm: str        # "240 × 120 × 18 cm"
    dimensions_ft: str        # "7.9 × 3.9 × 0.6 ft"
    cbm_per_unit: float
    quantity: float
    quantity_unit: str
    total_cbm: float
    container_size: str

    # Rates (post-markup, supplier identity hidden)
    rate_per_cbm_usd: float
    rate_per_cbm_inr: float
    rate_per_cbm_idr: float

    # Totals
    total_usd: float
    total_inr: float
    total_idr: float

    # Media links (photos/videos of the stock)
    media_urls: list[str] = field(default_factory=list)
    notes: Optional[str] = None


@dataclass
class BuyerQuote:
    rfq_id: str
    quote_ref: str            # short human-readable ref e.g. "QT-A1B2C3D4"
    generated_at: datetime
    buyer_name: str
    destination: str

    rows: list[QuoteGridRow] = field(default_factory=list)

    # Summary totals
    grand_total_usd: float = 0.0
    grand_total_inr: float = 0.0
    grand_total_idr: float = 0.0
    total_cbm: float = 0.0

    # Freight footnote (separate — never in unit price)
    freight_footnote: Optional[str] = None

    validity_days: int = 7

    def to_text(self) -> str:
        """Plain-text representation for WhatsApp/email dispatch."""
        lines = [
            f"QUOTATION — Ref: {self.quote_ref}",
            f"Date: {self.generated_at.strftime('%d %b %Y')}",
            f"Buyer: {self.buyer_name}",
            f"Destination: {self.destination}",
            f"Valid for: {self.validity_days} days",
            "",
            f"{'#':<3} {'Product':<22} {'Spec':<18} {'Qty':>6} {'CBM':>7} "
            f"{'Rate/m³ USD':>12} {'Total USD':>11}",
            "─" * 85,
        ]
        for row in self.rows:
            lines.append(
                f"{row.line_num:<3} "
                f"{row.product[:22]:<22} "
                f"{row.wood_species[:18]:<18} "
                f"{row.quantity:>6.0f} "
                f"{row.total_cbm:>7.3f} "
                f"${row.rate_per_cbm_usd:>11,.2f} "
                f"${row.total_usd:>10,.2f}"
            )
        lines += [
            "─" * 85,
            f"{'TOTAL':>52} "
            f"{self.total_cbm:>7.3f}              "
            f"${self.grand_total_usd:>10,.2f}",
            "",
            "Multi-currency totals:",
            f"  USD: ${self.grand_total_usd:,.2f}",
            f"  INR: ₹{self.grand_total_inr:,.0f}",
            f"  IDR: Rp {self.grand_total_idr:,.0f}",
        ]
        if self.freight_footnote:
            lines += ["", "─" * 85, f"* FREIGHT: {self.freight_footnote}"]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "quote_ref": self.quote_ref,
            "rfq_id": self.rfq_id,
            "generated_at": self.generated_at.isoformat(),
            "buyer_name": self.buyer_name,
            "destination": self.destination,
            "validity_days": self.validity_days,
            "rows": [vars(r) for r in self.rows],
            "totals": {
                "cbm": self.total_cbm,
                "usd": self.grand_total_usd,
                "inr": self.grand_total_inr,
                "idr": self.grand_total_idr,
            },
            "freight_footnote": self.freight_footnote,
        }


def _ev(x) -> str:
    """
    Get the plain string value from a str Enum member or a plain string.
    Robust to Pydantic v2 round-trips that may deserialize str Enums as
    plain strings (which don't have a `.value` attribute).
    """
    return x.value if hasattr(x, "value") else x


def format_buyer_quote(
    rfq: RFQ,
    priced_items: list[PricedLineItem],  # one per line item
    media_by_line: dict[str, list[str]],  # line_item_id → [url, ...]
    freight_footnote: Optional[FreightFootnote] = None,
    fx: Optional[FXRates] = None,
) -> BuyerQuote:
    """
    Assemble the final buyer quote from priced line items.
    Supplier identity is never included in the output.
    """
    if fx is None:
        fx = get_fx_rates()

    import hashlib
    quote_ref = "QT-" + hashlib.md5(rfq.id.encode()).hexdigest()[:8].upper()
    destination = rfq.destination_city or rfq.destination_port or rfq.destination_country

    rows: list[QuoteGridRow] = []
    for i, (line_item, priced) in enumerate(
        zip(rfq.line_items, priced_items), start=1
    ):
        dims_m = line_item.dimensions.to_meters()
        dims_cm = line_item.dimensions  # keep original unit for display

        def _fmt_dim(l, w, h, u): return f"{l:.0f} × {w:.0f} × {h:.0f} {u}"

        dims_cm_str = _fmt_dim(
            dims_cm.length, dims_cm.width, dims_cm.height, _ev(dims_cm.unit)
        )
        dims_ft_str = _fmt_dim(
            dims_m.length / 0.3048, dims_m.width / 0.3048, dims_m.height / 0.3048, "ft"
        )

        rows.append(QuoteGridRow(
            line_num=i,
            product=_ev(line_item.product_type).replace("_", " ").title(),
            wood_species=_ev(line_item.wood_species).replace("_", " ").title(),
            quality_grade=_ev(line_item.quality_grade),
            dimensions_cm=dims_cm_str,
            dimensions_ft=dims_ft_str,
            cbm_per_unit=round(line_item.dimensions.cbm, 6),
            quantity=line_item.quantity,
            quantity_unit=_ev(line_item.quantity_unit),
            total_cbm=round(priced.total_cbm, 4),
            container_size=_ev(line_item.container_size),
            rate_per_cbm_usd=priced.rate_per_cbm_usd,
            rate_per_cbm_inr=priced.rate_per_cbm_inr,
            rate_per_cbm_idr=priced.rate_per_cbm_idr,
            total_usd=priced.total_usd,
            total_inr=priced.total_inr,
            total_idr=priced.total_idr,
            media_urls=media_by_line.get(line_item.id, []),
        ))

    grand_usd = sum(r.total_usd for r in rows)
    grand_mc = fx.multi_currency(grand_usd)

    quote = BuyerQuote(
        rfq_id=rfq.id,
        quote_ref=quote_ref,
        generated_at=datetime.utcnow(),
        buyer_name=rfq.buyer_name,
        destination=destination,
        rows=rows,
        grand_total_usd=grand_mc["USD"],
        grand_total_inr=grand_mc["INR"],
        grand_total_idr=grand_mc["IDR"],
        total_cbm=sum(r.total_cbm for r in rows),
        freight_footnote=freight_footnote.to_string() if freight_footnote else None,
    )

    return quote
