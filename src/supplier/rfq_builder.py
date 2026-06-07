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

import os

from src.calculations.units import format_dimensions_all_units
from src.calculations.fx import get_fx_rates
from src.models.rfq import RFQ, LineItem, UnitType, Currency
from src.supplier.token import make_token


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
    email_html: str = ""
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
    payload.email_html = _format_email_body_html(rfq, payload)
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

----------------------------------------------------------
SUBMIT YOUR QUOTE ONLINE (fastest — takes 2 minutes):

{_form_link(rfq.id, payload.supplier_id)}

Click the link above, enter your prices and lead times, and submit.
Alternatively, reply to this email with your rates.
----------------------------------------------------------

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

    form_url = _form_link(rfq.id, payload.supplier_id)
    return (
        f"*RFQ #{rfq.id[:8].upper()}* — Wood Supply\n"
        f"Destination: {rfq.destination_country}\n"
        f"---{lines_text}\n---\n"
        f"Submit your quote here: {form_url}\n"
        f"(or reply with rate/CBM + lead time)\n"
        f"Thank you!"
    )


def _format_email_body_html(rfq: RFQ, payload: SupplierRFQPayload) -> str:
    """HTML version of the supplier RFQ email with a clickable button for the form link."""
    form_url = _form_link(rfq.id, payload.supplier_id)

    rows_html = ""
    for i, ln in enumerate(payload.lines, 1):
        rate_hint = (
            f"<br><span style='color:#c8922a;font-size:12px;'>Buyer target: {ln.expected_rate} {ln.expected_rate_currency}/CBM</span>"
            if ln.expected_rate else ""
        )
        rows_html += f"""
        <tr style='background:{"#faf6f0" if i % 2 == 0 else "#fff"}'>
          <td style='padding:10px 14px;border-bottom:1px solid #e8ddd0;font-weight:700;color:#5c3d1e;'>{i}.</td>
          <td style='padding:10px 14px;border-bottom:1px solid #e8ddd0;'>
            <strong>{ln.product_type.replace("_"," ").title()}</strong> — {ln.wood_species.replace("_"," ").title()}<br>
            <span style='color:#7a6652;font-size:13px;'>Grade {ln.quality_grade}</span>{rate_hint}
          </td>
          <td style='padding:10px 14px;border-bottom:1px solid #e8ddd0;color:#444;font-size:13px;'>
            {ln.length} × {ln.width} × {ln.height} {ln.unit}
          </td>
          <td style='padding:10px 14px;border-bottom:1px solid #e8ddd0;color:#444;font-size:13px;'>
            {ln.quantity} {ln.quantity_unit}<br>
            <span style='color:#7a6652;'>{ln.container_size}</span>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style='margin:0;padding:0;background:#f0e9df;font-family:Segoe UI,Arial,sans-serif;'>
  <table width='100%' cellpadding='0' cellspacing='0' style='background:#f0e9df;padding:32px 0;'>
    <tr><td align='center'>
      <table width='620' cellpadding='0' cellspacing='0' style='background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 12px rgba(92,61,30,.15);'>

        <!-- Header -->
        <tr>
          <td style='background:#5c3d1e;padding:24px 32px;'>
            <table cellpadding='0' cellspacing='0'>
              <tr>
                <td style='font-size:32px;padding-right:14px;'>🪵</td>
                <td>
                  <div style='color:#fff;font-size:20px;font-weight:700;'>Wood Export — Request for Quotation</div>
                  <div style='color:rgba(255,255,255,.7);font-size:13px;margin-top:3px;'>RFQ #{rfq.id[:8].upper()} · {rfq.destination_country}</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style='padding:28px 32px;'>
            <p style='margin:0 0 16px;color:#2c1a0e;font-size:15px;'>Dear <strong>{payload.supplier_name}</strong>,</p>
            <p style='margin:0 0 20px;color:#444;font-size:14px;line-height:1.6;'>
              We are requesting a quotation for the items below, for export to
              <strong>{rfq.destination_country}</strong>
              (Port of Loading: <strong>{rfq.origin_port or "Indonesia"}</strong> →
              Port of Discharge: <strong>{rfq.destination_port or rfq.destination_city or rfq.destination_country}</strong>).
            </p>

            <!-- Items table -->
            <table width='100%' cellpadding='0' cellspacing='0' style='border:1px solid #e8ddd0;border-radius:6px;overflow:hidden;margin-bottom:24px;'>
              <thead>
                <tr style='background:#5c3d1e;'>
                  <th style='padding:9px 14px;color:#fff;font-size:12px;text-align:left;width:30px;'>#</th>
                  <th style='padding:9px 14px;color:#fff;font-size:12px;text-align:left;'>Product</th>
                  <th style='padding:9px 14px;color:#fff;font-size:12px;text-align:left;'>Dimensions</th>
                  <th style='padding:9px 14px;color:#fff;font-size:12px;text-align:left;'>Quantity</th>
                </tr>
              </thead>
              <tbody>{rows_html}
              </tbody>
            </table>

            <!-- We need section -->
            <div style='background:#faf6f0;border:1px solid #e8ddd0;border-radius:8px;padding:16px 20px;margin-bottom:24px;'>
              <p style='margin:0 0 8px;color:#5c3d1e;font-weight:700;font-size:14px;'>Please provide:</p>
              <ul style='margin:0;padding-left:18px;color:#444;font-size:13px;line-height:1.8;'>
                <li>Unit price per CBM in <strong>{payload.preferred_currency}</strong></li>
                <li>Lead time in days</li>
                <li>Photos and/or video of available stock</li>
                <li>Any grade / quality notes</li>
              </ul>
            </div>

            <!-- CTA button -->
            <div style='background:linear-gradient(135deg,#5c3d1e,#7a5230);border-radius:10px;padding:24px 28px;text-align:center;margin-bottom:24px;'>
              <p style='margin:0 0 6px;color:rgba(255,255,255,.85);font-size:13px;'>⭐ Fastest way to respond — takes 2 minutes:</p>
              <p style='margin:0 0 18px;color:#fff;font-size:16px;font-weight:700;'>Submit Your Quote Online</p>
              <a href='{form_url}'
                 style='display:inline-block;background:#c8922a;color:#fff;text-decoration:none;
                        padding:13px 32px;border-radius:6px;font-size:15px;font-weight:700;
                        letter-spacing:.3px;'>
                Submit Quote →
              </a>
              <p style='margin:14px 0 0;color:rgba(255,255,255,.6);font-size:11px;'>
                Or reply to this email with your rates per CBM and lead times.
              </p>
            </div>

          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style='background:#f0e9df;padding:16px 32px;border-top:1px solid #e8ddd0;'>
            <p style='margin:0;color:#7a6652;font-size:12px;'>Wood Export Team · RFQ Reference: {rfq.id}</p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _form_link(rfq_id: str, supplier_id: str) -> str:
    """Build the supplier response form URL."""
    token = make_token(rfq_id, supplier_id)
    base_url = os.getenv("SERVER_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base_url}/supplier-quote/{rfq_id}/{token}"
