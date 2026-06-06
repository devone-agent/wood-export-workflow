# Wood Export Bot — Indonesia → India

Automated export workflow bot for wood products. Handles the full cycle from buyer demand capture through supplier RFQ dispatch, response parsing, rate comparison, quote generation, and negotiation.

## Architecture

```
Buyer Form (Step 1)
    ↓
Supplier RFQ Dispatch (Step 2) ── Email (Gmail API) + WhatsApp (Twilio)
    ↓
Supplier Response Ingestion (Step 3) ── Webhooks (/webhooks/whatsapp, /webhooks/email)
    ↓
Parse + Normalise (Step 3) ── Claude Haiku AI extraction + regex fallback
    ↓
Comparison Matrix (Step 4) ── Rate table, media gallery, quality scoring
    ↓
Buyer Quote Generation (Step 5) ── Best price + 3% markup, multi-currency
    ↓
Negotiation Loop (Step 6) ── Max 3 rounds, floor price enforcement
    ↓
Calculations Engine (Step 7) ── CBM, FX rates, unit conversion
    ↓
Freight Footnote (Step 8) ── Always separate, never in unit price
```

## Quick Start

```bash
cp .env.example .env
# Fill in your credentials in .env

pip install -r requirements.txt
uvicorn api.main:app --reload
```

API available at `http://localhost:8000`. Docs at `http://localhost:8000/docs`.

## Key API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/rfq` | Step 1 — Submit buyer demand form |
| POST | `/rfq/{id}/dispatch` | Step 2 — Send RFQs to suppliers |
| GET | `/rfq/{id}` | Get RFQ status |
| POST | `/rfq/{id}/quote` | Step 5 — Generate buyer quote |
| POST | `/rfq/{id}/negotiate` | Step 6 — Submit counter-offer |
| POST | `/webhooks/whatsapp` | Inbound WhatsApp (Twilio) |
| POST | `/webhooks/email` | Inbound Gmail (Pub/Sub) |

## External Integrations Required

| Service | Purpose | Config |
|---------|---------|--------|
| **Gmail API** | Send/receive email RFQs | `GMAIL_CREDENTIALS`, `GMAIL_SENDER` |
| **Twilio WhatsApp** | Send/receive WhatsApp messages | `TWILIO_*` vars |
| **Airtable** | Supplier DB + RFQ state | `AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID` |
| **Anthropic API** | AI response parsing | `ANTHROPIC_API_KEY` |
| **FX Rate API** | Live exchange rates (free) | No key required |

## Running Tests

```bash
PYTHONPATH=. pytest tests/ -v
```

24 tests covering: CBM/unit conversion, FX rates, pricing engine, markup, negotiation logic.

## Project Structure

```
src/
  models/          # RFQ, Supplier, Negotiation domain models (Pydantic)
  calculations/    # Units, FX, pricing — pure logic, no side effects
  buyer/           # Form validation, quote formatting
  supplier/        # RFQ builder, dispatcher, parser, comparator
  negotiation/     # Negotiation engine (floor check, round limit)
  freight/         # Freight footnote builder
  integrations/    # Gmail, WhatsApp, Airtable clients
  orchestrator.py  # Main workflow state machine
api/
  main.py          # FastAPI app
  routes/          # buyer, webhook, health
config/
  settings.py      # Environment-based config
tests/             # pytest test suite
```
