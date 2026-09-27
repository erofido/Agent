"""Minimal Telegram Bot API client (plain text messages + inline buttons)."""

from __future__ import annotations

from typing import Any

import httpx

MAX_MESSAGE_LEN = 4096


class Telegram:
    def __init__(self, token: str, http: httpx.AsyncClient | None = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.http = http or httpx.AsyncClient(timeout=30)

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = await self.http.post(f"{self.base}/{method}", json=payload)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data}")
        return data["result"]

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        buttons: list[list[tuple[str, str]]] | None = None,
        reply_to: int | None = None,
    ) -> int:
        """Send text (split if too long). Returns the id of the last message sent."""
        chunks = [text[i : i + MAX_MESSAGE_LEN] for i in range(0, len(text), MAX_MESSAGE_LEN)] or [""]
        message_id = 0
        for i, chunk in enumerate(chunks):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if reply_to and i == 0:
                payload["reply_parameters"] = {"message_id": reply_to}
            if buttons and i == len(chunks) - 1:
                payload["reply_markup"] = {
                    "inline_keyboard": [
                        [{"text": label, "callback_data": data} for label, data in row] for row in buttons
                    ]
                }
            message_id = (await self._call("sendMessage", payload))["message_id"]
        return message_id

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        await self._call(
            "editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text[:MAX_MESSAGE_LEN]}
        )

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    async def send_typing(self, chat_id: int) -> None:
        await self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    async def get_updates(self, offset: int, timeout: int = 30) -> list[dict[str, Any]]:
        resp = await self.http.post(
            f"{self.base}/getUpdates",
            json={"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
            timeout=timeout + 10,
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {data}")
        return data["result"]

    async def delete_webhook(self) -> None:
        await self._call("deleteWebhook", {})

    async def set_webhook(self, url: str, secret: str) -> None:
        await self._call(
            "setWebhook",
            {"url": url, "secret_token": secret, "allowed_updates": ["message", "callback_query"]},
        )
