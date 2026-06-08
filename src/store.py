"""
ContextStore — persistent storage for WorkflowContext.

Two backends:
  InMemoryStore   — in-process dict; for unit tests and local dev
  AirtableStore   — persists to the RFQs table's context_json field;
                    survives server restarts and works 24/7

Usage:
    store = get_context_store()          # picks backend from env
    store.save(rfq_id, ctx)
    ctx = store.load(rfq_id)             # None if not found
    store.delete(rfq_id)                 # optional cleanup
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


# ── Serialisation helpers ──────────────────────────────────────────────────────

def _ctx_to_dict(ctx) -> dict:
    """
    Serialise a WorkflowContext to a plain dict safe for JSON storage.
    Derived fields (comparison_matrix, current_quote) are excluded —
    they are recomputed on demand.
    """
    from src.orchestrator import WorkflowContext
    from src.supplier.parser import ParsedResponse, ParsedLineItem
    from src.freight.footnote import FreightFootnote

    def _serialise_parsed_response(pr: ParsedResponse) -> dict:
        return {
            "supplier_id": pr.supplier_id,
            "rfq_id": pr.rfq_id,
            "raw_text": pr.raw_text,
            "source": pr.source,
            "parse_confidence": pr.parse_confidence,
            "parse_method": pr.parse_method,
            "line_items": [dataclasses.asdict(li) for li in pr.line_items],
        }

    def _serialise_freight(f: FreightFootnote) -> Optional[dict]:
        if f is None:
            return None
        return dataclasses.asdict(f)

    def _serialise_round(r) -> dict:
        # NegotiationRound is a Pydantic BaseModel, not a dataclass
        if hasattr(r, "model_dump"):
            return r.model_dump(mode="json")
        # fallback for any legacy dataclass rounds
        return dataclasses.asdict(r)

    return {
        "rfq": json.loads(ctx.rfq.model_dump_json()),
        "stage": ctx.stage.value,
        "supplier_quotes": [_serialise_parsed_response(q) for q in ctx.supplier_quotes],
        "negotiation_rounds": [_serialise_round(r) for r in ctx.negotiation_rounds],
        "freight": _serialise_freight(ctx.freight),
        "errors": ctx.errors,
    }


def _dict_to_ctx(data: dict):
    """Deserialise a plain dict back into a WorkflowContext."""
    from src.orchestrator import WorkflowContext, WorkflowStage
    from src.models.rfq import RFQ
    from src.supplier.parser import ParsedResponse, ParsedLineItem
    from src.freight.footnote import FreightFootnote
    from src.negotiation.engine import NegotiationRound

    rfq = RFQ.model_validate(data["rfq"])
    stage = WorkflowStage(data["stage"])

    supplier_quotes = []
    for q in data.get("supplier_quotes", []):
        line_items = [ParsedLineItem(**li) for li in q.get("line_items", [])]
        supplier_quotes.append(ParsedResponse(
            supplier_id=q["supplier_id"],
            rfq_id=q["rfq_id"],
            raw_text=q["raw_text"],
            source=q["source"],
            parse_confidence=q.get("parse_confidence", 1.0),
            parse_method=q.get("parse_method", "regex"),
            line_items=line_items,
        ))

    negotiation_rounds = []
    for r in data.get("negotiation_rounds", []):
        try:
            negotiation_rounds.append(NegotiationRound.model_validate(r))
        except Exception:
            pass  # skip malformed rounds rather than crash

    freight = None
    if data.get("freight"):
        try:
            freight = FreightFootnote(**data["freight"])
        except Exception:
            pass

    return WorkflowContext(
        rfq=rfq,
        stage=stage,
        supplier_quotes=supplier_quotes,
        negotiation_rounds=negotiation_rounds,
        freight=freight,
        errors=data.get("errors", []),
        # comparison_matrix and current_quote start as None — rebuilt on demand
    )


# ── Store ABC ──────────────────────────────────────────────────────────────────

class ContextStore(ABC):
    @abstractmethod
    def save(self, rfq_id: str, ctx) -> None: ...

    @abstractmethod
    def load(self, rfq_id: str): ...  # returns WorkflowContext or None

    @abstractmethod
    def delete(self, rfq_id: str) -> None: ...


# ── In-memory backend (tests / local dev) ─────────────────────────────────────

class InMemoryStore(ContextStore):
    def __init__(self):
        self._data: dict = {}

    def save(self, rfq_id: str, ctx) -> None:
        self._data[rfq_id] = ctx

    def load(self, rfq_id: str):
        return self._data.get(rfq_id)

    def delete(self, rfq_id: str) -> None:
        self._data.pop(rfq_id, None)


# ── Airtable backend (production) ─────────────────────────────────────────────

_RFQ_STATUS_TO_AIRTABLE = {
    "draft": "draft",
    "sent_to_suppliers": "dispatched",
    "awaiting_responses": "dispatched",
    "responses_received": "responses_received",
    "quote_generated": "quoted",
    "quote_sent": "quoted",
    "negotiating": "negotiating",
    "accepted": "closed",
    "closed": "closed",
}


def _ev(x) -> str:
    """Get string value of str Enum or plain string safely."""
    return x.value if hasattr(x, "value") else (x or "")


# ── Airtable singleSelect value maps ──────────────────────────────────────────
# Domain values → Airtable option names (None = omit the field entirely)

_AT_PRODUCT_TYPE: dict[str, Optional[str]] = {
    "sawn_timber":      "timber",
    "panelling":        "moulding",
    "flooring":         "flooring",
    "ready_furniture":  None,
}

_AT_WOOD_SPECIES: dict[str, Optional[str]] = {
    "teak_a":       "teak",
    "teak_b":       "teak",
    "teak_c":       "teak",
    "mahogany":     "mahogany",
    "meranti":      "meranti",
    "terrambese":   None,
    "berangkai":    None,
    "plywood":      None,
    "veneer":       None,
    "mdf":          None,
    "other":        None,
}

_AT_QUALITY_GRADE: dict[str, Optional[str]] = {
    "A": "A", "B": "B", "C": "C", "N/A": None,
}

_AT_QTY_UNIT: dict[str, Optional[str]] = {
    "pieces": "pieces", "cbm": "cbm", "sets": None,
}

_AT_NEG_STATUS: dict[str, Optional[str]] = {
    "feasible":            "FEASIBLE",
    "infeasible":          "INFEASIBLE",
    "round_limit_reached": "ROUND_LIMIT_REACHED",
    "pending":             None,
    "accepted":            None,
}


class AirtableStore(ContextStore):
    """
    Stores each WorkflowContext as a JSON blob in the RFQs.context_json field.
    Also writes human-readable fields into LineItems, SupplierQuotes, and
    NegotiationRounds tables so the Airtable base is a useful live view.
    """

    def __init__(self, airtable_client):
        self._at = airtable_client
        self._record_id_cache: dict[str, str] = {}  # rfq_id → airtable record id
        # Track what's already been written to subsidiary tables (avoids duplicate writes)
        self._line_items_written: set[str] = set()      # line_item.id
        self._quotes_written: set[str] = set()           # composite key
        self._rounds_written: set[str] = set()           # negotiation_round.id

    def _get_airtable_record_id(self, rfq_id: str) -> Optional[str]:
        if rfq_id in self._record_id_cache:
            return self._record_id_cache[rfq_id]
        records = self._at.list_records(
            "RFQs",
            filter_formula=f"{{RFQ ID}}='{rfq_id}'",
            max_records=1,
        )
        if records:
            rec_id = records[0]["id"]
            self._record_id_cache[rfq_id] = rec_id
            return rec_id
        return None

    # ── RFQ (main blob) ────────────────────────────────────────────────────────

    def save(self, rfq_id: str, ctx) -> None:
        # 1. Write the main JSON blob + human fields to RFQs (critical — re-raises on failure)
        try:
            blob = json.dumps(_ctx_to_dict(ctx))
            rfq = ctx.rfq

            human_fields: dict = {
                "RFQ ID": rfq.id,
                "Status": _RFQ_STATUS_TO_AIRTABLE.get(_ev(rfq.status), "draft"),
                "Buyer Name": rfq.buyer_name,
                "Destination Country": rfq.destination_country or "",
                "Destination Port": rfq.destination_port or "",
                "Origin Port": rfq.origin_port or "",
                "Preferred Currency": _ev(rfq.preferred_currency) or "USD",
                "Negotiation Rounds": rfq.negotiation_rounds,
                "context_json": blob,
            }
            if rfq.buyer_email:
                human_fields["Buyer Email"] = rfq.buyer_email
            if rfq.buyer_whatsapp:
                human_fields["Buyer WhatsApp"] = rfq.buyer_whatsapp

            existing_id = self._get_airtable_record_id(rfq_id)
            if existing_id:
                self._at.update_record("RFQs", existing_id, human_fields)
            else:
                from datetime import datetime, timezone
                human_fields["Created At"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                record = self._at.create_record("RFQs", human_fields)
                self._record_id_cache[rfq_id] = record["id"]

        except Exception as exc:
            logger.error("AirtableStore.save failed for %s: %s", rfq_id, exc)
            raise

        # 2. Populate subsidiary tables (best-effort — failures logged, not raised)
        try:
            self._upsert_line_items(rfq_id, ctx.rfq)
        except Exception as exc:
            logger.warning("LineItems upsert failed for %s: %s", rfq_id, exc)
        try:
            self._upsert_supplier_quotes(rfq_id, ctx)
        except Exception as exc:
            logger.warning("SupplierQuotes upsert failed for %s: %s", rfq_id, exc)
        try:
            self._upsert_negotiation_rounds(rfq_id, ctx.negotiation_rounds)
        except Exception as exc:
            logger.warning("NegotiationRounds upsert failed for %s: %s", rfq_id, exc)

    # ── LineItems ──────────────────────────────────────────────────────────────

    def _upsert_line_items(self, rfq_id: str, rfq) -> None:
        for li in rfq.line_items:
            if li.id in self._line_items_written:
                continue
            # On restart, check Airtable before creating
            existing = self._at.list_records(
                "LineItems",
                filter_formula=f"{{Line Item ID}}='{li.id}'",
                max_records=1,
            )
            if existing:
                self._line_items_written.add(li.id)
                continue

            dims = li.dimensions
            pt  = _AT_PRODUCT_TYPE.get(_ev(li.product_type))
            sp  = _AT_WOOD_SPECIES.get(_ev(li.wood_species))
            qg  = _AT_QUALITY_GRADE.get(_ev(li.quality_grade))
            qu  = _AT_QTY_UNIT.get(_ev(li.quantity_unit))

            fields: dict = {
                "Line Item ID": li.id,
                "RFQ ID": rfq_id,
                "Length": dims.length,
                "Width": dims.width,
                "Height": dims.height,
                "Dimension Unit": _ev(dims.unit),   # m/ft/cm/mm match exactly
                "Quantity": li.quantity,
                "Container Size": _ev(li.container_size),  # 20ft/40ft match exactly
            }
            if pt is not None:
                fields["Product Type"] = pt
            if sp is not None:
                fields["Wood Species"] = sp
            if qg is not None:
                fields["Quality Grade"] = qg
            if qu is not None:
                fields["Quantity Unit"] = qu
            if li.expected_rate is not None:
                fields["Expected Rate"] = li.expected_rate
            if li.expected_rate_currency:
                fields["Expected Rate Currency"] = _ev(li.expected_rate_currency)

            try:
                self._at.create_record("LineItems", fields)
                self._line_items_written.add(li.id)
            except Exception as exc:
                logger.warning("Failed to create LineItem %s: %s", li.id, exc)

    # ── SupplierQuotes ─────────────────────────────────────────────────────────

    def _upsert_supplier_quotes(self, rfq_id: str, ctx) -> None:
        import hashlib
        from datetime import datetime, timezone

        rfq_line_ids = [li.id for li in ctx.rfq.line_items]

        try:
            from src.calculations.fx import get_fx_rates
            fx = get_fx_rates()
        except Exception:
            fx = None

        for parsed in ctx.supplier_quotes:
            for i, item in enumerate(parsed.line_items):
                # Match line item by position (same logic as _parsed_to_domain)
                if rfq_line_ids:
                    rfq_line_id = rfq_line_ids[i] if i < len(rfq_line_ids) else rfq_line_ids[-1]
                else:
                    rfq_line_id = "unknown"

                quote_key = f"{parsed.supplier_id}:{rfq_line_id}"
                if quote_key in self._quotes_written:
                    continue

                quote_id = "SQ-" + hashlib.md5(quote_key.encode()).hexdigest()[:12]
                existing = self._at.list_records(
                    "SupplierQuotes",
                    filter_formula=f"{{Quote ID}}='{quote_id}'",
                    max_records=1,
                )
                if existing:
                    self._quotes_written.add(quote_key)
                    continue

                # Compute normalised USD/CBM price
                price_usd_per_cbm = None
                if fx and item.price_unit == "cbm":
                    try:
                        price_usd_per_cbm = round(
                            fx.to_usd(item.price_per_unit, str(item.price_currency).upper()), 4
                        )
                    except Exception:
                        pass

                fields: dict = {
                    "Quote ID": quote_id,
                    "RFQ ID": rfq_id,
                    "Line Item ID": rfq_line_id,
                    "Supplier ID": parsed.supplier_id,
                    "Price Per Unit": item.price_per_unit,
                    "Price Unit": item.price_unit or "cbm",
                    "Raw Response": (parsed.raw_text or "")[:2000],
                    "Received At": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                }
                if item.price_currency:
                    fields["Price Currency"] = str(item.price_currency).upper()
                if price_usd_per_cbm is not None:
                    fields["Price USD per CBM"] = price_usd_per_cbm
                if item.quality_grade:
                    # Only write recognised grade values to avoid 422 on singleSelect
                    grade = str(item.quality_grade).strip()
                    if grade in ("A", "B", "C", "N/A"):
                        fields["Quality Grade"] = grade
                if item.lead_time_days is not None:
                    fields["Lead Time Days"] = item.lead_time_days

                try:
                    self._at.create_record("SupplierQuotes", fields)
                    self._quotes_written.add(quote_key)
                except Exception as exc:
                    logger.warning("Failed to create SupplierQuote %s: %s", quote_key, exc)

    # ── NegotiationRounds ──────────────────────────────────────────────────────

    def _upsert_negotiation_rounds(self, rfq_id: str, rounds: list) -> None:
        for r in rounds:
            round_id = getattr(r, "id", None)
            if not round_id:
                continue
            if round_id in self._rounds_written:
                continue

            existing = self._at.list_records(
                "NegotiationRounds",
                filter_formula=f"{{Round ID}}='{round_id}'",
                max_records=1,
            )
            if existing:
                self._rounds_written.add(round_id)
                continue

            at_status = _AT_NEG_STATUS.get(_ev(getattr(r, "status", "")))
            fields: dict = {
                "Round ID": round_id,
                "RFQ ID": rfq_id,
                "Round Number": r.round_number,
                "Buyer Target Rate": r.buyer_target_rate_usd,
                "Currency": "USD",
                "Minimum Achievable USD": r.minimum_achievable_usd,
            }
            if at_status is not None:
                fields["Status"] = at_status
            if getattr(r, "notes", None):
                fields["Notes"] = r.notes[:2000]

            try:
                self._at.create_record("NegotiationRounds", fields)
                self._rounds_written.add(round_id)
            except Exception as exc:
                logger.warning("Failed to create NegotiationRound %s: %s", round_id, exc)

    # ── load / delete ──────────────────────────────────────────────────────────

    def load(self, rfq_id: str):
        try:
            records = self._at.list_records(
                "RFQs",
                filter_formula=f"{{RFQ ID}}='{rfq_id}'",
                max_records=1,
            )
            if not records:
                return None

            rec = records[0]
            self._record_id_cache[rfq_id] = rec["id"]
            blob = rec["fields"].get("context_json")
            if not blob:
                logger.warning("RFQ %s found in Airtable but context_json is empty", rfq_id)
                return None

            return _dict_to_ctx(json.loads(blob))

        except Exception as exc:
            logger.error("AirtableStore.load failed for %s: %s", rfq_id, exc)
            return None

    def delete(self, rfq_id: str) -> None:
        # We don't hard-delete — just clear context_json to free space
        try:
            existing_id = self._get_airtable_record_id(rfq_id)
            if existing_id:
                self._at.update_record("RFQs", existing_id, {"context_json": ""})
                self._record_id_cache.pop(rfq_id, None)
        except Exception as exc:
            logger.warning("AirtableStore.delete failed for %s: %s", rfq_id, exc)


# ── Factory ────────────────────────────────────────────────────────────────────

_store: Optional[ContextStore] = None


def get_context_store() -> ContextStore:
    """
    Return the singleton store.
    Uses AirtableStore when AIRTABLE_API_KEY + AIRTABLE_BASE_ID are set,
    otherwise falls back to InMemoryStore with a warning.
    """
    global _store
    if _store is not None:
        return _store

    if os.getenv("AIRTABLE_API_KEY") and os.getenv("AIRTABLE_BASE_ID"):
        from src.integrations.airtable import get_airtable_client
        at = get_airtable_client()
        if at:
            logger.info("ContextStore: using AirtableStore (persistent)")
            _store = AirtableStore(at)
            return _store

    logger.warning(
        "ContextStore: AIRTABLE_API_KEY/BASE_ID not set — "
        "using InMemoryStore (state lost on restart)"
    )
    _store = InMemoryStore()
    return _store


def reset_store() -> None:
    """Reset singleton — used in tests."""
    global _store
    _store = None
