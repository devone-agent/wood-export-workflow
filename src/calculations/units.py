"""Unit conversion utilities — Step 7A of the workflow."""
from __future__ import annotations

from dataclasses import dataclass

from src.models.rfq import Dimensions, UnitType


# Conversion factors to metres
_TO_METRES: dict[UnitType, float] = {
    UnitType.FT: 0.3048,
    UnitType.CM: 0.01,
    UnitType.MM: 0.001,
    UnitType.M: 1.0,
}


@dataclass
class DimensionsM:
    """Dimensions guaranteed to be in metres."""
    length_m: float
    width_m: float
    height_m: float

    @property
    def cbm(self) -> float:
        return self.length_m * self.width_m * self.height_m

    def __repr__(self) -> str:
        return (
            f"DimensionsM(L={self.length_m:.4f}m × W={self.width_m:.4f}m "
            f"× H={self.height_m:.4f}m = {self.cbm:.6f} CBM)"
        )


def convert_to_meters(value: float, unit: UnitType) -> float:
    """Convert a single dimension value to metres."""
    factor = _TO_METRES.get(unit)
    if factor is None:
        raise ValueError(f"Unknown unit: {unit}")
    return value * factor


def dimensions_to_metres(dims: Dimensions) -> DimensionsM:
    """Convert a Dimensions object to metres regardless of input unit."""
    factor = _TO_METRES[dims.unit]
    return DimensionsM(
        length_m=dims.length * factor,
        width_m=dims.width * factor,
        height_m=dims.height * factor,
    )


def calculate_cbm(dims: Dimensions) -> float:
    """Return CBM for a Dimensions object."""
    return dimensions_to_metres(dims).cbm


def cbm_for_quantity(dims: Dimensions, quantity: float, quantity_unit: str) -> float:
    """
    Total CBM for a line item.

    - quantity_unit == 'cbm' → quantity is already in CBM
    - quantity_unit == 'pieces' → CBM × quantity
    - quantity_unit == 'sets'   → CBM × quantity (each set treated as one unit of the given dims)
    """
    if quantity_unit == "cbm":
        return quantity
    unit_cbm = calculate_cbm(dims)
    return unit_cbm * quantity


def format_dimensions_all_units(dims: Dimensions) -> dict[str, dict[str, float]]:
    """
    Return dimensions expressed in ft, cm, mm, and m.
    Useful for the output grid column 'L × W × H (all units)'.
    """
    m = dimensions_to_metres(dims)
    return {
        "m": {"L": m.length_m, "W": m.width_m, "H": m.height_m},
        "cm": {"L": m.length_m * 100, "W": m.width_m * 100, "H": m.height_m * 100},
        "mm": {"L": m.length_m * 1000, "W": m.width_m * 1000, "H": m.height_m * 1000},
        "ft": {"L": m.length_m / 0.3048, "W": m.width_m / 0.3048, "H": m.height_m / 0.3048},
    }
