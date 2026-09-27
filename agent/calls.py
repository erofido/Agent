"""Outbound phone calls placed from YOUR number.

Flow:
  1. Claude calls the `place_call` tool -> we store the call and ask you to approve it on Telegram.
  2. You tap "Call" -> we register the call with Retell (the voice AI) and get a Retell call id.
  3. Twilio dials the other person with `from = your number` (a Twilio verified caller ID),
     and once they pick up it bridges the audio to Retell over SIP.
  4. Retell POSTs the transcript + summary to /retell/webhook when the call is over -> we tell Claude,
     Claude texts you the outcome.
  5. During the call the voice agent can use the `ask_owner` function to text you a question
     and wait for your Telegram reply.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from xml.sax.saxutils import escape

from retell import AsyncRetell
from twilio.rest import Client as TwilioClient

from .config import Settings
from .db import DB
from .telegram import Telegram

log = logging.getLogger(__name__)

# Called with (chat_id, event_text) so the brain can react to call outcomes.
EventSink = Callable[[int, str], Awaitable[None]]

FAILED_TWILIO_STATUSES = {"busy", "no-answer", "failed", "canceled"}


@dataclass
class PendingQuestion:
    call_id: str
    question: str
    telegram_message_id: int
    future: asyncio.Future[str] = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class CallManager:
    def __init__(self, settings: Settings, db: DB, telegram: Telegram, on_event: EventSink):
        self.s = settings
        self.db = db
        self.tg = telegram
        self.on_event = on_event
        self.retell = AsyncRetell(api_key=settings.retell_api_key)
        self.twilio = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
        self.questions: list[PendingQuestion] = []

    # --- 1. request approval ---------------------------------------------------

    async def request_call(
        self, chat_id: int, to_number: str, contact_name: str, goal: str, brief: str, language: str
    ) -> str:
        call_id = self.db.create_call(chat_id, contact_name, to_number, goal, brief, language)
        text = (
            f"📞 Call request\n\n"
            f"To: {contact_name} ({to_number})\n"
            f"Shows as: {self.s.owner_phone} (your number)\n"
            f"Language: {language}\n"
            f"Goal: {goal}\n\n"
            f"Brief for the voice agent:\n{brief}"
        )
        await self.tg.send_message(
            chat_id, text, buttons=[[("✅ Call now", f"call:go:{call_id}"), ("❌ Cancel", f"call:no:{call_id}")]]
        )
        return call_id

    async def handle_button(self, chat_id: int, message_id: int, data: str) -> str:
        """Handle an inline-button press. Returns a short toast text."""
        _, action, call_id = data.split(":", 2)
        call = self.db.get_call(call_id)
        if not call or call["chat_id"] != chat_id:
            return "Unknown call"
        if call["status"] != "awaiting_approval":
            return f"Already {call['status']}"
        if action == "no":
            self.db.update_call(call_id, status="cancelled")
            await self.tg.edit_message(chat_id, message_id, f"❌ Cancelled call to {call['contact_name']}.")
            await self.on_event(chat_id, f"Owner cancelled the call to {call['contact_name']} (call id {call_id}).")
            return "Cancelled"
        await self.tg.edit_message(chat_id, message_id, f"📞 Calling {call['contact_name']} ({call['to_number']})…")
        try:
            await self.start_call(call_id)
        except Exception as exc:  # surface any provider error to the owner
            log.exception("Failed to start call %s", call_id)
            self.db.update_call(call_id, status="failed", summary=f"Could not start call: {exc}")
            await self.on_event(chat_id, f"Call {call_id} to {call['contact_name']} could not be started: {exc}")
            return "Call failed"
        return "Calling"

    # --- 2 + 3. place the call -------------------------------------------------

    async def start_call(self, call_id: str) -> None:
        call = self.db.get_call(call_id)
        assert call is not None
        registered = await self.retell.call.register_phone_call(
            agent_id=self.s.retell_agent_id,
            direction="outbound",
            from_number=self.s.owner_phone,
            to_number=call["to_number"],
            metadata={"local_call_id": call_id},
            retell_llm_dynamic_variables={
                "owner_name": self.s.owner_name,
                "owner_phone": self.s.owner_phone,
                "contact_name": call["contact_name"],
                "goal": call["goal"],
                "call_brief": call["brief"],
                "language": call["language"],
            },
        )
        retell_call_id = registered.call_id
        self.db.update_call(call_id, status="dialing", retell_call_id=retell_call_id)

        # Retell requires the SIP dial within 5 minutes of registering; Twilio runs this TwiML
        # when the other person answers.
        sip_uri = escape(f"sip:{retell_call_id}@sip.retellai.com")
        twiml = f"<Response><Dial><Sip>{sip_uri}</Sip></Dial></Response>"
        twilio_call = await asyncio.to_thread(
            self.twilio.calls.create,
            to=call["to_number"],
            from_=self.s.owner_phone,  # your own number, verified as a caller ID in Twilio
            twiml=twiml,
            status_callback=f"{self.s.public_base_url}/twilio/status/{call_id}",
            status_callback_event=["completed"],
            timeout=40,
        )
        self.db.update_call(call_id, twilio_sid=twilio_call.sid)

    # --- 4. outcomes -----------------------------------------------------------

    async def handle_twilio_status(self, call_id: str, status: str) -> None:
        call = self.db.get_call(call_id)
        if not call:
            return
        if status in FAILED_TWILIO_STATUSES and call["status"] in {"dialing", "in_progress"}:
            self.db.update_call(call_id, status=status)
            await self.on_event(
                call["chat_id"],
                f"Call {call_id} to {call['contact_name']} ({call['to_number']}) did not connect: {status}.",
            )

    async def handle_retell_event(self, payload: dict[str, Any]) -> None:
        event = payload.get("event")
        rc = payload.get("call") or {}
        local_id = (rc.get("metadata") or {}).get("local_call_id")
        call = self.db.get_call(local_id) if local_id else self.db.get_call_by_retell_id(rc.get("call_id", ""))
        if not call:
            log.warning("Retell event for unknown call: %s", rc.get("call_id"))
            return
        if event == "call_started":
            self.db.update_call(call["id"], status="in_progress")
        elif event == "call_analyzed":
            analysis = rc.get("call_analysis") or {}
            summary = analysis.get("call_summary") or ""
            transcript = rc.get("transcript") or ""
            self.db.update_call(call["id"], status="completed", summary=summary, transcript=transcript)
            self._drop_questions(call["id"])
            await self.on_event(
                call["chat_id"],
                f"Call {call['id']} to {call['contact_name']} finished.\n"
                f"Goal was: {call['goal']}\n"
                f"Disconnection reason: {rc.get('disconnection_reason', 'unknown')}\n"
                f"Call successful (Retell's judgement): {analysis.get('call_successful', 'unknown')}\n"
                f"Summary: {summary or '(none)'}\n\nTranscript:\n{transcript[:6000] or '(empty)'}",
            )

    # --- 5. ask the owner mid-call --------------------------------------------

    async def ask_owner(self, retell_call_id: str, question: str) -> str:
        call = self.db.get_call_by_retell_id(retell_call_id)
        if not call:
            return "No answer available. Do not commit to anything; say the owner will follow up."
        msg_id = await self.tg.send_message(
            call["chat_id"],
            f"❓ On the call with {call['contact_name']}:\n{question}\n\n"
            f"Reply to this message within {self.s.ask_owner_timeout}s.",
        )
        pending = PendingQuestion(call_id=call["id"], question=question, telegram_message_id=msg_id)
        self.questions.append(pending)
        try:
            answer = await asyncio.wait_for(asyncio.shield(pending.future), timeout=self.s.ask_owner_timeout)
            return f"The owner answered: {answer}"
        except asyncio.TimeoutError:
            await self.tg.send_message(call["chat_id"], "⏱️ No reply in time — told them you'll follow up.")
            return "The owner did not reply in time. Do not commit to anything; say the owner will follow up."
        finally:
            if pending in self.questions:
                self.questions.remove(pending)

    def resolve_owner_reply(self, text: str, reply_to_message_id: int | None) -> bool:
        """If a question is waiting, treat this Telegram message as the answer."""
        if not self.questions:
            return False
        target = next((q for q in self.questions if q.telegram_message_id == reply_to_message_id), None)
        if target is None and reply_to_message_id is None:
            target = self.questions[-1]  # plain message while a question is open -> newest question
        if target is None or target.future.done():
            return False
        target.future.set_result(text)
        return True

    def _drop_questions(self, call_id: str) -> None:
        for q in list(self.questions):
            if q.call_id == call_id:
                if not q.future.done():
                    q.future.set_result("(call ended)")
                self.questions.remove(q)
