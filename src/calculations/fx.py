"""
Live FX rate fetcher — Step 7 exchange rates.

Primary source:  https://open.er-api.com/v6/latest/USD  (free, no key)
Fallback source: https://api.exchangerate-api.com/v4/latest/USD
Manual override: set rates explicitly via FXRates(usd_to_inr=..., usd_to_idr=...)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 3600  # refresh at most once per hour

# Module-level cache
_cached_rates: Optional["FXRates"] = None
_cache_fetched_at: float = 0.0


@dataclass
class FXRates:
    """Exchange rates relative to USD 1.0."""
    usd_to_inr: float = 83.5
    usd_to_idr: float = 15_700.0
    usd_to_eur: float = 0.92
    source: str = "fallback_defaults"
    fetched_at: float = field(default_factory=time.time)

    def convert(self, amount_usd: float, to_currency: str) -> float:
        """Convert a USD amount to the target currency."""
        rates = {
            "USD": 1.0,
            "INR": self.usd_to_inr,
            "IDR": self.usd_to_idr,
            "EUR": self.usd_to_eur,
        }
        rate = rates.get(to_currency.upper())
        if rate is None:
            raise ValueError(f"Unsupported currency: {to_currency}")
        return amount_usd * rate

    def to_usd(self, amount: float, from_currency: str) -> float:
        """Convert any supported currency to USD."""
        rates = {
            "USD": 1.0,
            "INR": self.usd_to_inr,
            "IDR": self.usd_to_idr,
            "EUR": self.usd_to_eur,
        }
        rate = rates.get(from_currency.upper())
        if rate is None:
            raise ValueError(f"Unsupported currency: {from_currency}")
        return amount / rate

    def multi_currency(self, amount_usd: float) -> dict[str, float]:
        """Return amount in USD, INR, and IDR."""
        return {
            "USD": round(amount_usd, 2),
            "INR": round(self.convert(amount_usd, "INR"), 2),
            "IDR": round(self.convert(amount_usd, "IDR"), 0),
        }


def get_fx_rates(force_refresh: bool = False) -> FXRates:
    """
    Return cached FX rates, fetching from the API if stale or forced.
    Falls back to hardcoded defaults if the network call fails.
    """
    global _cached_rates, _cache_fetched_at

    now = time.time()
    if (
        not force_refresh
        and _cached_rates is not None
        and (now - _cache_fetched_at) < _CACHE_TTL_SECONDS
    ):
        return _cached_rates

    try:
        _cached_rates = _fetch_from_api()
        _cache_fetched_at = now
        logger.info("FX rates refreshed from API (source: %s)", _cached_rates.source)
    except Exception as exc:
        logger.warning("FX rate fetch failed (%s). Using defaults or cached.", exc)
        if _cached_rates is None:
            _cached_rates = FXRates()

    return _cached_rates


def _fetch_from_api() -> FXRates:
    """Attempt to fetch live rates. Tries two sources."""
    urls = [
        "https://open.er-api.com/v6/latest/USD",
        "https://api.exchangerate-api.com/v4/latest/USD",
    ]
    for url in urls:
        try:
            resp = httpx.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            rates = data.get("rates") or data.get("conversion_rates", {})
            return FXRates(
                usd_to_inr=float(rates["INR"]),
                usd_to_idr=float(rates["IDR"]),
                usd_to_eur=float(rates["EUR"]),
                source=url,
            )
        except Exception as exc:
            logger.debug("FX source %s failed: %s", url, exc)
            continue
    raise RuntimeError("All FX sources unavailable")


def override_rates(usd_to_inr: float, usd_to_idr: float, usd_to_eur: float = 0.92) -> FXRates:
    """Manually set FX rates (bypasses API). Persists for the session."""
    global _cached_rates, _cache_fetched_at
    _cached_rates = FXRates(
        usd_to_inr=usd_to_inr,
        usd_to_idr=usd_to_idr,
        usd_to_eur=usd_to_eur,
        source="manual_override",
    )
    _cache_fetched_at = time.time() + _CACHE_TTL_SECONDS * 24  # pin for 24h
    return _cached_rates
