"""Supplier and supplier quote data models."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .rfq import Currency, UnitType, WoodSpecies


class MediaItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    url: str
    media_type: str  # "image" | "video" | "document"
    filename: Optional[str] = None
    description: Optional[str] = None
    supplier_id: str
    rfq_line_item_id: str
    received_at: datetime = Field(default_factory=datetime.utcnow)


class Supplier(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    country: str = "Indonesia"
    wood_specialisms: list[WoodSpecies] = Field(default_factory=list)
    preferred_unit: UnitType = UnitType.CM
    preferred_currency: Currency = Currency.USD
    rating: float = 5.0  # 1–10 past performance rating
    active: bool = True
    notes: Optional[str] = None


class RFQLineResponse(BaseModel):
    """A supplier's response to a single RFQ line item."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rfq_id: str
    rfq_line_item_id: str
    supplier_id: str
    received_at: datetime = Field(default_factory=datetime.utcnow)

    # Pricing as supplied (supplier's native unit/currency)
    price_per_unit: float
    price_currency: Currency
    price_unit: str  # "cbm" | "piece" | "set"

    # Normalised to USD/m³ (filled by parser)
    price_usd_per_cbm: Optional[float] = None

    # Quality info
    quality_grade: Optional[str] = None
    quality_notes: Optional[str] = None

    # Media
    media_items: list[MediaItem] = Field(default_factory=list)

    # Raw text from WhatsApp/email for audit
    raw_response: Optional[str] = None

    # Lead time in days
    lead_time_days: Optional[int] = None


class SupplierQuote(BaseModel):
    """Aggregated quote from one supplier across all RFQ line items."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rfq_id: str
    supplier_id: str
    supplier_name: str
    received_at: datetime = Field(default_factory=datetime.utcnow)
    line_responses: list[RFQLineResponse] = Field(default_factory=list)

    @property
    def total_usd(self) -> float:
        return sum(
            (r.price_usd_per_cbm or 0) for r in self.line_responses
        )

    def response_for_line(self, line_item_id: str) -> Optional[RFQLineResponse]:
        return next((r for r in self.line_responses if r.rfq_line_item_id == line_item_id), None)
