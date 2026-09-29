"""Tiny SQLite store: conversation history, memories, contacts and calls."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    chat_id INTEGER PRIMARY KEY,
    messages TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS group_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    chat_title TEXT NOT NULL,
    sender TEXT NOT NULL,
    text TEXT NOT NULL,
    sent_at REAL NOT NULL,
    summarized INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS group_messages_pending ON group_messages (summarized, chat_id);
CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    contact_name TEXT NOT NULL,
    to_number TEXT NOT NULL,
    goal TEXT NOT NULL,
    brief TEXT NOT NULL,
    language TEXT NOT NULL,
    status TEXT NOT NULL,
    retell_call_id TEXT,
    twilio_sid TEXT,
    summary TEXT,
    transcript TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- conversation history -------------------------------------------------

    def get_history(self, chat_id: int, provider: str) -> list[dict[str, Any]]:
        """Messages are stored in the provider's own format; another provider starts fresh."""
        row = self.conn.execute("SELECT messages FROM history WHERE chat_id = ?", (chat_id,)).fetchone()
        if not row:
            return []
        stored = json.loads(row["messages"])
        return stored["messages"] if stored.get("provider") == provider else []

    def save_history(self, chat_id: int, provider: str, messages: list[dict[str, Any]]) -> None:
        self.conn.execute(
            "INSERT INTO history (chat_id, messages) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET messages = excluded.messages",
            (chat_id, json.dumps({"provider": provider, "messages": messages})),
        )
        self.conn.commit()

    def clear_history(self, chat_id: int) -> None:
        self.conn.execute("DELETE FROM history WHERE chat_id = ?", (chat_id,))
        self.conn.commit()

    # --- memories -------------------------------------------------------------

    def add_memory(self, text: str) -> int:
        cur = self.conn.execute("INSERT INTO memories (text, created_at) VALUES (?, ?)", (text, time.time()))
        self.conn.commit()
        return int(cur.lastrowid)

    def list_memories(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT id, text FROM memories ORDER BY id").fetchall()

    def delete_memory(self, memory_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # --- contacts -------------------------------------------------------------

    def upsert_contact(self, name: str, phone: str, notes: str = "") -> int:
        row = self.conn.execute("SELECT id FROM contacts WHERE lower(name) = lower(?)", (name,)).fetchone()
        if row:
            self.conn.execute(
                "UPDATE contacts SET phone = ?, notes = ? WHERE id = ?", (phone, notes, row["id"])
            )
            self.conn.commit()
            return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO contacts (name, phone, notes) VALUES (?, ?, ?)", (name, phone, notes)
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def find_contacts(self, query: str) -> list[sqlite3.Row]:
        like = f"%{query.lower()}%"
        return self.conn.execute(
            "SELECT name, phone, notes FROM contacts "
            "WHERE lower(name) LIKE ? OR lower(notes) LIKE ? OR phone LIKE ? ORDER BY name",
            (like, like, like),
        ).fetchall()

    # --- group chats ------------------------------------------------------------

    def add_group_message(
        self, chat_id: int, chat_title: str, sender: str, text: str, sent_at: float, summarized: bool = False
    ) -> None:
        self.conn.execute(
            "INSERT INTO group_messages (chat_id, chat_title, sender, text, sent_at, summarized) VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, chat_title, sender, text, sent_at, int(summarized)),
        )
        self.conn.commit()

    def pending_group_chats(self) -> list[int]:
        rows = self.conn.execute("SELECT DISTINCT chat_id FROM group_messages WHERE summarized = 0").fetchall()
        return [r["chat_id"] for r in rows]

    def group_messages(self, chat_id: int, *, summarized: bool | None = None, limit: int = 200) -> list[sqlite3.Row]:
        """Newest `limit` messages, returned oldest first. summarized=None means both kinds."""
        where, args = "chat_id = ?", [chat_id]
        if summarized is not None:
            where += " AND summarized = ?"
            args.append(int(summarized))
        rows = self.conn.execute(
            f"SELECT id, chat_title, sender, text, sent_at FROM group_messages WHERE {where} ORDER BY id DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return list(reversed(rows))

    def mark_summarized(self, ids: list[int]) -> None:
        self.conn.executemany("UPDATE group_messages SET summarized = 1 WHERE id = ?", [(i,) for i in ids])
        self.conn.commit()

    def recent_group_messages(self, limit: int = 150) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT chat_title, sender, text, sent_at FROM group_messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return list(reversed(rows))

    # --- calls ----------------------------------------------------------------

    def create_call(
        self, chat_id: int, contact_name: str, to_number: str, goal: str, brief: str, language: str
    ) -> str:
        call_id = uuid.uuid4().hex[:12]
        now = time.time()
        self.conn.execute(
            "INSERT INTO calls (id, chat_id, contact_name, to_number, goal, brief, language, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'awaiting_approval', ?, ?)",
            (call_id, chat_id, contact_name, to_number, goal, brief, language, now, now),
        )
        self.conn.commit()
        return call_id

    def get_call(self, call_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()

    def get_call_by_retell_id(self, retell_call_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM calls WHERE retell_call_id = ?", (retell_call_id,)
        ).fetchone()

    def update_call(self, call_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE calls SET {cols} WHERE id = ?", (*fields.values(), call_id))
        self.conn.commit()

    def recent_calls(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, contact_name, to_number, goal, status, summary, created_at FROM calls "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
