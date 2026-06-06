"""
Step 3 — Parse incoming supplier responses from WhatsApp and email.

Uses Claude (claude-haiku-4-5) to extract structured rate/quality data
from free-form text. Falls back to regex extraction for simple cases.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Structured extraction schema ──────────────────────────────────────────────
EXTRACTION_SCHEMA = {
    "line_items": [
        {
            "description": "str — product/species description from the text",
            "price_per_unit": "float",
            "price_currency": "str — USD | INR | IDR | EUR",
            "price_unit": "str — cbm | piece | set",
            "quality_grade": "str or null",
            "lead_time_days": "int or null",
            "notes": "str or null",
        }
    ]
}

SYSTEM_PROMPT = """You are a parser for wood export supplier responses.
Extract pricing and quality information from the supplier's message and return valid JSON.
Schema: {"line_items": [{"description": str, "price_per_unit": float, "price_currency": str,
"price_unit": str, "quality_grade": str|null, "lead_time_days": int|null, "notes": str|null}]}
Price units: use "cbm" for per cubic metre, "piece" for per piece, "set" for per set.
Currencies: USD, INR, IDR, EUR. If currency is Rupiah/Rp use IDR; if Rupee/Rs use INR.
Return ONLY the JSON object, no explanation."""


@dataclass
class ParsedLineItem:
    description: str
    price_per_unit: float
    price_currency: str
    price_unit: str  # cbm | piece | set
    quality_grade: Optional[str] = None
    lead_time_days: Optional[int] = None
    notes: Optional[str] = None


@dataclass
class ParsedResponse:
    supplier_id: str
    rfq_id: str
    raw_text: str
    source: str  # "whatsapp" | "email"
    line_items: list[ParsedLineItem] = field(default_factory=list)
    parse_confidence: float = 1.0  # 0–1
    parse_method: str = "regex"  # "ai" | "regex" | "manual"
    parse_errors: list[str] = field(default_factory=list)


def parse_supplier_response(
    raw_text: str,
    supplier_id: str,
    rfq_id: str,
    source: str = "whatsapp",
    anthropic_client=None,  # injected anthropic.Anthropic client
) -> ParsedResponse:
    """
    Parse a free-form supplier response into structured data.
    Tries AI extraction first if client provided; falls back to regex.
    """
    result = ParsedResponse(
        supplier_id=supplier_id,
        rfq_id=rfq_id,
        raw_text=raw_text,
        source=source,
    )

    if anthropic_client:
        try:
            items = _parse_with_ai(raw_text, anthropic_client)
            result.line_items = items
            result.parse_method = "ai"
            result.parse_confidence = 0.9
            return result
        except Exception as exc:
            logger.warning("AI parse failed, falling back to regex: %s", exc)
            result.parse_errors.append(f"AI parse error: {exc}")

    # Regex fallback
    items = _parse_with_regex(raw_text)
    result.line_items = items
    result.parse_method = "regex"
    result.parse_confidence = 0.6 if items else 0.1
    if not items:
        result.parse_errors.append("No structured data extracted by regex — manual review required")

    return result


def _parse_with_ai(raw_text: str, client) -> list[ParsedLineItem]:
    """Call Claude Haiku to extract structured data."""
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": raw_text}],
    )
    content = message.content[0].text.strip()
    # Strip markdown code fences if present
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    data = json.loads(content)
    return [ParsedLineItem(**item) for item in data.get("line_items", [])]


# ── Regex patterns ─────────────────────────────────────────────────────────────

_CURRENCY_PATTERNS = {
    "USD": r"(?:USD|\$)",
    "INR": r"(?:INR|Rs\.?|₹|Rupee)",
    "IDR": r"(?:IDR|Rp\.?|Rupiah)",
    "EUR": r"(?:EUR|€)",
}

_PRICE_REGEX = re.compile(
    r"(?P<currency>" + "|".join(_CURRENCY_PATTERNS.values()) + r")?\s*"
    r"(?P<price>[\d,]+(?:\.\d{1,2})?)\s*"
    r"(?:(?:per|/)\s*(?P<unit>cbm|m3|m³|piece|pcs|set))?",
    re.IGNORECASE,
)

_LEAD_TIME_REGEX = re.compile(
    r"(?:lead\s*time|delivery|ready\s*in)[:\s]+(?P<days>\d+)\s*(?:days?|d\b)",
    re.IGNORECASE,
)


def _normalise_currency(raw: Optional[str]) -> str:
    if not raw:
        return "USD"
    raw = raw.upper()
    if any(c in raw for c in ["RS", "INR", "₹", "RUPEE"]):
        return "INR"
    if any(c in raw for c in ["RP", "IDR", "RUPIAH"]):
        return "IDR"
    if "EUR" in raw or "€" in raw:
        return "EUR"
    return "USD"


def _normalise_unit(raw: Optional[str]) -> str:
    if not raw:
        return "cbm"
    raw = raw.lower()
    if raw in ("m3", "m³", "cbm"):
        return "cbm"
    if raw in ("pcs", "piece", "pieces"):
        return "piece"
    return raw


def _parse_with_regex(text: str) -> list[ParsedLineItem]:
    items: list[ParsedLineItem] = []
    lead_match = _LEAD_TIME_REGEX.search(text)
    lead_time = int(lead_match.group("days")) if lead_match else None

    for match in _PRICE_REGEX.finditer(text):
        price_str = match.group("price").replace(",", "")
        try:
            price = float(price_str)
        except ValueError:
            continue
        # Skip implausibly small numbers (phone numbers, dates, etc.)
        if price < 1:
            continue

        items.append(ParsedLineItem(
            description="(extracted by regex — verify manually)",
            price_per_unit=price,
            price_currency=_normalise_currency(match.group("currency")),
            price_unit=_normalise_unit(match.group("unit")),
            lead_time_days=lead_time,
        ))

    return items
