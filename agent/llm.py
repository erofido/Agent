"""Model backends for the brain. Each one runs the tool-use loop for one turn.

- AnthropicBackend: Claude via the Anthropic SDK, with Claude's built-in web search/fetch.
- OpenAICompatBackend: DeepSeek (or any OpenAI-compatible host, e.g. EUrouter) via the OpenAI SDK,
  with our own web_search/fetch_url tools.

Both keep history in their provider's native message format, so a stored conversation belongs to one
provider. The brain starts a fresh conversation when you switch.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Protocol

import anthropic
import openai

log = logging.getLogger(__name__)

MAX_STEPS = 25  # safety cap on tool-use iterations per turn
STEP_LIMIT_REPLY = "I stopped after too many steps. Tell me how you'd like to continue."

# (tool name, args) -> (result text, is_error)
ToolRunner = Callable[[str, Any], Awaitable[tuple[str, bool]]]


class Refused(Exception):
    """The model declined the request; the brain rolls the turn back."""


class Backend(Protocol):
    provider: str
    api_errors: tuple[type[Exception], ...]

    async def run(
        self, system: str, history: list[dict[str, Any]], tools: list[dict[str, Any]], run_tool: ToolRunner
    ) -> str: ...

    async def complete(self, system: str, prompt: str) -> str:
        """One-shot text answer, no tools (summaries, translations)."""
        ...


# --- Claude ---------------------------------------------------------------------

ANTHROPIC_SERVER_TOOLS: list[dict[str, Any]] = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5},
]
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicBackend:
    provider = "anthropic"
    api_errors = (anthropic.APIError,)

    def __init__(self, model: str, client: anthropic.AsyncAnthropic | None = None):
        self.model = model
        self.client = client or anthropic.AsyncAnthropic()

    async def run(self, system, history, tools, run_tool):
        extra: dict[str, Any] = {}
        if self.model.startswith(("claude-opus-5", "claude-fable")):
            # Server-side retry on a substitute model if a safety classifier declines.
            extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        for _ in range(MAX_STEPS):
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                tools=[*ANTHROPIC_SERVER_TOOLS, *tools],
                messages=history,
                thinking={"type": "adaptive"},
                cache_control={"type": "ephemeral"},
                **extra,
            )
            if response.stop_reason == "refusal":
                raise Refused()
            history.append({"role": "assistant", "content": [b.to_dict(mode="json") for b in response.content]})

            if response.stop_reason == "pause_turn":
                continue  # server tool (web search) paused mid-turn; resend to resume
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                return "".join(b.text for b in response.content if b.type == "text")

            results = await asyncio.gather(*(run_tool(b.name, b.input) for b in tool_uses))
            history.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": b.id, "content": out, "is_error": err}
                        for b, (out, err) in zip(tool_uses, results)
                    ],
                }
            )
        return STEP_LIMIT_REPLY

    async def complete(self, system, prompt):
        response = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"},
        )
        if response.stop_reason == "refusal":
            raise Refused()
        return "".join(b.text for b in response.content if b.type == "text")


# --- DeepSeek / OpenAI-compatible -----------------------------------------------


class OpenAICompatBackend:
    provider = "deepseek"
    api_errors = (openai.APIError,)

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        reasoning_effort: str = "none",
        client: openai.AsyncOpenAI | None = None,
    ):
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.client = client or openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def run(self, system, history, tools, run_tool):
        fn_tools = [
            {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in tools
        ]
        # DeepSeek V4 thinks before answering unless told not to. Reservay measured ~4s extra per step
        # with it on; "none" turns it off. Set DEEPSEEK_REASONING_EFFORT=default to turn it back on.
        extra = {} if self.reasoning_effort == "default" else {"reasoning_effort": self.reasoning_effort}
        for _ in range(MAX_STEPS):
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=8000,
                messages=[{"role": "system", "content": system}, *history],
                tools=fn_tools,
                tool_choice="auto",
                **extra,
            )
            choice = response.choices[0]
            msg = choice.message
            assistant: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                # DeepSeek wants its reasoning passed back while a tool-call turn is still going.
                assistant["reasoning_content"] = reasoning
            calls = [c for c in (msg.tool_calls or []) if c.type == "function"]
            if calls:
                assistant["tool_calls"] = [
                    {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in calls
                ]
            history.append(assistant)

            if choice.finish_reason == "content_filter":
                raise Refused()
            if not calls:
                if choice.finish_reason == "length":
                    return (msg.content or "") + "\n\n(My reply got cut off. Ask me to continue.)"
                return msg.content or ""

            results = await asyncio.gather(*(run_tool(c.function.name, _parse_args(c.function.arguments)) for c in calls))
            for c, (out, err) in zip(calls, results):
                history.append({"role": "tool", "tool_call_id": c.id, "content": out})
        return STEP_LIMIT_REPLY

    async def complete(self, system, prompt):
        extra = {} if self.reasoning_effort == "default" else {"reasoning_effort": self.reasoning_effort}
        response = await self.client.chat.completions.create(
            model=self.model,
            max_tokens=4000,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            **extra,
        )
        choice = response.choices[0]
        if choice.finish_reason == "content_filter":
            raise Refused()
        return choice.message.content or ""


def _parse_args(raw: str | None) -> Any:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None  # the brain reports "tool input must be an object" back to the model
