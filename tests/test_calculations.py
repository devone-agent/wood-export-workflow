"""Tests for the calculations module (units, FX, pricing)."""
import pytest
from src.models.rfq import Dimensions, UnitType
from src.calculations.units import calculate_cbm, format_dimensions_all_units, cbm_for_quantity
from src.calculations.pricing import apply_markup, check_margin_feasibility, price_line_item
from src.calculations.fx import FXRates


# ── Unit conversion ───────────────────────────────────────────────────────────

class TestCBMCalculation:
    def test_metres_identity(self):
        dims = Dimensions(length=2.0, width=1.0, height=0.05, unit=UnitType.M)
        assert calculate_cbm(dims) == pytest.approx(0.1, rel=1e-6)

    def test_cm_conversion(self):
        # 200cm × 100cm × 5cm = 0.1 CBM
        dims = Dimensions(length=200, width=100, height=5, unit=UnitType.CM)
        assert calculate_cbm(dims) == pytest.approx(0.1, rel=1e-6)

    def test_mm_conversion(self):
        dims = Dimensions(length=2000, width=1000, height=50, unit=UnitType.MM)
        assert calculate_cbm(dims) == pytest.approx(0.1, rel=1e-6)

    def test_ft_conversion(self):
        # 1ft × 1ft × 1ft ≈ 0.028317 CBM
        dims = Dimensions(length=1, width=1, height=1, unit=UnitType.FT)
        assert calculate_cbm(dims) == pytest.approx(0.028317, rel=1e-3)

    def test_all_units_output(self):
        dims = Dimensions(length=200, width=100, height=5, unit=UnitType.CM)
        all_units = format_dimensions_all_units(dims)
        assert "m" in all_units
        assert "ft" in all_units
        assert all_units["m"]["L"] == pytest.approx(2.0, rel=1e-6)

    def test_cbm_for_quantity_pieces(self):
        dims = Dimensions(length=200, width=100, height=5, unit=UnitType.CM)
        total = cbm_for_quantity(dims, 10, "pieces")
        assert total == pytest.approx(1.0, rel=1e-6)

    def test_cbm_for_quantity_cbm(self):
        dims = Dimensions(length=200, width=100, height=5, unit=UnitType.CM)
        total = cbm_for_quantity(dims, 5.5, "cbm")
        assert total == pytest.approx(5.5)


# ── Pricing engine ────────────────────────────────────────────────────────────

class TestPricing:
    def test_markup_3pct(self):
        result = apply_markup(100.0, 0.03)
        assert result == pytest.approx(103.0)

    def test_markup_zero(self):
        assert apply_markup(100.0, 0.0) == pytest.approx(100.0)

    def test_feasibility_above_floor(self):
        feasible, minimum = check_margin_feasibility(
            buyer_target_usd_per_cbm=110.0,
            best_supplier_usd_per_cbm=100.0,
            markup_pct=0.03,
        )
        assert feasible is True
        assert minimum == pytest.approx(103.0)

    def test_feasibility_below_floor(self):
        feasible, minimum = check_margin_feasibility(
            buyer_target_usd_per_cbm=100.0,
            best_supplier_usd_per_cbm=100.0,
            markup_pct=0.03,
        )
        assert feasible is False
        assert minimum == pytest.approx(103.0)

    def test_feasibility_exactly_at_floor(self):
        feasible, _ = check_margin_feasibility(103.0, 100.0, 0.03)
        assert feasible is True

    def test_price_line_item(self):
        fx = FXRates(usd_to_inr=83.0, usd_to_idr=15500.0)
        priced = price_line_item(
            rfq_line_item_id="test-id",
            supplier_id="sup-1",
            supplier_price_usd_per_cbm=500.0,
            total_cbm=2.0,
            markup_pct=0.03,
            fx=fx,
        )
        assert priced.buyer_price_usd_per_cbm == pytest.approx(515.0)
        assert priced.total_usd == pytest.approx(1030.0)
        assert priced.total_inr == pytest.approx(1030.0 * 83.0)
        assert priced.total_idr == pytest.approx(1030.0 * 15500.0)


# ── FX rates ──────────────────────────────────────────────────────────────────

class TestFXRates:
    def test_convert_usd_to_inr(self):
        fx = FXRates(usd_to_inr=83.0, usd_to_idr=15000.0)
        assert fx.convert(100.0, "INR") == pytest.approx(8300.0)

    def test_convert_usd_to_idr(self):
        fx = FXRates(usd_to_inr=83.0, usd_to_idr=15000.0)
        assert fx.convert(1.0, "IDR") == pytest.approx(15000.0)

    def test_to_usd_from_inr(self):
        fx = FXRates(usd_to_inr=80.0, usd_to_idr=15000.0)
        assert fx.to_usd(800.0, "INR") == pytest.approx(10.0)

    def test_multi_currency(self):
        fx = FXRates(usd_to_inr=80.0, usd_to_idr=15000.0)
        mc = fx.multi_currency(100.0)
        assert mc["USD"] == pytest.approx(100.0)
        assert mc["INR"] == pytest.approx(8000.0)
        assert mc["IDR"] == pytest.approx(1_500_000.0)

    def test_unknown_currency_raises(self):
        fx = FXRates()
        with pytest.raises(ValueError):
            fx.convert(100.0, "GBP")
