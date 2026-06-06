"""
FastAPI application entry point.

Provides:
  POST /rfq               — Step 1: Submit buyer demand form
  GET  /rfq/{id}          — Get RFQ status
  POST /rfq/{id}/quote    — Step 5: Trigger quote generation (after suppliers respond)
  POST /rfq/{id}/negotiate — Step 6: Submit buyer counter-offer
  POST /webhooks/whatsapp — Inbound WhatsApp supplier replies
  POST /webhooks/email    — Inbound Gmail push notifications
  GET  /health            — Health check
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import buyer, webhook, health

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Wood Export Bot starting up")
    yield
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
