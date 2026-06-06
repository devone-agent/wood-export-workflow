"""
Step 1 — Buyer demand form validation.

Accepts raw buyer input (from API or chat), validates all fields,
and returns a clean RFQ-ready object.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

from src.models.rfq import (
    RFQ, LineItem, Dimensions,
    ProductType, WoodSpecies, QualityGrade,
    ContainerSize, Currency, UnitType, QuantityUnit,
)


class LineItemInput(BaseModel):
    product_type: str
    wood_species: str
    quality_grade: str = "N/A"
    length: float
    width: float
    height: float
    unit: str = "cm"
    quantity: float
    quantity_unit: str = "pieces"
    container_size: str = "20ft"
    expected_rate: Optional[float] = None
    expected_rate_currency: str = "USD"
    notes: Optional[str] = None

    @field_validator("product_type")
    @classmethod
    def validate_product_type(cls, v: str) -> str:
        try:
            ProductType(v.lower().replace(" ", "_"))
        except ValueError:
            valid = [e.value for e in ProductType]
            raise ValueError(f"product_type must be one of {valid}")
        return v.lower().replace(" ", "_")

    @field_validator("wood_species")
    @classmethod
    def validate_wood_species(cls, v: str) -> str:
        normalised = v.lower().replace(" ", "_").replace("-", "_")
        # Allow partial matches for common names
        alias_map = {
            "teak": "teak_a",
            "teka": "teak_a",
            "jati": "teak_a",
        }
        normalised = alias_map.get(normalised, normalised)
        try:
            WoodSpecies(normalised)
        except ValueError:
            valid = [e.value for e in WoodSpecies]
            raise ValueError(f"wood_species must be one of {valid}")
        return normalised

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, v: str) -> str:
        try:
            UnitType(v.lower())
        except ValueError:
            raise ValueError("unit must be ft, cm, mm, or m")
        return v.lower()

    @field_validator("length", "width", "height")
    @classmethod
    def validate_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Dimensions must be positive")
        return v

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Quantity must be positive")
        return v


class BuyerFormInput(BaseModel):
    buyer_name: str
    buyer_email: Optional[str] = None
    buyer_whatsapp: Optional[str] = None
    destination_country: str = "India"
    destination_port: Optional[str] = None
    destination_city: Optional[str] = None
    origin_port: Optional[str] = None
    preferred_currency: str = "USD"
    line_items: list[LineItemInput]

    @model_validator(mode="after")
    def at_least_one_contact(self) -> "BuyerFormInput":
        if not self.buyer_email and not self.buyer_whatsapp:
            raise ValueError("At least one of buyer_email or buyer_whatsapp is required")
        return self

    @model_validator(mode="after")
    def has_line_items(self) -> "BuyerFormInput":
        if not self.line_items:
            raise ValueError("At least one line item is required")
        return self


def validate_buyer_form(raw: dict) -> tuple[BuyerFormInput, list[str]]:
    """
    Validate raw buyer form data.
    Returns (BuyerFormInput, []) on success or raises ValidationError.
    Returns (None, [error_messages]) on validation failure.
    """
    from pydantic import ValidationError
    errors: list[str] = []
    try:
        form = BuyerFormInput(**raw)
        return form, errors
    except ValidationError as exc:
        for err in exc.errors():
            loc = " → ".join(str(l) for l in err["loc"])
            errors.append(f"{loc}: {err['msg']}")
        return None, errors


def form_to_rfq(form: BuyerFormInput) -> RFQ:
    """Convert a validated BuyerFormInput into an RFQ domain object."""
    line_items = []
    for item in form.line_items:
        line_items.append(LineItem(
            product_type=ProductType(item.product_type),
            wood_species=WoodSpecies(item.wood_species),
            quality_grade=QualityGrade(item.quality_grade) if item.quality_grade != "N/A" else QualityGrade.NA,
            dimensions=Dimensions(
                length=item.length,
                width=item.width,
                height=item.height,
                unit=UnitType(item.unit),
            ),
            quantity=item.quantity,
            quantity_unit=QuantityUnit(item.quantity_unit),
            container_size=ContainerSize(item.container_size),
            expected_rate=item.expected_rate,
            expected_rate_currency=Currency(item.expected_rate_currency),
            notes=item.notes,
        ))

    return RFQ(
        buyer_name=form.buyer_name,
        buyer_email=form.buyer_email,
        buyer_whatsapp=form.buyer_whatsapp,
        destination_country=form.destination_country,
        destination_port=form.destination_port,
        destination_city=form.destination_city,
        origin_port=form.origin_port,
        preferred_currency=Currency(form.preferred_currency),
        line_items=line_items,
    )
