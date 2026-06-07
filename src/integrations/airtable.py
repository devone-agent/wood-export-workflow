"""
Airtable integration — supplier database and RFQ state store.

Base structure:
  Table: Suppliers   — id, name, email, whatsapp, wood_specialisms,
                        preferred_unit, preferred_currency, rating, active
  Table: RFQs        — id, status, buyer_name, created_at, ...
  Table: LineItems   — rfq_id, product_type, wood_species, ...
  Table: Quotes      — rfq_id, supplier_id, line_item_id, price, currency, ...

Env vars required:
  AIRTABLE_API_KEY
  AIRTABLE_BASE_ID
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

AIRTABLE_API_BASE = "https://api.airtable.com/v0"


class AirtableClient:
    def __init__(self, api_key: str, base_id: str):
        self.base_id = base_id
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _url(self, table: str) -> str:
        return f"{AIRTABLE_API_BASE}/{self.base_id}/{table}"

    _TIMEOUT = 30.0  # seconds — Airtable cold-start can be slow

    def list_records(
        self,
        table: str,
        filter_formula: Optional[str] = None,
        max_records: int = 100,
    ) -> list[dict]:
        params: dict[str, Any] = {"maxRecords": max_records}
        if filter_formula:
            params["filterByFormula"] = filter_formula

        resp = httpx.get(
            self._url(table), headers=self._headers, params=params,
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("records", [])

    def create_record(self, table: str, fields: dict) -> dict:
        resp = httpx.post(
            self._url(table),
            headers=self._headers,
            json={"fields": fields},
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def update_record(self, table: str, record_id: str, fields: dict) -> dict:
        resp = httpx.patch(
            f"{self._url(table)}/{record_id}",
            headers=self._headers,
            json={"fields": fields},
            timeout=self._TIMEOUT,
        )
        if not resp.is_success:
            logger.error(
                "Airtable PATCH %s/%s → %s: %s",
                table, record_id, resp.status_code, resp.text,
            )
        resp.raise_for_status()
        return resp.json()

    # ── Supplier helpers ───────────────────────────────────────────────────────

    def get_suppliers_for_species(self, wood_species: str) -> list[dict]:
        """Return active suppliers that handle the given wood species."""
        formula = (
            f"AND({{Active}}=1, FIND('{wood_species}', ARRAYJOIN({{Wood Specialisms}}, ',')))"
        )
        records = self.list_records("Suppliers", filter_formula=formula)
        return [_normalise_supplier(r) for r in records]

    def get_all_active_suppliers(self) -> list[dict]:
        records = self.list_records("Suppliers", filter_formula="{Active}=1")
        return [_normalise_supplier(r) for r in records]

    # ── RFQ state helpers ──────────────────────────────────────────────────────

    def save_rfq(self, rfq_dict: dict) -> str:
        """Upsert an RFQ record. Returns the Airtable record ID."""
        record = self.create_record("RFQs", rfq_dict)
        return record["id"]

    def update_rfq_status(self, airtable_record_id: str, status: str) -> None:
        self.update_record("RFQs", airtable_record_id, {"status": status})


def _normalise_supplier(record: dict) -> dict:
    """
    Map Airtable title-case field names to the snake_case keys
    expected by rfq_builder and the rest of the codebase.
    """
    f = record.get("fields", {})
    return {
        "id": record.get("id", ""),
        "_id": record.get("id", ""),
        "name": f.get("Name", ""),
        "email": f.get("Email", ""),
        "whatsapp": f.get("WhatsApp", ""),
        "country": f.get("Country", ""),
        "wood_specialisms": f.get("Wood Specialisms", []),
        "preferred_unit": f.get("Preferred Unit", "m"),
        "preferred_currency": f.get("Preferred Currency", "USD"),
        "rating": f.get("Rating", 5),
        "active": f.get("Active", False),
        "notes": f.get("Notes", ""),
    }


def get_airtable_client() -> Optional[AirtableClient]:
    """Factory — returns None if env vars not set."""
    api_key = os.getenv("AIRTABLE_API_KEY")
    base_id = os.getenv("AIRTABLE_BASE_ID")
    if not api_key or not base_id:
        logger.warning("AIRTABLE_API_KEY or AIRTABLE_BASE_ID not set — Airtable unavailable")
        return None
    return AirtableClient(api_key, base_id)
