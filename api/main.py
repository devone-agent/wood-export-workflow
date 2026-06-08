"""
FastAPI application entry point.

Provides:
  POST /rfq               — Step 1: Submit buyer demand form
  GET  /rfq/{id}          — Get RFQ status
  POST /rfq/{id}/quote    — Step 5: Trigger quote generation (after suppliers respond)
  POST /rfq/{id}/negotiate — Step 6: Submit buyer counter-offer
  POST /webhooks/whatsapp — Inbound WhatsApp supplier replies
  POST /webhooks/email    — Inbound email (forwarded/webhook)
  GET  /health            — Health check

Background tasks:
  IMAP polling loop — checks pav@instructset.com every 60s for new supplier replies
"""
import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager

# Load .env before anything else
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from api.routes import buyer, webhook, health, supplier_quote

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

IMAP_POLL_INTERVAL = int(os.getenv("IMAP_POLL_INTERVAL", "60"))  # seconds

# ── Startup env diagnostics (values masked) ───────────────────────────────────
_REQUIRED_VARS = [
    "RESEND_API_KEY", "EMAIL_USER", "OPERATOR_EMAIL",
    "IMAP_HOST", "IMAP_PORT",
    "AIRTABLE_API_KEY", "AIRTABLE_BASE_ID",
    "SECRET_KEY", "SERVER_BASE_URL",
]
def _log_env_check():
    for var in _REQUIRED_VARS:
        val = os.getenv(var)
        if val:
            masked = val[:4] + "****" if len(val) > 4 else "****"
            logger.info("ENV %s = %s", var, masked)
        else:
            logger.warning("ENV %s = NOT SET", var)


async def _imap_poll_loop():
    """
    Background task: poll IMAP inbox every IMAP_POLL_INTERVAL seconds.
    Any UNSEEN message is treated as a supplier reply and ingested.
    """
    from src.integrations.email import get_imap_client
    from src.supplier.parser import parse_supplier_response
    from src.store import get_context_store

    imap = get_imap_client()
    if not imap:
        logger.warning("IMAP not configured — skipping background email polling")
        return

    logger.info("IMAP poll loop started (interval=%ds)", IMAP_POLL_INTERVAL)

    while True:
        try:
            messages = imap.poll_new_replies()
            for msg in messages:
                sender = msg["from"]
                body = msg["body_text"]
                if not body.strip():
                    continue

                logger.info("IMAP: new reply from %s — ingesting", sender)

                # Find the active RFQ for this sender
                rfq_id, ctx = webhook._find_rfq_for_supplier(sender)
                if not ctx:
                    logger.warning("IMAP: no active RFQ for %s", sender)
                    continue

                match = re.search(r"[\w.+-]+@[\w-]+\.[a-z]+", sender)
                supplier_id = match.group(0) if match else sender

                ctx = webhook._ingest(ctx, body, supplier_id, "email")
                get_context_store().save(rfq_id, ctx)
                logger.info("IMAP: ingested reply from %s into RFQ %s", supplier_id, rfq_id)

                await webhook._auto_quote_if_ready(rfq_id, ctx)

        except Exception as exc:
            logger.error("IMAP poll error: %s", exc)

        await asyncio.sleep(IMAP_POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Wood Export Bot starting up")
    _log_env_check()
    task = asyncio.create_task(_imap_poll_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("Wood Export Bot shutting down")


app = FastAPI(
    title="Wood Export Bot",
    description="Indonesia → India wood export workflow automation",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(buyer.router, prefix="/rfq", tags=["RFQ"])
app.include_router(webhook.router, prefix="/webhooks", tags=["Webhooks"])
app.include_router(supplier_quote.router, prefix="/supplier-quote", tags=["Supplier"])

# ── Buyer form (same-origin — no CORS needed) ──────────────────────────────────
_BUYER_FORM = Path(__file__).parent.parent / "buyer_form.html"


@app.get("/form", include_in_schema=False)
async def buyer_form():
    return FileResponse(_BUYER_FORM, media_type="text/html")
