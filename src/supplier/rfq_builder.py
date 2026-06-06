"""
Step 2 — Build structured RFQ payloads for suppliers.

Converts the buyer's RFQ into per-supplier messages with:
  - dimensions in the supplier's preferred unit
  - rates/prices in the supplier's preferred currency
  - formatted for both email body and WhatsApp template
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.calculations.units import format_dimensions_all_units
from src.calculations.fx import get_fx_rates
from src.models.rfq import RFQ, LineItem, UnitType, Currency


@dataclass
class RFQLinePayload:
    line_item_id: str
    product_type: str
    wood_species: str
    quality_grade: str
    # Dimensions in supplier's preferred unit
    length: float
    width: float
    height: float
    unit: str
    quantity: float
    quantity_unit: str
    container_size: str
    # Buyer's expected rate converted to supplier's preferred currency (optional hint)
    expected_rate: Optional[float]
    expected_rate_currency: str


@dataclass
class SupplierRFQPayload:
    rfq_id: str
    supplier_id: str
    supplier_name: str
    supplier_email: Optional[str]
    supplier_whatsapp: Optional[str]
    preferred_unit: str
    preferred_currency: str
    lines: list[RFQLinePayload] = field(default_factory=list)

    # Ready-to-send formatted text
    email_subject: str = ""
    email_body: str = ""
    whatsapp_message: str = ""


def build_supplier_rfq(rfq: RFQ, supplier: dict) -> SupplierRFQPayload:
    """
    Build a SupplierRFQPayload tailored to one supplier's preferences.

    supplier dict keys: id, name, email, whatsapp,
                        preferred_unit, preferred_currency
    """
    fx = get_fx_rates()
    pref_unit = UnitType(supplier.get("preferred_unit", "cm"))
    pref_currency = supplier.get("preferred_currency", "USD")

    lines: list[RFQLinePayload] = []
    for item in rfq.line_items:
        dims_all = format_dimensions_all_units(item.dimensions)
        unit_dims = dims_all.get(pref_unit.value, dims_all["cm"])

        # Convert expected rate to supplier's preferred currency
        exp_rate: Optional[float] = None
        if item.expected_rate is not None:
            rate_usd = fx.to_usd(item.expected_rate, item.expected_rate_currency.value)
            exp_rate = round(fx.convert(rate_usd, pref_currency), 2)

        lines.append(RFQLinePayload(
            line_item_id=item.id,
            product_type=item.product_type.value,
            wood_species=item.wood_species.value,
            quality_grade=item.quality_grade.value,
            length=round(unit_dims["L"], 2),
            width=round(unit_dims["W"], 2),
            height=round(unit_dims["H"], 2),
            unit=pref_unit.value,
            quantity=item.quantity,
            quantity_unit=item.quantity_unit.value,
            container_size=item.container_size.value,
            expected_rate=exp_rate,
            expected_rate_currency=pref_currency,
        ))

    payload = SupplierRFQPayload(
        rfq_id=rfq.id,
        supplier_id=supplier["id"],
        supplier_name=supplier["name"],
        supplier_email=supplier.get("email"),
        supplier_whatsapp=supplier.get("whatsapp"),
        preferred_unit=pref_unit.value,
        preferred_currency=pref_currency,
        lines=lines,
    )

    payload.email_subject = _format_email_subject(rfq, payload)
    payload.email_body = _format_email_body(rfq, payload)
    payload.whatsapp_message = _format_whatsapp_message(rfq, payload)

    return payload


def _format_email_subject(rfq: RFQ, payload: SupplierRFQPayload) -> str:
    species = {ln.wood_species for ln in payload.lines}
    return (
        f"RFQ #{rfq.id[:8].upper()} — Wood Supply Request "
        f"({', '.join(species)}) | {rfq.destination_country}"
    )


def _format_email_body(rfq: RFQ, payload: SupplierRFQPayload) -> str:
    lines_text = ""
    for i, ln in enumerate(payload.lines, 1):
        rate_hint = (
            f" | Target: {ln.expected_rate} {ln.expected_rate_currency}/CBM"
            if ln.expected_rate else ""
        )
        lines_text += (
            f"\n  {i}. {ln.product_type.replace('_',' ').title()} — "
            f"{ln.wood_species.replace('_',' ').title()} Grade {ln.quality_grade}\n"
            f"     Dimensions: {ln.length} × {ln.width} × {ln.height} {ln.unit}\n"
            f"     Quantity: {ln.quantity} {ln.quantity_unit} | Container: {ln.container_size}"
            f"{rate_hint}\n"
        )

    return f"""Dear {payload.supplier_name},

We are requesting a quotation for the following items for export to {rfq.destination_country}
(Port of Loading: {rfq.origin_port or 'Indonesia'} → Port of Discharge: {rfq.destination_port or rfq.destination_city or rfq.destination_country}).

ITEMS REQUESTED:
{lines_text}

Please provide:
  • Unit price per CBM in {payload.preferred_currency}
  • Lead time in days
  • Photos and/or video of available stock
  • Any grade/quality notes

Please reply to this email or WhatsApp with your best rates at your earliest convenience.

Regards,
Wood Export Team
RFQ Reference: {rfq.id}
"""


def _format_whatsapp_message(rfq: RFQ, payload: SupplierRFQPayload) -> str:
    lines_text = ""
    for i, ln in enumerate(payload.lines, 1):
        lines_text += (
            f"\n{i}. {ln.wood_species.replace('_',' ').upper()} {ln.quality_grade} | "
            f"{ln.product_type.replace('_',' ').title()}\n"
            f"   {ln.length}×{ln.width}×{ln.height} {ln.unit} | "
            f"Qty: {ln.quantity} {ln.quantity_unit} | {ln.container_size}"
        )
        if ln.expected_rate:
            lines_text += f"\n   Target: {ln.expected_rate} {ln.expected_rate_currency}/CBM"

    return (
        f"*RFQ #{rfq.id[:8].upper()}* — Wood Supply\n"
        f"Destination: {rfq.destination_country}\n"
        f"---{lines_text}\n---\n"
        f"Please send: ✅ Rate/CBM in {payload.preferred_currency} "
        f"✅ Lead time ✅ Photos/video\n"
        f"Thank you!"
    )
