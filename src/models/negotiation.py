"""Negotiation round models."""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .rfq import Currency


class NegotiationStatus(str, Enum):
    PENDING = "pending"
    FEASIBLE = "feasible"          # target >= best_supplier + 3%
    INFEASIBLE = "infeasible"      # target < minimum achievable
    ACCEPTED = "accepted"
    ROUND_LIMIT_REACHED = "round_limit_reached"


class NegotiationRound(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rfq_id: str
    round_number: int  # 1-based
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Buyer's counter-offer
    buyer_target_rate: float  # per CBM
    buyer_target_currency: Currency = Currency.USD
    buyer_target_rate_usd: float  # normalised

    # Best achievable rate (best supplier + 3%)
    minimum_achievable_usd: float
    best_supplier_id: Optional[str] = None

    status: NegotiationStatus = NegotiationStatus.PENDING
    notes: Optional[str] = None

    # The revised quote sent if feasible
    revised_quote_id: Optional[str] = None
