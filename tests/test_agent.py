import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.brain import Brain, _trim
from agent.llm import AnthropicBackend, OpenAICompatBackend
from agent.calls import CallManager
from agent.config import Settings
from agent.db import DB

SETTINGS = Settings(
    brain_provider="deepseek",
    deepseek_api_key="sk-test",
    deepseek_base_url="https://api.deepseek.com/v1",
    deepseek_model="deepseek-v4-flash",
    deepseek_reasoning_effort="none",
    brave_api_key="",
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


def test_anthropic_backend_tool_loop(tmp_path):
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
        brain = Brain(SETTINGS, db, tg, backend=AnthropicBackend("claude-opus-5", client=client))
        brain.calls = calls
        await brain.handle_user_message(7, "remember my plate")
        assert [m["text"] for m in db.list_memories()] == ["plate 34 ABC 123"]
        tg.send_message.assert_awaited_with(7, "Saved!")
        hist = db.get_history(7, "anthropic")
        assert hist[2]["content"][0]["type"] == "tool_result"
        assert db.get_history(7, "deepseek") == []  # other provider starts fresh
        kwargs = client.beta.messages.create.call_args.kwargs
        assert kwargs["fallbacks"] == "default" and kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["tools"][0]["type"] == "web_search_20260209"

    asyncio.run(run())


def _oai_response(content=None, tool_calls=None, finish="stop"):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)])


def test_deepseek_backend_tool_loop(tmp_path):
    async def run():
        db, tg, calls, _ = make(tmp_path)
        client = MagicMock()
        call = SimpleNamespace(
            id="c1", type="function",
            function=SimpleNamespace(name="save_contact", arguments='{"name": "Luigi\'s", "phone": "+902161234567"}'),
        )
        client.chat.completions.create = AsyncMock(
            side_effect=[_oai_response(tool_calls=[call], finish="tool_calls"), _oai_response(content="Saved Luigi's.")]
        )
        backend = OpenAICompatBackend("deepseek-v4-flash", "https://x", "k", client=client)
        brain = Brain(SETTINGS, db, tg, backend=backend)
        brain.calls = calls
        await brain.handle_user_message(7, "save luigi +902161234567")

        assert db.find_contacts("luigi")[0]["phone"] == "+902161234567"
        tg.send_message.assert_awaited_with(7, "Saved Luigi's.")
        hist = db.get_history(7, "deepseek")
        assert [m["role"] for m in hist] == ["user", "assistant", "tool", "assistant"]
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["reasoning_effort"] == "none"
        assert kwargs["messages"][0]["role"] == "system"
        names = [t["function"]["name"] for t in kwargs["tools"]]
        assert "place_call" in names and "fetch_url" in names and "web_search" not in names  # no Brave key

    asyncio.run(run())


def test_api_error_rolls_back(tmp_path):
    async def run():
        import httpx, openai
        db, tg, calls, _ = make(tmp_path)
        client = MagicMock()
        req = httpx.Request("POST", "https://x")
        client.chat.completions.create = AsyncMock(side_effect=openai.APIConnectionError(request=req))
        brain = Brain(SETTINGS, db, tg, backend=OpenAICompatBackend("m", "https://x", "k", client=client))
        await brain.handle_user_message(7, "hi")
        assert db.get_history(7, "deepseek") == []
        assert "error" in tg.send_message.call_args.args[1]

    asyncio.run(run())


def test_place_call_rejects_bad_number(tmp_path):
    async def run():
        db, tg, calls, _ = make(tmp_path)
        brain = Brain(SETTINGS, db, tg, backend=OpenAICompatBackend("m", "https://x", "k", client=MagicMock()))
        brain.calls = calls
        out, err = await brain._run_tool(7, "place_call", {"to_number": "0532 123", "contact_name": "x", "goal": "g", "brief": "b", "language": "en"})
        assert err and "E.164" in out

    asyncio.run(run())


def test_fetch_url_blocks_private_hosts():
    import httpx
    from agent import web

    async def run():
        async with httpx.AsyncClient() as http:
            for url in ("http://127.0.0.1:8000/", "http://169.254.169.254/latest", "file:///etc/passwd"):
                with pytest.raises(ValueError):
                    await web.fetch_url(http, url)

    asyncio.run(run())


def test_chat_only_mode(tmp_path, monkeypatch):
    """No public URL / Twilio / Retell: bot polls Telegram, calls are off, place_call is hidden."""
    import dataclasses
    from agent.main import App

    s = dataclasses.replace(SETTINGS, public_base_url="", twilio_account_sid="", db_path=str(tmp_path / "a.db"))
    assert not s.calls_enabled

    async def run():
        a = App(s)
        assert a.calls is None
        names = [t["name"] for t in a.brain._tools()]
        assert "place_call" not in names and "fetch_url" in names
        assert "not set up yet" in a.brain._system_prompt()

        a.brain.handle_user_message = AsyncMock()
        a.handle_update({"message": {"chat": {"id": 1}, "from": {"id": 999}, "message_id": 1, "text": "hi"}})
        a.handle_update({"message": {"chat": {"id": 1}, "from": {"id": 1}, "message_id": 2, "text": "hello"}})
        await asyncio.sleep(0)
        a.brain.handle_user_message.assert_awaited_once_with(1, "hello")  # non-owner ignored

    asyncio.run(run())


def test_load_settings_minimal(monkeypatch):
    from agent.config import load_settings

    for k in list(__import__("os").environ):
        if k.startswith(("TWILIO", "RETELL", "PUBLIC_BASE", "TELEGRAM", "DEEPSEEK", "BRAIN", "OWNER")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("OWNER_NAME", "Eray")
    monkeypatch.setenv("OWNER_PHONE", "+905321234567")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC...")  # untouched placeholder counts as empty
    s = load_settings()
    assert s.brain_provider == "deepseek" and not s.calls_enabled and s.telegram_webhook_secret


def test_bad_timezone_message(monkeypatch):
    from agent.config import _timezone

    monkeypatch.setenv("OWNER_TIMEZONE", "United Kingdom/London")
    with pytest.raises(RuntimeError, match="Europe/London"):
        _timezone()
    monkeypatch.setenv("OWNER_TIMEZONE", "Europe/London")
    assert _timezone() == "Europe/London"


def _group_app(tmp_path, owner_status="member"):
    import dataclasses
    from agent.main import App

    s = dataclasses.replace(SETTINGS, public_base_url="", twilio_account_sid="", db_path=str(tmp_path / "g.db"))
    a = App(s)
    a.tg.send_message = AsyncMock(return_value=77)
    a.tg.send_typing = AsyncMock()
    a.tg.get_chat_member_status = AsyncMock(return_value=owner_status)
    a.groups.bot_id, a.groups.bot_username, a.groups.bot_name = 555, "eray_helper_bot", "Helper"
    a.brain.backend.run = AsyncMock(return_value="Привет! Биг-Бен рядом с Вестминстером.")
    a.brain.backend.complete = AsyncMock(return_value="- Anna: hotel booked for 3 nights")
    return a


def _gmsg(text, sender_id=42, first="Anna", reply_to_bot=False, chat_id=-100):
    m = {"chat": {"id": chat_id, "type": "supergroup", "title": "London trip"}, "from": {"id": sender_id, "first_name": first},
         "message_id": 10, "date": 1790000000, "text": text}
    if reply_to_bot:
        m["reply_to_message"] = {"from": {"id": 555}}
    return m


def test_group_records_and_replies_only_when_addressed(tmp_path):
    async def run():
        a = _group_app(tmp_path)
        await a.groups.handle(_gmsg("Кто бронирует отель?"))
        a.brain.backend.run.assert_not_awaited()  # not addressed to the bot: just recorded
        await a.groups.handle(_gmsg("@eray_helper_bot где Биг-Бен?"))
        await a.groups.handle(_gmsg("а сколько стоит?", reply_to_bot=True))
        assert a.brain.backend.run.await_count == 2
        chat_id, text = a.tg.send_message.call_args.args
        assert chat_id == -100 and "Биг-Бен" in text and a.tg.send_message.call_args.kwargs["reply_to"] == 10
        system, history, tools, runner = a.brain.backend.run.call_args.args
        assert "bot" in system and "Кто бронирует отель?" in history[0]["content"]  # context included
        assert {t["name"] for t in tools} <= {"web_search", "fetch_url"}  # no calls/memories for the group
        out, err = await runner("place_call", {"to_number": "+441234567890"})
        assert err and "not available" in out
        senders = [r["sender"] for r in a.db.group_messages(-100)]
        assert senders.count("Anna") == 3 and "Helper (bot)" in senders

    asyncio.run(run())


def test_group_ignored_when_owner_not_member(tmp_path):
    async def run():
        a = _group_app(tmp_path, owner_status="left")
        await a.groups.handle(_gmsg("@eray_helper_bot hi"))
        a.brain.backend.run.assert_not_awaited()
        assert a.db.group_messages(-100) == []

    asyncio.run(run())


def test_group_rate_limit(tmp_path):
    from agent import groups

    async def run():
        a = _group_app(tmp_path)
        for _ in range(groups.MAX_REPLIES_PER_MINUTE + 3):
            await a.groups.handle(_gmsg("@eray_helper_bot ?"))
        assert a.brain.backend.run.await_count == groups.MAX_REPLIES_PER_MINUTE

    asyncio.run(run())


def test_group_summary_sent_to_owner_once(tmp_path):
    async def run():
        a = _group_app(tmp_path)
        await a.groups.handle(_gmsg("Я забронировала отель на 3 ночи"))
        await a.groups.handle(_gmsg("Эрай, ты прилетаешь в пятницу?", sender_id=43, first="Oleg"))
        await a.groups.send_summaries()
        chat_id, text = a.tg.send_message.call_args.args
        assert chat_id == SETTINGS.telegram_owner_id and "London trip (2 new)" in text and "hotel" in text
        prompt = a.brain.backend.complete.call_args.args[1]
        assert "Oleg: Эрай" in prompt
        a.tg.send_message.reset_mock()
        await a.groups.send_summaries()  # nothing new -> no message
        a.tg.send_message.assert_not_awaited()

    asyncio.run(run())


def test_private_routing_ru_and_group_owner(tmp_path):
    async def run():
        a = _group_app(tmp_path)
        a.brain.backend.complete = AsyncMock(return_value="Кто бронирует отель?")
        a.brain.handle_user_message = AsyncMock()
        a.handle_update({"message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "message_id": 1, "text": "/ru Who books the hotel?"}})
        # the owner writing in the group goes to the group logic, never the personal brain
        a.handle_update({"message": _gmsg("hello all", sender_id=1, first="Eray")})
        await asyncio.sleep(0.05)
        a.tg.send_message.assert_awaited_with(1, "Кто бронирует отель?")
        a.brain.handle_user_message.assert_not_awaited()
        assert a.db.group_messages(-100)[0]["sender"] == "Eray (owner)"

    asyncio.run(run())


def test_read_group_chats_tool(tmp_path):
    async def run():
        a = _group_app(tmp_path)
        await a.groups.handle(_gmsg("Отель на Кингс-Кросс"))
        out, err = await a.brain._run_tool(1, "read_group_chats", {})
        assert not err and "London trip" in out and "Anna: Отель" in out

    asyncio.run(run())
