"""Signed-in conversations against a real PostgreSQL + pgvector."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from nursing_bot.config import get_settings
from nursing_bot.jobs import purge_expired_account_data
from nursing_bot.storage.database import get_session
from nursing_bot.storage.models import (
    AuthSession,
    Conversation,
    DailyTokenUsage,
    Message,
)
from tests.integration.conftest import CONTACT_TXT, FEES_TXT, RENEWAL_TXT, upload
from tests.integration.test_accounts import APP, login, make_admin, signup

pytestmark = pytest.mark.integration


def parse_sse(body: str) -> list[tuple[str, object]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def load_corpus(client) -> None:
    for name, content in (
        ("fees.txt", FEES_TXT),
        ("renewal.txt", RENEWAL_TXT),
        ("contact.txt", CONTACT_TXT),
    ):
        assert (await upload(client, name, content)).status_code == 201


async def new_chat(client, title: str | None = None) -> str:
    response = await client.post(
        "/api/v1/conversations", headers=APP, json={"title": title}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def ask(client, conversation_id: str, content: str, stream: bool = False):
    return await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=APP,
        json={"content": content, "stream": stream},
    )


# ----------------------------------------------------------------- sidebar
async def test_conversations_crud_order_and_search(client):
    await signup(client)
    first = await new_chat(client, "Registration fees")
    second = await new_chat(client, "Renewal")
    third = await new_chat(client)
    listing = (await client.get("/api/v1/conversations")).json()["conversations"]
    assert [c["id"] for c in listing] == [third, second, first], "newest first"
    assert listing[0]["title"] == "New chat"

    pinned = await client.patch(
        f"/api/v1/conversations/{first}", headers=APP, json={"pinned": True}
    )
    assert pinned.json()["pinned"] is True
    renamed = await client.patch(
        f"/api/v1/conversations/{second}", headers=APP, json={"title": " Renewal FAQ "}
    )
    assert renamed.json()["title"] == "Renewal FAQ"
    listing = (await client.get("/api/v1/conversations")).json()["conversations"]
    assert listing[0]["id"] == first, "pinned chats come first"

    found = (await client.get("/api/v1/conversations?query=renewal")).json()
    assert [c["id"] for c in found["conversations"]] == [second]
    nothing = (await client.get("/api/v1/conversations?query=100%25")).json()
    assert nothing["conversations"] == [], "LIKE wildcards are escaped"

    await client.patch(
        f"/api/v1/conversations/{third}", headers=APP, json={"archived": True}
    )
    active = (await client.get("/api/v1/conversations")).json()["conversations"]
    archived = (await client.get("/api/v1/conversations?archived=true")).json()
    assert third not in [c["id"] for c in active]
    assert [c["id"] for c in archived["conversations"]] == [third]

    deleted = await client.delete(f"/api/v1/conversations/{second}", headers=APP)
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/conversations/{second}")).status_code == 404


async def test_cursor_pagination_walks_every_conversation_once(client):
    await signup(client)
    created = {await new_chat(client, f"Chat {i}") for i in range(7)}
    seen, cursor = [], None
    while True:
        url = "/api/v1/conversations?limit=3" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(url)).json()
        seen += [c["id"] for c in page["conversations"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 7 and set(seen) == created
    assert (await client.get("/api/v1/conversations?cursor=junk")).status_code == 422


async def test_users_cannot_see_each_others_conversations(client):
    await signup(client, email="a@example.org")
    mine = await new_chat(client, "Private")
    await client.post("/api/v1/auth/logout", headers=APP)
    await signup(client, email="b@example.org")
    for response in (
        await client.get(f"/api/v1/conversations/{mine}"),
        await client.patch(
            f"/api/v1/conversations/{mine}", headers=APP, json={"title": "x"}
        ),
        await client.delete(f"/api/v1/conversations/{mine}", headers=APP),
        await ask(client, mine, "What is the fee?"),
    ):
        assert response.status_code == 404
    assert (await client.get("/api/v1/conversations")).json()["conversations"] == []


async def test_guests_cannot_use_conversations(client):
    assert (await client.get("/api/v1/conversations")).status_code == 401


# --------------------------------------------------------------- answering
async def test_ask_stores_both_turns_and_names_the_chat(client):
    await load_corpus(client)
    await signup(client)
    chat = await new_chat(client)
    response = await ask(client, chat, "What is the online registration fee?")
    assert response.status_code == 200, response.text
    body = response.json()
    answer = body["assistant_message"]
    assert answer["answer_mode"] == "grounded" and "[Source 1]" in answer["content"]
    assert answer["label"] == "From official records" and answer["citations"]
    assert body["title"] == "Nursing registration question"
    assert body["quota_remaining"] < get_settings().USER_DAILY_TOKEN_QUOTA

    detail = (await client.get(f"/api/v1/conversations/{chat}")).json()
    assert detail["title"] == "Nursing registration question"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "What is the online registration fee?"


async def test_streamed_answer_events_and_storage(client):
    await load_corpus(client)
    await signup(client)
    chat = await new_chat(client)
    response = await ask(client, chat, "What is the renewal fee?", stream=True)
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "start" and kinds[1:3] == ["mode", "citations"]
    assert "token" in kinds and kinds[-2:] == ["end", "title"]
    end = dict(events[kinds.index("end")][1])  # type: ignore[arg-type]
    assert end["answer_mode"] == "grounded" and end["message"]["id"]
    assert end["quota_remaining"] is not None
    detail = (await client.get(f"/api/v1/conversations/{chat}")).json()
    assert detail["messages"][1]["id"] == end["message"]["id"]
    assert detail["messages"][1]["status"] == "complete"


async def test_follow_up_questions_use_server_side_history(client, fake_llm):
    await load_corpus(client)
    await signup(client)
    chat = await new_chat(client)
    await ask(client, chat, "What is the online registration fee?")
    await ask(client, chat, "How can I pay it?")
    rewrites = [
        p for p, system in fake_llm.prompts if "query-rewriting" in (system or "")
    ]
    assert rewrites, "the follow-up was condensed"
    payload = json.loads(rewrites[-1])
    assert payload["question"] == "How can I pay it?"
    assert payload["history"][0]["content"] == "What is the online registration fee?"


async def test_general_question_is_answered_and_labelled(client):
    await load_corpus(client)
    await signup(client)
    chat = await new_chat(client)
    body = (await ask(client, chat, "Explain the importance of hand hygiene")).json()
    answer = body["assistant_message"]
    assert answer["answer_mode"] == "general" and answer["citations"] == []
    assert "not from official TNNMC records" in answer["label"]


async def test_failed_stream_keeps_the_partial_answer_as_error(client, fake_llm):
    await load_corpus(client)
    await signup(client)
    chat = await new_chat(client)

    async def broken_stream(*args, **kwargs):
        yield "The online "
        raise RuntimeError("provider dropped the connection")

    fake_llm.astream = broken_stream  # type: ignore[method-assign]
    response = await ask(
        client, chat, "What is the online registration fee?", stream=True
    )
    kinds = [kind for kind, _ in parse_sse(response.text)]
    assert kinds[-1] == "error"
    messages = (await client.get(f"/api/v1/conversations/{chat}")).json()["messages"]
    assert (
        messages[-1]["status"] == "error" and messages[-1]["content"] == "The online "
    )
    async with get_session() as db:
        charged = await db.scalar(
            select(DailyTokenUsage.tokens).where(DailyTokenUsage.subject == "global")
        )
        assert charged and charged > 0, "an aborted answer still costs tokens"


async def test_regenerate_replaces_only_the_latest_answer(client):
    await load_corpus(client)
    await signup(client)
    chat = await new_chat(client)
    first = (await ask(client, chat, "What is the online registration fee?")).json()
    second = (await ask(client, chat, "What is the renewal fee?")).json()
    old = await client.post(
        f"/api/v1/messages/{first['assistant_message']['id']}/regenerate?stream=false",
        headers=APP,
    )
    assert old.status_code == 409
    again = await client.post(
        f"/api/v1/messages/{second['assistant_message']['id']}/regenerate?stream=false",
        headers=APP,
    )
    assert again.status_code == 200, again.text
    messages = (await client.get(f"/api/v1/conversations/{chat}")).json()["messages"]
    assert len(messages) == 4
    assert messages[-1]["id"] == again.json()["assistant_message"]["id"]
    assert second["assistant_message"]["id"] not in [m["id"] for m in messages]


async def test_feedback_is_per_user_and_anonymised_for_admins(client):
    await load_corpus(client)
    await signup(client)
    chat = await new_chat(client)
    answer = (await ask(client, chat, "What is the renewal fee?")).json()[
        "assistant_message"
    ]
    url = f"/api/v1/messages/{answer['id']}/feedback"
    assert (await client.post(url, headers=APP, json={"rating": 1})).status_code == 204
    down = await client.post(
        url, headers=APP, json={"rating": -1, "comment": "Missing the late fee"}
    )
    assert down.status_code == 204
    messages = (await client.get(f"/api/v1/conversations/{chat}")).json()["messages"]
    assert messages[1]["feedback"] == -1
    question_id = messages[0]["id"]
    assert (
        await client.post(
            f"/api/v1/messages/{question_id}/feedback", headers=APP, json={"rating": 1}
        )
    ).status_code == 404, "only answers can be rated"

    await make_admin("priya@example.org")
    review = (await client.get("/api/v1/admin/feedback?rating=-1")).json()
    assert review["total"] == 1
    item = review["items"][0]
    assert item["question"] == "What is the renewal fee?"
    assert item["comment"] == "Missing the late fee"
    assert "priya" not in json.dumps(review), "no user identity"
    stats = (await client.get("/api/v1/admin/stats")).json()
    assert stats["answers_last_7_days"]["grounded"] == 1 and stats["tokens_today"] > 0

    assert (
        await client.post(url, headers=APP, json={"rating": None})
    ).status_code == 204
    assert (await client.get("/api/v1/admin/feedback")).json()["total"] == 0


async def test_import_guest_chats_into_an_account(client):
    await signup(client)
    response = await client.post(
        "/api/v1/conversations/import",
        headers=APP,
        json={
            "conversations": [
                {
                    "title": "From my phone",
                    "created_at": "2026-11-01T10:00:00",
                    "messages": [
                        {"role": "user", "content": "What is the fee?"},
                        {
                            "role": "assistant",
                            "content": "Rs. 365 [Source 1].",
                            "answer_mode": "grounded",
                            "citations": [{"source_index": 1, "title": "FAQ.pdf"}],
                        },
                        {"role": "user", "content": "Thanks"},
                        {
                            "role": "assistant",
                            "content": "You're welcome.",
                            "answer_mode": "made-up-mode",
                        },
                    ],
                }
            ]
        },
    )
    assert response.status_code == 201, response.text
    chat = response.json()["conversation_ids"][0]
    detail = (await client.get(f"/api/v1/conversations/{chat}")).json()
    assert detail["title"] == "From my phone"
    assert [m["content"] for m in detail["messages"]] == [
        "What is the fee?",
        "Rs. 365 [Source 1].",
        "Thanks",
        "You're welcome.",
    ]
    assert all(m["is_imported"] for m in detail["messages"])
    assert detail["messages"][1]["citations"][0]["title"] == "FAQ.pdf"
    assert detail["messages"][3]["answer_mode"] is None, "unknown modes are dropped"


# ------------------------------------------------------------------ limits
async def test_daily_quota_and_global_cap(client, settings_override):
    await load_corpus(client)
    await signup(client)
    chat = await new_chat(client)
    settings_override(USER_DAILY_TOKEN_QUOTA="1")
    assert (await ask(client, chat, "What is the renewal fee?")).status_code == 200
    blocked = await ask(client, chat, "What is the renewal fee?")
    assert blocked.status_code == 429 and "usage limit" in blocked.json()["detail"]

    settings_override(USER_DAILY_TOKEN_QUOTA="0", GLOBAL_DAILY_TOKEN_CAP="1")
    assert (await ask(client, chat, "What is the renewal fee?")).status_code == 503
    session = (await client.post("/api/v1/chat/session")).json()
    guest = await client.post(
        "/api/v1/chat",
        headers={"X-Session-Token": session["session_token"]},
        json={"query": "What is the renewal fee?"},
    )
    assert guest.status_code == 503, "the cap covers guests too"


async def test_guest_chat_can_be_switched_off(client, settings_override):
    settings_override(GUEST_CHAT_ENABLED="false")
    assert (await client.post("/api/v1/chat/session")).status_code == 403
    assert (await client.get("/api/v1/app/config")).json()[
        "guest_chat_enabled"
    ] is False


async def test_user_chat_rate_limit_is_per_session(client, settings_override):
    await load_corpus(client)
    await signup(client)
    chat = await new_chat(client)
    settings_override(USER_CHAT_RATE_LIMIT="1/minute")
    assert (await ask(client, chat, "What is the renewal fee?")).status_code == 200
    assert (await ask(client, chat, "What is the renewal fee?")).status_code == 429


# --------------------------------------------------------- memory, cleanup
async def test_long_chats_get_a_rolling_summary(client, settings_override, fake_llm):
    await load_corpus(client)
    # Window = 2 messages; summarise once 4 more have scrolled out of it.
    settings_override(MAX_RECENT_CONTEXT_TURNS="1", CONVERSATION_SUMMARY_BATCH="4")
    await signup(client)
    chat = await new_chat(client)
    for question in (
        "What is the online registration fee?",
        "What is the renewal fee?",
        "When is the council office open?",
    ):
        assert (await ask(client, chat, question)).status_code == 200
    async with get_session() as db:
        conversation = await db.get(Conversation, chat)
        assert conversation is not None
        assert conversation.summary == "The user asked about nurse registration."
        assert conversation.summary_message_count == 4
    summary_calls = [s for _, s in fake_llm.prompts if "running summary" in (s or "")]
    assert len(summary_calls) == 1, "summarised in batches, not every turn"


async def test_retention_purge(client, settings_override):
    await signup(client)
    chat = await new_chat(client)
    old = datetime.now(UTC) - timedelta(days=400)
    async with get_session() as db:
        await db.execute(update(Conversation).values(updated_at=old))
        db.add(DailyTokenUsage(day=old.date(), subject="global", tokens=5))
        await db.execute(
            update(AuthSession).values(expires_at=datetime.now(UTC) - timedelta(days=1))
        )
        await db.commit()
    counts = await purge_expired_account_data()
    assert counts == {"auth_sessions": 1, "usage_days": 1, "conversations": 0}
    settings_override(CONVERSATION_RETENTION_DAYS="30")
    assert (await purge_expired_account_data())["conversations"] == 1
    async with get_session() as db:
        assert await db.get(Conversation, chat) is None
        assert (await db.execute(select(Message))).first() is None


async def test_login_after_signup_elsewhere_keeps_working(client):
    """Signing in again replaces this browser's previous session."""
    await signup(client)
    first = client.cookies.get("nb_session")
    await login(client)
    second = client.cookies.get("nb_session")
    assert first and second and first != second
    async with get_session() as db:
        sessions = (await db.execute(select(AuthSession))).scalars().all()
        assert sum(s.revoked_at is None for s in sessions) == 1
