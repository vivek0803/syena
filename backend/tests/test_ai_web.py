"""AI with live market data: web search wiring, cost, sources, and no searching on replays of past news."""
from types import SimpleNamespace as NS

from syena import settings_store
from syena.ai import llm


def test_anthropic_backend_reads_answer_after_searches_and_collects_sources(monkeypatch):
    seen = {}

    class Client:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                seen.update(kw)
                cit = [NS(url="https://nseindia.com/x", title="NSE filing"), NS(url="https://nseindia.com/x", title="dup")]
                return NS(stop_reason="end_turn", content=[
                    NS(type="text", text="Let me check the latest results."),
                    NS(type="server_tool_use", text=""), NS(type="web_search_tool_result", text=""),
                    NS(type="text", text='{"in_scope": true, ', citations=cit), NS(type="text", text='"answer": "ok"}', citations=None)],
                    usage=NS(input_tokens=100, output_tokens=20, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                             server_tool_use=NS(web_search_requests=2)))

    be = llm.AnthropicBackend.__new__(llm.AnthropicBackend)
    be.client = Client()
    text, tin, tout = be.complete("m", "sys", "q", 500, web_search=3)
    assert text == '{"in_scope": true, "answer": "ok"}'
    assert seen["tools"][0]["type"].startswith("web_search") and seen["tools"][0]["max_uses"] == 3
    assert be.last_web == (2, [{"title": "NSE filing", "url": "https://nseindia.com/x"}])


def test_ask_returns_sources_and_charges_for_searches(db, fake_llm):
    from syena import ask
    from syena.models import AICall

    out = ask.answer(db, "How did Nifty do today?")
    assert out["sources"] == [{"title": "test source", "url": "https://example.com/x"}] and out["searches"] == 1
    call = db.query(AICall).order_by(AICall.id.desc()).first()
    assert "LIVE MARKET DATA" in call.prompt and call.cost_usd >= llm.WEB_SEARCH_USD


def test_web_search_can_be_turned_off(db, fake_llm):
    from syena import ask
    from syena.models import AICall

    settings_store.put(db, "ai_web", {**settings_store.get(db, "ai_web"), "ask": 0}) if hasattr(settings_store, "put") else None
    if not hasattr(settings_store, "put"):
        from syena.models import AppSetting

        db.merge(AppSetting(key="ai_web", value={"ask": 0, "compare": 4, "invest": 3, "news_analysis": 0}))
        db.commit()
    out = ask.answer(db, "How did Nifty do today?")
    assert out["searches"] == 0 and "LIVE MARKET DATA" not in db.query(AICall).order_by(AICall.id.desc()).first().prompt
