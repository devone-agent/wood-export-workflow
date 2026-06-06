"""Tests for the negotiation engine."""
import pytest
from src.models.rfq import RFQ, LineItem, Dimensions, UnitType, ProductType, WoodSpecies, ContainerSize
from src.models.negotiation import NegotiationStatus
from src.negotiation.engine import NegotiationEngine
from src.calculations.fx import FXRates


def _make_rfq(rounds=0) -> RFQ:
    return RFQ(
        buyer_name="Test Buyer",
        buyer_email="test@example.com",
        line_items=[LineItem(
            product_type=ProductType.SAWN_TIMBER,
            wood_species=WoodSpecies.TEAK_A,
            dimensions=Dimensions(length=200, width=100, height=5, unit=UnitType.CM),
            quantity=10,
            container_size=ContainerSize.TWENTY_FT,
        )],
        negotiation_rounds=rounds,
    )


class TestNegotiationEngine:
    def setup_method(self):
        self.engine = NegotiationEngine(markup_pct=0.03)
        self.fx = FXRates(usd_to_inr=83.0, usd_to_idr=15500.0)

    def test_feasible_target(self):
        rfq = _make_rfq()
        result = self.engine.evaluate(
            rfq=rfq,
            buyer_target_rate=110.0,
            buyer_target_currency="USD",
            best_supplier_price_usd_per_cbm=100.0,
            fx=self.fx,
        )
        assert result.status == NegotiationStatus.FEASIBLE
        assert result.minimum_achievable_usd == pytest.approx(103.0)

    def test_infeasible_target(self):
        rfq = _make_rfq()
        result = self.engine.evaluate(
            rfq=rfq,
            buyer_target_rate=100.0,
            buyer_target_currency="USD",
            best_supplier_price_usd_per_cbm=100.0,
            fx=self.fx,
        )
        assert result.status == NegotiationStatus.INFEASIBLE
        assert result.gap_usd == pytest.approx(-3.0)

    def test_exactly_at_floor(self):
        rfq = _make_rfq()
        result = self.engine.evaluate(
            rfq=rfq,
            buyer_target_rate=103.0,
            buyer_target_currency="USD",
            best_supplier_price_usd_per_cbm=100.0,
            fx=self.fx,
        )
        assert result.status == NegotiationStatus.FEASIBLE

    def test_round_limit_reached(self):
        rfq = _make_rfq(rounds=3)
        result = self.engine.evaluate(
            rfq=rfq,
            buyer_target_rate=200.0,
            buyer_target_currency="USD",
            best_supplier_price_usd_per_cbm=100.0,
            fx=self.fx,
        )
        assert result.status == NegotiationStatus.ROUND_LIMIT_REACHED

    def test_inr_target_normalised(self):
        rfq = _make_rfq()
        # INR 9000/CBM ≈ USD 108.43 at 83 INR/USD — above floor of 103
        result = self.engine.evaluate(
            rfq=rfq,
            buyer_target_rate=9000.0,
            buyer_target_currency="INR",
            best_supplier_price_usd_per_cbm=100.0,
            fx=self.fx,
        )
        assert result.status == NegotiationStatus.FEASIBLE

    def test_record_round_increments_counter(self):
        rfq = _make_rfq()
        result = self.engine.evaluate(
            rfq=rfq,
            buyer_target_rate=110.0,
            buyer_target_currency="USD",
            best_supplier_price_usd_per_cbm=100.0,
            fx=self.fx,
        )
        self.engine.record_round(rfq, result)
        assert rfq.negotiation_rounds == 1
