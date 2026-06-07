"""
Supplier response token — signs and verifies per-supplier form links.

Token = first 24 hex chars of HMAC-SHA256(SECRET_KEY, "{rfq_id}:{supplier_id}")

This lets the GET /supplier-quote/{rfq_id}/{token} endpoint verify that:
  1. The link was issued by us (not guessable)
  2. The supplier_id embedded in the URL or looked up from the RFQ matches

Env var:
  SECRET_KEY   — any random string; falls back to "dev-secret" if unset
"""
from __future__ import annotations

import hashlib
import hmac
import os


def _secret() -> bytes:
    return os.getenv("SECRET_KEY", "dev-secret").encode()


def make_token(rfq_id: str, supplier_id: str) -> str:
    """Generate a URL-safe token for a specific rfq+supplier pair."""
    msg = f"{rfq_id}:{supplier_id}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:24]


def verify_token(rfq_id: str, supplier_id: str, token: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    expected = make_token(rfq_id, supplier_id)
    return hmac.compare_digest(expected, token)
