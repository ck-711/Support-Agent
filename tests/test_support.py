"""Security and grounding checks for the player support flow."""

import asyncio

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage

from app.api.v1 import support as support_api
from app.models.support import SupportConversation, SupportKnowledge, SupportMessage, SupportTicket
from app.models.user import User
from app.schemas.support import MessageCreate, SourceReference
from app.services import game_support_agent
from app.services.support_documents import document_page_text, split_document_pages
from app.models.support import SupportDocument
from evals.support_eval import SupportCase, score_case, summarize


def test_player_routes_reject_account_without_conversation(monkeypatch) -> None:
    """A signed-in account without a conversation cannot read another player's history."""
    async def no_conversation(user_id: int) -> None:
        return None

    monkeypatch.setattr(support_api, "find_conversation", no_conversation)
    with pytest.raises(HTTPException) as error:
        asyncio.run(support_api.current_player(User(id=41, email="player@example.com", hashed_password="unused")))
    assert error.value.status_code == 404


def test_staff_allowlist_denies_regular_user() -> None:
    """A valid account JWT alone does not grant staff privileges."""
    with pytest.raises(HTTPException) as error:
        asyncio.run(support_api.current_admin(User(id=41, email="player@example.com", hashed_password="unused")))
    assert error.value.status_code == 403


def test_bug_report_creates_ticket_and_returns_source(monkeypatch) -> None:
    """Concrete issue chat produces a visible ticket and cited answer."""
    player = SupportConversation(id="conversation-1", token_hash="hash", language="en")
    records: list[SupportMessage] = []
    created: list[SupportTicket] = []

    async def fake_messages(conversation_id: str, limit: int = 100) -> list[SupportMessage]:
        return records[:]

    async def fake_add(conversation_id: str, role: str, content: str, sources=None) -> SupportMessage:
        message = SupportMessage(
            id=f"message-{len(records)}", conversation_id=conversation_id, role=role, content=content,
            sources_json='[{"title":"Steam features","url":"https://store.steampowered.com/app/3326810/"}]' if sources else "[]",
        )
        records.append(message)
        return message

    async def fake_answer(question: str, language: str, history: list[tuple[str, str]], conversation_id: str) -> dict:
        return {
            "answer": "Steam lists achievements. The team will investigate your report.",
            "sources": [SourceReference(title="Steam features", url="https://store.steampowered.com/app/3326810/")],
            "needs_ticket": True,
        }

    async def fake_ticket(conversation_id: str, summary: str, reason: str) -> SupportTicket:
        ticket = SupportTicket(id="ticket-1", conversation_id=conversation_id, summary=summary,
                               handoff_reason=reason)
        created.append(ticket)
        return ticket

    monkeypatch.setattr(support_api, "list_messages", fake_messages)
    monkeypatch.setattr(support_api, "add_message", fake_add)
    monkeypatch.setattr(support_api, "answer_player", fake_answer)
    monkeypatch.setattr(support_api, "create_handoff_ticket", fake_ticket)
    async def no_active_handoff(conversation_id: str) -> None:
        return None
    monkeypatch.setattr(support_api, "get_active_handoff", no_active_handoff)
    handler = support_api.send_message.__wrapped__
    result = asyncio.run(handler(None, MessageCreate(content="My Steam achievement did not unlock", language="en"), player))
    assert result.ticket_id == "ticket-1"
    assert result.message.sources[0].title == "Steam features"
    assert len(created) == 1


def test_high_risk_handoff_even_with_article() -> None:
    """Payment and account changes always go to staff."""
    result = {"answer": "See the source", "sources": [SourceReference(title="A", url="https://example.com")],
              "needs_ticket": False}
    assert game_support_agent.classify_handoff("I need a refund", result) == "high_risk"


def test_native_tool_call_uses_server_conversation_scope(monkeypatch) -> None:
    """Provider tool calls reach the allowlist with server-owned conversation identity."""
    seen: list[tuple[str, str]] = []

    class FakeModel:
        def bind_tools(self, schemas):
            assert any(schema["function"]["name"] == "request_human_handoff" for schema in schemas)
            return self

        async def ainvoke(self, messages):
            return AIMessage(content="", tool_calls=[{
                "name": "request_human_handoff", "args": {"summary": "Save cannot be restored", "reason": "high_risk"},
                "id": "call-1", "type": "tool_call",
            }])

    async def fake_execute(name, args, conversation_id):
        seen.append((name, conversation_id))
        return {"ticket_id": "ticket-99", "handoff_reason": "high_risk", "status": "open"}

    monkeypatch.setattr(game_support_agent.LLMRegistry, "get", classmethod(lambda cls, name: FakeModel()))
    monkeypatch.setattr(game_support_agent, "execute_support_tool", fake_execute)
    result = asyncio.run(game_support_agent._call_tools({
        "question": "Restore my save", "conversation_id": "trusted-conversation-id",
    }))
    assert result["tool_ticket_id"] == "ticket-99"
    assert seen == [("request_human_handoff", "trusted-conversation-id")]


def test_ticket_status_question_returns_scoped_tool_data(monkeypatch) -> None:
    """A ticket status question must not be suppressed by the knowledge citation rule."""
    async def fake_execute(name, args, conversation_id):
        assert (name, conversation_id) == ("list_my_tickets", "player-1")
        return {"tickets": [{"id": "ticket-1", "status": "investigating", "summary": "成就未解锁"}]}

    monkeypatch.setattr(game_support_agent, "execute_support_tool", fake_execute)
    tool_result = asyncio.run(game_support_agent._call_tools({
        "question": "我的工单状态是什么？", "conversation_id": "player-1", "language": "zh",
    }))
    answer = asyncio.run(game_support_agent._answer({**tool_result, "language": "zh"}))
    assert "ticket-1" in answer["answer"]
    assert answer["tool_answered"] is True
    assert game_support_agent.classify_handoff("我的工单状态是什么？", answer) is None


def test_uncited_model_answer_is_not_sent_to_player(monkeypatch) -> None:
    """Unsupported model claims are replaced by an explicit uncertainty message."""
    article = SupportKnowledge(
        id="article-1", title="Features", content="Steam Achievements are listed.",
        source_url="https://store.steampowered.com/app/3326810/", status="published",
    )

    async def fake_call(*args, **kwargs) -> game_support_agent.SupportAnswer:
        return game_support_agent.SupportAnswer(answer="All saves are automatically restored.", cited_ids=[])

    monkeypatch.setattr(game_support_agent.llm_service, "call", fake_call)
    result = asyncio.run(game_support_agent._answer({"language": "en", "question": "Can you restore my save?", "articles": [article]}))
    assert result["answer"] == game_support_agent.FALLBACK["en"]
    assert result["sources"] == []


def test_model_outage_uses_matching_published_article(monkeypatch) -> None:
    """A provider outage still gives a cited source and flags a concrete issue."""
    article = SupportKnowledge(
        id="article-1", title="Steam features", content="Steam Achievements are listed.",
        source_url="https://store.steampowered.com/app/3326810/", status="published",
    )

    async def unavailable(*args, **kwargs) -> game_support_agent.SupportAnswer:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(game_support_agent.llm_service, "call", unavailable)
    result = asyncio.run(game_support_agent._answer({
        "language": "en", "question": "My Steam achievement did not unlock", "articles": [article]
    }))
    assert "Steam Achievements are listed." in result["answer"]
    assert result["sources"][0].title == "Steam features"
    assert result["needs_ticket"] is True


def test_active_handoff_pauses_agent_and_keeps_player_message(monkeypatch) -> None:
    """A human-owned conversation must not call the AI while the ticket is active."""
    player = SupportConversation(id="conversation-1", token_hash="hash", language="zh")
    ticket = SupportTicket(id="ticket-1", conversation_id=player.id, summary="成就无法解锁",
                           handoff_reason="complex", status="investigating")
    saved: list[SupportMessage] = []

    async def fake_messages(conversation_id: str, limit: int = 100) -> list[SupportMessage]:
        return saved[:]

    async def fake_add(conversation_id: str, role: str, content: str, sources=None) -> SupportMessage:
        message = SupportMessage(id=f"message-{len(saved)}", conversation_id=conversation_id, role=role, content=content)
        saved.append(message)
        return message

    async def fake_active(conversation_id: str) -> SupportTicket:
        return ticket

    async def no_ai(*args, **kwargs):
        raise AssertionError("AI ran during human handling")

    monkeypatch.setattr(support_api, "list_messages", fake_messages)
    monkeypatch.setattr(support_api, "add_message", fake_add)
    monkeypatch.setattr(support_api, "get_active_handoff", fake_active)
    monkeypatch.setattr(support_api, "answer_player", no_ai)
    handler = support_api.send_message.__wrapped__
    result = asyncio.run(handler(None, MessageCreate(content="还有新的线索", language="zh"), player))
    assert result.handoff_active is True
    assert result.ticket_id == ticket.id
    assert [message.role for message in saved] == ["player", "handoff"]


def test_pdf_page_provenance_and_eval_metrics() -> None:
    """Chunks retain page identity and regression metrics detect missed handoffs."""
    chunks = split_document_pages([(1, "first page fact"), (2, "second page fact")])
    assert chunks == [(1, "first page fact"), (2, "second page fact")]
    document = SupportDocument(title="Guide", filename="guide.pdf", mime_type="application/pdf",
                               content="first page fact\n\nsecond page fact", embedding_model="test",
                               pages_json='[{"page": 1, "content": "first page fact"}, {"page": 2, "content": "second page fact"}]')
    assert document_page_text(document, 2) == "second page fact"
    assert document_page_text(document, 3) is None
    case = SupportCase(id="en-a", language="en", question="Please refund me",
                       expect_handoff=True, expect_citation=False)
    row = score_case(case, {"answer": "Cannot verify", "sources": [], "needs_ticket": False}, 200)
    assert summarize([row])["missed_handoffs"] == 0  # high-risk rule still routes to staff


def test_contextual_citation_does_not_suppress_unresolved_handoff(monkeypatch) -> None:
    """An irrelevant contextual citation cannot mark an unanswered question resolved."""
    article = SupportKnowledge(id="article-1", title="Steam features",
                               content="The store lists single-player.",
                               source_url="https://store.steampowered.com/app/3326810/", status="published")

    async def fake_call(*args, **kwargs) -> game_support_agent.SupportAnswer:
        return game_support_agent.SupportAnswer(answer="A hidden mode is not verified.",
                                                cited_ids=[article.id], unresolved=True)

    monkeypatch.setattr(game_support_agent.llm_service, "call", fake_call)
    result = asyncio.run(game_support_agent._answer({"language": "en", "question": "Is there a hidden multiplayer mode?",
                                                     "articles": [article]}))
    assert result["sources"]
    assert game_support_agent.classify_handoff("Is there a hidden multiplayer mode?", result) == "unresolved"
