"""The agent's brain: tools, memory and per-chat history on top of a model backend (see llm.py)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import httpx

from . import web
from .config import Settings
from .db import DB
from .llm import AnthropicBackend, Backend, OpenAICompatBackend, Refused
from .telegram import Telegram

if TYPE_CHECKING:
    from .calls import CallManager

log = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 60
E164 = re.compile(r"^\+[1-9]\d{6,14}$")

SYSTEM_PROMPT = """\
You are {owner_name}'s personal assistant. {owner_name} talks to you through Telegram from their phone, \
usually in short messages. Get things done for them with minimal back-and-forth, and reply briefly in \
the language they write in.

Phone calls:
- You can call people and businesses on {owner_name}'s behalf with `place_call`. Calls go out from \
{owner_name}'s own number ({owner_phone}), so the other side sees their number. A separate voice agent \
does the talking, guided only by the brief you write — so make the brief complete: who is calling and \
for whom, the goal, facts it may share (name, dates, booking refs, callback number), acceptable \
options/limits, what it must never agree to, and what to do if the goal isn't possible.
- Only share personal details the task actually needs.
- Numbers must be E.164 (e.g. +905321234567, +4915112345678). If you don't have the number, check \
`find_contacts`, then search the web for businesses; ask {owner_name} for private people's numbers.
- `place_call` only asks {owner_name} for approval; the call happens after they tap the button. Don't \
claim a call was made until you get the automatic event with its outcome.
- When a call finishes you'll receive an automatic event with the summary and transcript. Report the \
outcome to {owner_name} in 1-3 lines and propose or take obvious next steps (save a new contact, remember \
an appointment, etc.).

Memory: use `remember` for durable facts and preferences {owner_name} tells you (addresses, \
preferences, appointments). Things you already know:
{memories}
"""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "place_call",
        "description": (
            "Request an outbound phone call from the owner's own number, handled by a voice agent. "
            "The owner must approve it on Telegram before it is dialed; the outcome arrives later as an event."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to_number": {"type": "string", "description": "Number to call in E.164 format, e.g. +905321234567"},
                "contact_name": {"type": "string", "description": "Who is being called, e.g. 'Dr. Yilmaz dental clinic'"},
                "goal": {"type": "string", "description": "One-line goal of the call"},
                "brief": {
                    "type": "string",
                    "description": (
                        "Complete instructions for the voice agent: context, facts it may share, acceptable "
                        "options and limits, what not to agree to, fallback if the goal isn't possible."
                    ),
                },
                "language": {"type": "string", "description": "Language to speak on the call, e.g. Turkish, German, English"},
            },
            "required": ["to_number", "contact_name", "goal", "brief", "language"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_recent_calls",
        "description": "List the most recent calls with their status and summary.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_call_details",
        "description": "Get the full brief, summary and transcript of a call by id.",
        "input_schema": {
            "type": "object",
            "properties": {"call_id": {"type": "string"}},
            "required": ["call_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "save_contact",
        "description": "Save or update a contact (name, E.164 phone number, optional notes).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["name", "phone"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_contacts",
        "description": "Search saved contacts by name, notes or number.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remember",
        "description": "Store a durable fact or preference about the owner for future conversations.",
        "input_schema": {
            "type": "object",
            "properties": {"fact": {"type": "string"}},
            "required": ["fact"],
            "additionalProperties": False,
        },
    },
    {
        "name": "forget",
        "description": "Delete a stored memory by its id.",
        "input_schema": {
            "type": "object",
            "properties": {"memory_id": {"type": "integer"}},
            "required": ["memory_id"],
            "additionalProperties": False,
        },
    },
]

# Only offered to backends without built-in web tools (DeepSeek); Claude uses its own.
WEB_SEARCH_TOOL: dict[str, Any] = {
    "name": "web_search",
    "description": "Search the web. Returns titles, URLs and snippets of the top results.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
}
FETCH_URL_TOOL: dict[str, Any] = {
    "name": "fetch_url",
    "description": "Fetch a web page and return its text, e.g. to read a business's opening hours or phone number.",
    "input_schema": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
        "additionalProperties": False,
    },
}


def make_backend(s: Settings) -> Backend:
    if s.brain_provider == "anthropic":
        return AnthropicBackend(s.anthropic_model)
    return OpenAICompatBackend(s.deepseek_model, s.deepseek_base_url, s.deepseek_api_key, s.deepseek_reasoning_effort)


class Brain:
    def __init__(self, settings: Settings, db: DB, telegram: Telegram, backend: Backend | None = None):
        self.s = settings
        self.db = db
        self.tg = telegram
        self.backend = backend or make_backend(settings)
        self.http = httpx.AsyncClient(timeout=20)
        self.calls: CallManager | None = None  # wired up in main.py
        self._locks: dict[int, asyncio.Lock] = {}

    # --- entry points ----------------------------------------------------------

    async def handle_user_message(self, chat_id: int, text: str) -> None:
        now = datetime.now(ZoneInfo(self.s.timezone)).strftime("%a %Y-%m-%d %H:%M %Z")
        await self._run_turn(chat_id, f"[{now}] {text}")

    async def handle_event(self, chat_id: int, text: str) -> None:
        await self._run_turn(chat_id, f"[Automatic event — not a message from the owner]\n{text}")

    # --- the loop --------------------------------------------------------------

    async def _run_turn(self, chat_id: int, user_content: str) -> None:
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            provider = self.backend.provider
            history = self.db.get_history(chat_id, provider)  # empty after switching provider
            snapshot = list(history)
            history.append({"role": "user", "content": user_content})
            try:
                await self.tg.send_typing(chat_id)
                reply = await self.backend.run(self._system_prompt(), history, self._tools(), self._tool_runner(chat_id))
            except Refused:
                self.db.save_history(chat_id, provider, snapshot)  # roll back so the conversation stays valid
                await self.tg.send_message(chat_id, "I can't help with that one.")
                return
            except self.backend.api_errors as exc:
                log.exception("Model API error")
                self.db.save_history(chat_id, provider, snapshot)
                await self.tg.send_message(chat_id, f"⚠️ I hit an error talking to the AI ({exc.__class__.__name__}). Try again?")
                return
            self.db.save_history(chat_id, provider, _trim(history))
            if reply.strip():
                await self.tg.send_message(chat_id, reply)

    def _system_prompt(self) -> str:
        return SYSTEM_PROMPT.format(
            owner_name=self.s.owner_name,
            owner_phone=self.s.owner_phone,
            memories="\n".join(f"- [{m['id']}] {m['text']}" for m in self.db.list_memories()) or "- (nothing yet)",
        )

    def _tools(self) -> list[dict[str, Any]]:
        if self.backend.provider == "anthropic":
            return TOOLS
        return [*TOOLS, *([WEB_SEARCH_TOOL] if self.s.brave_api_key else []), FETCH_URL_TOOL]

    def _tool_runner(self, chat_id: int):
        async def run(name: str, args: Any) -> tuple[str, bool]:
            return await self._run_tool(chat_id, name, args)

        return run

    # --- tools -----------------------------------------------------------------

    async def _run_tool(self, chat_id: int, name: str, args: Any) -> tuple[str, bool]:
        try:
            if not isinstance(args, dict):
                raise ValueError("tool input must be an object")
            return await self._dispatch(chat_id, name, args), False
        except Exception as exc:
            log.exception("Tool %s failed", name)
            return f"Error: {exc}", True

    async def _dispatch(self, chat_id: int, name: str, a: dict[str, Any]) -> str:
        if name == "place_call":
            number = str(a["to_number"]).replace(" ", "").replace("-", "")
            if not E164.match(number):
                raise ValueError(f"'{a['to_number']}' is not an E.164 number like +905321234567")
            if number == self.s.owner_phone:
                raise ValueError("That is the owner's own number")
            assert self.calls is not None
            call_id = await self.calls.request_call(
                chat_id, number, a["contact_name"], a["goal"], a["brief"], a["language"]
            )
            return f"Approval requested from the owner (call id {call_id}). The outcome will arrive as an event."
        if name == "list_recent_calls":
            rows = self.db.recent_calls()
            return json.dumps([dict(r) for r in rows], default=str) if rows else "No calls yet."
        if name == "get_call_details":
            row = self.db.get_call(a["call_id"])
            return json.dumps(dict(row), default=str) if row else "No call with that id."
        if name == "save_contact":
            if not E164.match(a["phone"]):
                raise ValueError("phone must be E.164, e.g. +905321234567")
            self.db.upsert_contact(a["name"], a["phone"], a.get("notes", ""))
            return f"Saved {a['name']}."
        if name == "find_contacts":
            rows = self.db.find_contacts(a["query"])
            return json.dumps([dict(r) for r in rows]) if rows else "No matching contacts."
        if name == "remember":
            return f"Remembered (id {self.db.add_memory(a['fact'])})."
        if name == "web_search" and self.s.brave_api_key:
            return await web.brave_search(self.http, self.s.brave_api_key, a["query"])
        if name == "fetch_url":
            return await web.fetch_url(self.http, a["url"])
        if name == "forget":
            return "Deleted." if self.db.delete_memory(int(a["memory_id"])) else "No memory with that id."
        raise ValueError(f"Unknown tool {name}")


def _trim(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the tail of the conversation, starting at a plain user text message."""
    if len(history) <= MAX_HISTORY_MESSAGES:
        return history
    tail = history[-MAX_HISTORY_MESSAGES:]
    for i, msg in enumerate(tail):
        if msg["role"] == "user" and isinstance(msg["content"], str):
            return tail[i:]
    return history[-1:]
