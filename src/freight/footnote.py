"""
Step 8 — Freight charges footnote builder.

Freight is ALWAYS displayed separately — never bundled into unit price or CBM rate.
Format: "Freight: USD [X] per container (20ft / 40ft) — Port of [Origin] to Port of
[Destination]. Not included in above rates."
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.models.rfq import ContainerSize


@dataclass
class FreightFootnote:
    port_of_loading: str          # e.g. "Tanjung Priok, Jakarta"
    port_of_discharge: str        # e.g. "Nhava Sheva, Mumbai"
    container_size: ContainerSize
    freight_usd: float            # USD per container
    logistics_partner: Optional[str] = None
    notes: Optional[str] = None

    def to_string(self) -> str:
        partner_note = f" (via {self.logistics_partner})" if self.logistics_partner else ""
        extra = f" {self.notes}" if self.notes else ""
        return (
            f"USD {self.freight_usd:,.0f} per {self.container_size.value} container — "
            f"Port of {self.port_of_loading} to Port of {self.port_of_discharge}"
            f"{partner_note}. Not included in above rates.{extra}"
        )

    def to_dict(self) -> dict:
        return {
            "port_of_loading": self.port_of_loading,
            "port_of_discharge": self.port_of_discharge,
            "container_size": self.container_size.value,
            "freight_usd": self.freight_usd,
            "logistics_partner": self.logistics_partner,
            "display_text": self.to_string(),
        }


def build_freight_footnote(
    origin_port: Optional[str],
    destination_port: Optional[str],
    container_size: str,
    freight_usd: float,
    logistics_partner: Optional[str] = None,
) -> FreightFootnote:
    return FreightFootnote(
        port_of_loading=origin_port or "Indonesia",
        port_of_discharge=destination_port or "India",
        container_size=ContainerSize(container_size),
        freight_usd=freight_usd,
        logistics_partner=logistics_partner,
    )
