"""Settings loaded from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    # Brain (Telegram chats): "deepseek" or "anthropic"
    brain_provider: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    deepseek_reasoning_effort: str
    brave_api_key: str  # optional: web search for DeepSeek (Claude has its own)
    anthropic_model: str
    # Telegram
    telegram_bot_token: str
    telegram_owner_id: int
    telegram_webhook_secret: str
    # Where this server is reachable from the internet, e.g. https://my-agent.fly.dev
    public_base_url: str
    # You
    owner_name: str
    owner_phone: str  # your own number in E.164; must be a Twilio *verified caller ID*
    timezone: str
    # Twilio (places the call, shows your number as caller ID)
    twilio_account_sid: str
    twilio_auth_token: str
    # Retell (the voice AI that talks on the call)
    retell_api_key: str
    retell_agent_id: str
    # Misc
    db_path: str
    ask_owner_timeout: int  # seconds the voice agent waits for your Telegram answer mid-call


def load_settings() -> Settings:
    provider = os.environ.get("BRAIN_PROVIDER", "deepseek").strip().lower()
    if provider not in ("deepseek", "anthropic"):
        raise RuntimeError("BRAIN_PROVIDER must be 'deepseek' or 'anthropic'")
    if provider == "anthropic":
        _require("ANTHROPIC_API_KEY")  # read by the Anthropic SDK itself
    return Settings(
        brain_provider=provider,
        deepseek_api_key=_require("DEEPSEEK_API_KEY") if provider == "deepseek" else "",
        deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        deepseek_reasoning_effort=os.environ.get("DEEPSEEK_REASONING_EFFORT", "none"),
        brave_api_key=os.environ.get("BRAVE_API_KEY", "").strip(),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        telegram_owner_id=int(_require("TELEGRAM_OWNER_ID")),
        telegram_webhook_secret=_require("TELEGRAM_WEBHOOK_SECRET"),
        public_base_url=_require("PUBLIC_BASE_URL").rstrip("/"),
        owner_name=_require("OWNER_NAME"),
        owner_phone=_require("OWNER_PHONE"),
        timezone=os.environ.get("OWNER_TIMEZONE", "Europe/Istanbul"),
        twilio_account_sid=_require("TWILIO_ACCOUNT_SID"),
        twilio_auth_token=_require("TWILIO_AUTH_TOKEN"),
        retell_api_key=_require("RETELL_API_KEY"),
        retell_agent_id=_require("RETELL_AGENT_ID"),
        db_path=os.environ.get("DB_PATH", "agent.db"),
        ask_owner_timeout=int(os.environ.get("ASK_OWNER_TIMEOUT", "60")),
    )
