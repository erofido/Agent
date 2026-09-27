"""HTTP server: Telegram, Twilio and Retell webhooks.

Run locally:  uvicorn agent.main:app --port 8000
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from retell.lib.webhook_auth import verify as retell_verify
from twilio.request_validator import RequestValidator

from .brain import Brain
from .calls import CallManager
from .config import Settings, load_settings
from .db import DB
from .telegram import Telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agent")

HELP = (
    "Hi! Just tell me what you need, e.g.\n"
    "• “Call Luigi's and book a table for 4 on Friday 20:00”\n"
    "• “Remember my dentist is Dr. Yilmaz, +90 212 …”\n\n"
    "Commands: /reset (forget this conversation), /memories"
)


class App:
    def __init__(self, settings: Settings):
        self.s = settings
        self.db = DB(settings.db_path)
        self.tg = Telegram(settings.telegram_bot_token)
        self.brain = Brain(settings, self.db, self.tg)
        self.calls: CallManager | None = None
        if settings.calls_enabled:
            self.calls = CallManager(settings, self.db, self.tg, on_event=self.brain.handle_event)
            self.brain.calls = self.calls
        self.twilio_validator = RequestValidator(settings.twilio_auth_token)
        self._tasks: set[asyncio.Task[Any]] = set()

    def handle_update(self, update: dict[str, Any]) -> None:
        """Route one Telegram update (from the webhook or from polling)."""
        if cb := update.get("callback_query"):
            if cb["from"]["id"] != self.s.telegram_owner_id:
                return
            msg = cb.get("message") or {}
            chat_id, message_id = msg["chat"]["id"], msg["message_id"]
            data = cb.get("data", "")

            async def handle_cb() -> None:
                toast = ""
                if data.startswith("call:") and self.calls:
                    toast = await self.calls.handle_button(chat_id, message_id, data)
                await self.tg.answer_callback(cb["id"], toast)

            self.spawn(handle_cb())
            return

        msg = update.get("message")
        if not msg or not msg.get("text"):
            return
        if msg["from"]["id"] != self.s.telegram_owner_id:
            log.warning("Ignoring message from non-owner %s", msg["from"]["id"])
            return

        chat_id, text = msg["chat"]["id"], msg["text"].strip()
        reply_to = (msg.get("reply_to_message") or {}).get("message_id")

        if self.calls and self.calls.resolve_owner_reply(text, reply_to):
            self.spawn(self.tg.send_message(chat_id, "👍 Passed on to the call."))
        elif text in ("/start", "/help"):
            self.spawn(self.tg.send_message(chat_id, HELP))
        elif text == "/reset":
            self.db.clear_history(chat_id)
            self.spawn(self.tg.send_message(chat_id, "Conversation cleared (memories and contacts kept)."))
        elif text == "/memories":
            mems = self.db.list_memories()
            self.spawn(self.tg.send_message(chat_id, "\n".join(f"[{m['id']}] {m['text']}" for m in mems) or "No memories yet."))
        else:
            self.spawn(self.brain.handle_user_message(chat_id, text))

    async def poll_telegram(self) -> None:
        """Without a public URL, fetch messages from Telegram instead of receiving webhooks."""
        offset = 0
        while True:
            try:
                for update in await self.tg.get_updates(offset):
                    offset = update["update_id"] + 1
                    self.handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Telegram polling failed; retrying in 5s")
                await asyncio.sleep(5)

    def spawn(self, coro: Any) -> None:
        """Run work in the background so webhooks return immediately."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(_log_failure)


def _log_failure(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and task.exception():
        log.error("Background task failed", exc_info=task.exception())


def create_app(settings: Settings | None = None) -> FastAPI:
    state: dict[str, App] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        s = settings or load_settings()
        a = state["app"] = App(s)
        poller: asyncio.Task[None] | None = None
        if s.public_base_url:
            try:
                await a.tg.set_webhook(f"{s.public_base_url}/telegram/webhook", s.telegram_webhook_secret)
                log.info("Telegram webhook set to %s/telegram/webhook", s.public_base_url)
            except Exception:
                log.exception("Could not set Telegram webhook (is PUBLIC_BASE_URL reachable?)")
        else:
            await a.tg.delete_webhook()
            poller = asyncio.create_task(a.poll_telegram())
            log.info("No PUBLIC_BASE_URL: polling Telegram for messages")
        log.info(
            "Brain: %s. Phone calls: %s. Web search: %s.",
            a.brain.backend.provider,
            "on" if a.calls else "off (needs PUBLIC_BASE_URL + Twilio + Retell)",
            "on" if s.brave_api_key or s.brain_provider == "anthropic" else "off (no BRAVE_API_KEY)",
        )
        yield
        if poller:
            poller.cancel()

    api = FastAPI(lifespan=lifespan)

    def app() -> App:
        return state["app"]

    @api.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- Telegram ---------------------------------------------------------------

    @api.post("/telegram/webhook")
    async def telegram_webhook(request: Request) -> dict[str, bool]:
        a = app()
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != a.s.telegram_webhook_secret:
            raise HTTPException(status_code=403)
        a.handle_update(await request.json())
        return {"ok": True}

    # --- Twilio -----------------------------------------------------------------

    def calls() -> CallManager:
        if app().calls is None:
            raise HTTPException(status_code=404, detail="Phone calls are not configured")
        return app().calls

    @api.post("/twilio/status/{call_id}")
    async def twilio_status(call_id: str, request: Request) -> dict[str, bool]:
        a = app()
        manager = calls()
        form = dict(await request.form())
        url = f"{a.s.public_base_url}{request.url.path}"
        if not a.twilio_validator.validate(url, form, request.headers.get("X-Twilio-Signature", "")):
            raise HTTPException(status_code=403)
        a.spawn(manager.handle_twilio_status(call_id, str(form.get("CallStatus", ""))))
        return {"ok": True}

    # --- Retell -----------------------------------------------------------------

    async def _verified_retell_json(request: Request) -> dict[str, Any]:
        body = (await request.body()).decode()
        signature = request.headers.get("X-Retell-Signature", "")
        if not signature or not retell_verify(body, app().s.retell_api_key, signature):
            raise HTTPException(status_code=403)
        return await request.json()

    @api.post("/retell/webhook")
    async def retell_webhook(request: Request) -> dict[str, bool]:
        manager = calls()
        payload = await _verified_retell_json(request)
        app().spawn(manager.handle_retell_event(payload))
        return {"ok": True}

    @api.post("/retell/ask-owner")
    async def retell_ask_owner(request: Request) -> dict[str, str]:
        """Retell custom function: the voice agent asks you something and waits for your reply."""
        manager = calls()
        payload = await _verified_retell_json(request)
        call_id = (payload.get("call") or {}).get("call_id", "")
        question = (payload.get("args") or {}).get("question", "")
        answer = await manager.ask_owner(call_id, question)
        return {"result": answer}

    return api


app = create_app()
