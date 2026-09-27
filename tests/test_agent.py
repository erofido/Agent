import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.brain import Brain, _trim
from agent.calls import CallManager
from agent.config import Settings
from agent.db import DB

SETTINGS = Settings(
    anthropic_model="claude-opus-5",
    telegram_bot_token="t",
    telegram_owner_id=1,
    telegram_webhook_secret="s",
    public_base_url="https://agent.test",
    owner_name="Eray",
    owner_phone="+905321234567",
    timezone="Europe/Istanbul",
    twilio_account_sid="AC123",
    twilio_auth_token="tok",
    retell_api_key="key",
    retell_agent_id="agent_1",
    db_path=":memory:",
    ask_owner_timeout=1,
)


def make(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    tg = MagicMock()
    tg.send_message = AsyncMock(return_value=42)
    tg.edit_message = AsyncMock()
    tg.send_typing = AsyncMock()
    events = AsyncMock()
    calls = CallManager(SETTINGS, db, tg, on_event=events)
    return db, tg, calls, events


def test_db_contacts_and_memories(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    db.upsert_contact("Luigi's", "+902161234567", "restaurant")
    db.upsert_contact("luigi's", "+902160000000")
    rows = db.find_contacts("luigi")
    assert len(rows) == 1 and rows[0]["phone"] == "+902160000000"
    mid = db.add_memory("likes window seats")
    assert [m["text"] for m in db.list_memories()] == ["likes window seats"]
    assert db.delete_memory(mid)


def test_trim_starts_at_user_text():
    hist = []
    for i in range(40):
        hist += [{"role": "user", "content": f"hi {i}"}, {"role": "assistant", "content": [{"type": "text", "text": "x"}]}]
    trimmed = _trim(hist)
    assert trimmed[0]["role"] == "user" and isinstance(trimmed[0]["content"], str)
    assert len(trimmed) <= 60


def test_call_flow_uses_owner_number(tmp_path):
    async def run():
        db, tg, calls, events = make(tmp_path)
        calls.retell.call.register_phone_call = AsyncMock(return_value=SimpleNamespace(call_id="rc_1"))
        calls.twilio = MagicMock()
        calls.twilio.calls.create.return_value = SimpleNamespace(sid="CA1")

        call_id = await calls.request_call(7, "+902161234567", "Luigi's", "Book table", "brief", "Turkish")
        _, kwargs = tg.send_message.call_args
        assert kwargs["buttons"][0][0][1] == f"call:go:{call_id}"

        assert await calls.handle_button(7, 99, f"call:go:{call_id}") == "Calling"
        reg = calls.retell.call.register_phone_call.call_args.kwargs
        assert reg["from_number"] == SETTINGS.owner_phone
        assert reg["retell_llm_dynamic_variables"]["call_brief"] == "brief"
        tw = calls.twilio.calls.create.call_args.kwargs
        assert tw["from_"] == SETTINGS.owner_phone
        assert "sip:rc_1@sip.retellai.com" in tw["twiml"]
        assert db.get_call(call_id)["status"] == "dialing"

        await calls.handle_retell_event(
            {
                "event": "call_analyzed",
                "call": {
                    "call_id": "rc_1",
                    "metadata": {"local_call_id": call_id},
                    "transcript": "Agent: hi\nUser: booked",
                    "call_analysis": {"call_summary": "Table booked"},
                },
            }
        )
        assert db.get_call(call_id)["status"] == "completed"
        chat, text = events.call_args.args
        assert chat == 7 and "Table booked" in text

    asyncio.run(run())


def test_cancel_and_no_answer(tmp_path):
    async def run():
        db, tg, calls, events = make(tmp_path)
        cid = await calls.request_call(7, "+902161234567", "X", "g", "b", "English")
        assert await calls.handle_button(7, 1, f"call:no:{cid}") == "Cancelled"
        assert db.get_call(cid)["status"] == "cancelled"
        assert await calls.handle_button(7, 1, f"call:go:{cid}") == "Already cancelled"

        cid2 = await calls.request_call(7, "+902161234567", "Y", "g", "b", "English")
        db.update_call(cid2, status="dialing")
        await calls.handle_twilio_status(cid2, "no-answer")
        assert db.get_call(cid2)["status"] == "no-answer"
        assert "no-answer" in events.call_args.args[1]

    asyncio.run(run())


def test_ask_owner_answer_and_timeout(tmp_path):
    async def run():
        db, tg, calls, _ = make(tmp_path)
        cid = await calls.request_call(7, "+902161234567", "X", "g", "b", "English")
        db.update_call(cid, retell_call_id="rc_9")

        task = asyncio.create_task(calls.ask_owner("rc_9", "9:30 ok?"))
        await asyncio.sleep(0.05)
        assert calls.resolve_owner_reply("yes", reply_to_message_id=42)
        assert "yes" in await task

        assert "did not reply" in await calls.ask_owner("rc_9", "again?")
        assert not calls.resolve_owner_reply("late", None)

    asyncio.run(run())


def test_brain_tool_loop(tmp_path):
    async def run():
        db, tg, calls, _ = make(tmp_path)
        client = MagicMock()
        tool_block = SimpleNamespace(
            type="tool_use", id="tu1", name="remember", input={"fact": "plate 34 ABC 123"},
            to_dict=lambda mode="json": {"type": "tool_use", "id": "tu1", "name": "remember", "input": {"fact": "plate 34 ABC 123"}},
        )
        text_block = SimpleNamespace(type="text", text="Saved!", to_dict=lambda mode="json": {"type": "text", "text": "Saved!"})
        client.beta.messages.create = AsyncMock(
            side_effect=[
                SimpleNamespace(stop_reason="tool_use", content=[tool_block]),
                SimpleNamespace(stop_reason="end_turn", content=[text_block]),
            ]
        )
        brain = Brain(SETTINGS, db, tg, client=client)
        brain.calls = calls
        await brain.handle_user_message(7, "remember my plate")
        assert [m["text"] for m in db.list_memories()] == ["plate 34 ABC 123"]
        tg.send_message.assert_awaited_with(7, "Saved!")
        hist = db.get_history(7)
        assert hist[2]["content"][0]["type"] == "tool_result"
        kwargs = client.beta.messages.create.call_args.kwargs
        assert kwargs["fallbacks"] == "default" and kwargs["thinking"] == {"type": "adaptive"}

    asyncio.run(run())


def test_place_call_rejects_bad_number(tmp_path):
    async def run():
        db, tg, calls, _ = make(tmp_path)
        brain = Brain(SETTINGS, db, tg, client=MagicMock())
        brain.calls = calls
        out, err = await brain._run_tool(7, "place_call", {"to_number": "0532 123", "contact_name": "x", "goal": "g", "brief": "b", "language": "en"})
        assert err and "E.164" in out

    asyncio.run(run())
