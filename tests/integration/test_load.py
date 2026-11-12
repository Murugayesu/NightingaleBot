"""Concurrency: many answers at once must neither exhaust the pool nor lose tokens."""

import asyncio

import pytest
from sqlalchemy import func, select

from nursing_bot.storage.database import dispose_engine, get_engine, get_session
from nursing_bot.storage.models import ChatSession, DailyTokenUsage, Message
from tests.integration.conftest import CONTACT_TXT, FEES_TXT, RENEWAL_TXT, upload
from tests.integration.test_accounts import APP, signup

pytestmark = pytest.mark.integration

QUESTIONS = [
    "What is the online registration fee?",
    "What is the renewal fee?",
    "When is the council office open?",
    "Explain aseptic technique",
]


async def _corpus(client) -> None:
    for name, content in (
        ("fees.txt", FEES_TXT),
        ("renewal.txt", RENEWAL_TXT),
        ("contact.txt", CONTACT_TXT),
    ):
        assert (await upload(client, name, content)).status_code == 201


async def test_concurrent_guest_answers_charge_every_token(client, settings_override):
    settings_override(
        CHAT_RATE_LIMIT="1000/minute",
        SESSION_CREATE_RATE_LIMIT="1000/hour",
        DATABASE_POOL_SIZE="5",
        DATABASE_MAX_OVERFLOW="0",
    )
    await dispose_engine()  # the next connection uses the small pool
    await _corpus(client)
    sessions = [
        (await client.post("/api/v1/chat/session")).json()["session_token"]
        for _ in range(4)
    ]

    async def ask(i: int):
        return await client.post(
            "/api/v1/chat",
            headers={"X-Session-Token": sessions[i % len(sessions)]},
            json={"query": QUESTIONS[i % len(QUESTIONS)], "stream": i % 2 == 0},
        )

    # 20 answers in flight on a pool of 5 connections: each request holds a
    # connection only briefly, never while the model is generating.
    responses = await asyncio.gather(*(ask(i) for i in range(20)))
    assert [r.status_code for r in responses] == [200] * 20
    assert get_engine().pool.size() == 5  # type: ignore[attr-defined]

    async with get_session() as db:
        per_session = await db.scalar(select(func.sum(ChatSession.tokens_used)))
        global_today = await db.scalar(
            select(DailyTokenUsage.tokens).where(DailyTokenUsage.subject == "global")
        )
    assert per_session and per_session > 0
    assert global_today == per_session, "atomic charges: no lost updates"


async def test_concurrent_questions_in_one_conversation(client, settings_override):
    settings_override(USER_CHAT_RATE_LIMIT="1000/minute")
    await _corpus(client)
    await signup(client)
    chat = (await client.post("/api/v1/conversations", headers=APP, json={})).json()[
        "id"
    ]

    async def ask(question: str):
        return await client.post(
            f"/api/v1/conversations/{chat}/messages",
            headers=APP,
            json={"content": question, "stream": False},
        )

    responses = await asyncio.gather(*(ask(q) for q in QUESTIONS + QUESTIONS[:1]))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    async with get_session() as db:
        stored = await db.scalar(select(func.count(Message.id)))
        user_today = await db.scalar(
            select(DailyTokenUsage.tokens).where(DailyTokenUsage.subject.like("user:%"))
        )
        global_today = await db.scalar(
            select(DailyTokenUsage.tokens).where(DailyTokenUsage.subject == "global")
        )
    assert stored == 10, "every question and every answer is stored"
    answers = sum(r.json()["assistant_message"]["tokens_used"] for r in responses)
    # Titles are extra (concurrent first questions may each ask for one).
    assert user_today == global_today >= answers
