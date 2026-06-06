"""RFQ (Request for Quote) data models."""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ProductType(str, Enum):
    READY_FURNITURE = "ready_furniture"
    SAWN_TIMBER = "sawn_timber"
    FLOORING = "flooring"
    PANELLING = "panelling"


class WoodSpecies(str, Enum):
    TEAK_A = "teak_a"
    TEAK_B = "teak_b"
    TEAK_C = "teak_c"
    MAHOGANY = "mahogany"
    TERRAMBESE = "terrambese"
    MERANTI = "meranti"
    BERANGKAI = "berangkai"
    PLYWOOD = "plywood"
    VENEER = "veneer"
    MDF = "mdf"
    OTHER = "other"


class QualityGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    NA = "N/A"


class ContainerSize(str, Enum):
    TWENTY_FT = "20ft"
    FORTY_FT = "40ft"


class Currency(str, Enum):
    USD = "USD"
    INR = "INR"
    IDR = "IDR"
    EUR = "EUR"


class UnitType(str, Enum):
    FT = "ft"
    CM = "cm"
    MM = "mm"
    M = "m"


class Dimensions(BaseModel):
    length: float
    width: float
    height: float
    unit: UnitType = UnitType.CM

    def to_meters(self) -> "Dimensions":
        """Convert all dimensions to metres."""
        factor = {
            UnitType.FT: 0.3048,
            UnitType.CM: 0.01,
            UnitType.MM: 0.001,
            UnitType.M: 1.0,
        }[self.unit]
        return Dimensions(
            length=self.length * factor,
            width=self.width * factor,
            height=self.height * factor,
            unit=UnitType.M,
        )

    @property
    def cbm(self) -> float:
        """Volume in cubic metres."""
        m = self.to_meters()
        return m.length * m.width * m.height


class QuantityUnit(str, Enum):
    PIECES = "pieces"
    CBM = "cbm"
    SETS = "sets"


class LineItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    product_type: ProductType
    wood_species: WoodSpecies
    quality_grade: QualityGrade = QualityGrade.NA
    dimensions: Dimensions
    quantity: float
    quantity_unit: QuantityUnit = QuantityUnit.PIECES
    container_size: ContainerSize
    expected_rate: Optional[float] = None
    expected_rate_currency: Currency = Currency.USD
    notes: Optional[str] = None

    @property
    def total_cbm(self) -> float:
        """Total volume: dimensions CBM × quantity (when unit is pieces)."""
        if self.quantity_unit == QuantityUnit.CBM:
            return self.quantity
        return self.dimensions.cbm * self.quantity


class RFQStatus(str, Enum):
    DRAFT = "draft"
    SENT_TO_SUPPLIERS = "sent_to_suppliers"
    AWAITING_RESPONSES = "awaiting_responses"
    RESPONSES_RECEIVED = "responses_received"
    QUOTE_GENERATED = "quote_generated"
    QUOTE_SENT = "quote_sent"
    NEGOTIATING = "negotiating"
    ACCEPTED = "accepted"
    CLOSED = "closed"


class RFQ(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    status: RFQStatus = RFQStatus.DRAFT

    # Buyer details
    buyer_name: str
    buyer_email: Optional[str] = None
    buyer_whatsapp: Optional[str] = None

    # Destination
    destination_country: str = "India"
    destination_port: Optional[str] = None
    destination_city: Optional[str] = None

    # Origin
    origin_country: str = "Indonesia"
    origin_port: Optional[str] = None

    # Line items
    line_items: list[LineItem] = Field(default_factory=list)

    # Preferred currency for buyer quote output
    preferred_currency: Currency = Currency.USD

    # Negotiation rounds counter
    negotiation_rounds: int = 0
    max_negotiation_rounds: int = 3

    @model_validator(mode="after")
    def validate_line_items(self) -> "RFQ":
        if not self.line_items:
            raise ValueError("RFQ must have at least one line item")
        return self
