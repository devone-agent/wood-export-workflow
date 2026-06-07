"""
Application settings — loaded from environment variables.
All secrets must be set in .env (never committed to git).
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Settings:
    # ── Core ──────────────────────────────────────────────────────────────────
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    markup_pct: float = field(default_factory=lambda: float(os.getenv("MARKUP_PCT", "0.03")))
    max_negotiation_rounds: int = field(default_factory=lambda: int(os.getenv("MAX_NEGOTIATION_ROUNDS", "3")))

    # ── Anthropic (for AI parsing) ────────────────────────────────────────────
    anthropic_api_key: Optional[str] = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))

    # ── Email — Hostinger SMTP + IMAP ─────────────────────────────────────────
    email_host: str = field(default_factory=lambda: os.getenv("EMAIL_HOST", "smtp.hostinger.com"))
    email_port: int = field(default_factory=lambda: int(os.getenv("EMAIL_PORT", "587")))
    email_user: Optional[str] = field(default_factory=lambda: os.getenv("EMAIL_USER"))
    email_password: Optional[str] = field(default_factory=lambda: os.getenv("EMAIL_PASSWORD"))
    email_sender_name: str = field(default_factory=lambda: os.getenv("EMAIL_SENDER_NAME", "Wood Export Bot"))
    imap_host: str = field(default_factory=lambda: os.getenv("IMAP_HOST", "imap.hostinger.com"))
    imap_port: int = field(default_factory=lambda: int(os.getenv("IMAP_PORT", "993")))
    imap_poll_interval: int = field(default_factory=lambda: int(os.getenv("IMAP_POLL_INTERVAL", "60")))

    # ── Twilio / WhatsApp ─────────────────────────────────────────────────────
    twilio_account_sid: Optional[str] = field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID"))
    twilio_auth_token: Optional[str] = field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN"))
    twilio_whatsapp_from: Optional[str] = field(default_factory=lambda: os.getenv("TWILIO_WHATSAPP_FROM"))

    # ── Airtable ──────────────────────────────────────────────────────────────
    airtable_api_key: Optional[str] = field(default_factory=lambda: os.getenv("AIRTABLE_API_KEY"))
    airtable_base_id: Optional[str] = field(default_factory=lambda: os.getenv("AIRTABLE_BASE_ID"))

    # ── FX API ────────────────────────────────────────────────────────────────
    fx_cache_ttl_seconds: int = field(default_factory=lambda: int(os.getenv("FX_CACHE_TTL", "3600")))

    def is_production(self) -> bool:
        return self.app_env == "production"

    def missing_integrations(self) -> list[str]:
        missing = []
        if not self.email_user or not self.email_password:
            missing.append("Email (EMAIL_USER, EMAIL_PASSWORD)")
        if not all([self.twilio_account_sid, self.twilio_auth_token, self.twilio_whatsapp_from]):
            missing.append("WhatsApp/Twilio (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM)")
        if not self.airtable_api_key or not self.airtable_base_id:
            missing.append("Airtable (AIRTABLE_API_KEY, AIRTABLE_BASE_ID)")
        if not self.anthropic_api_key:
            missing.append("Anthropic AI parsing (ANTHROPIC_API_KEY)")
        return missing


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
