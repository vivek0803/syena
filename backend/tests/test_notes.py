"""My notes: the owner's learnings indexed on save, matched to related news/questions, given to the AI and tracked."""
from datetime import date

from syena import notes, settings_store
from syena.ai import llm
from syena.models import AICall, NewsAnalysis, NewsItem, NoteUse, OwnerNote, Stock, Trade, TradeStatus, Style
from syena.db import utcnow

CRUDE = "When crude oil prices fall, paint companies like Asian Paints gain because crude derivatives are their main raw material."


def _stocks(db):
    db.add_all([Stock(symbol="ASIANPAINT", name="Asian Paints Ltd", industry="Paints", sector="Consumer", active=True),
                Stock(symbol="BERGEPAINT", name="Berger Paints India Ltd", industry="Paints", sector="Consumer", active=True),
                Stock(symbol="INFY", name="Infosys Ltd", industry="IT Services", sector="IT", active=True)])
    db.commit()


def test_save_indexes_with_ai_and_keeps_only_real_industries_and_stocks(db, fake_llm):
    _stocks(db)
    n = notes.save(db, body=CRUDE)
    assert n.digest_status == "ai" and n.kind == "macro" and n.title == "Cheap crude helps paint makers"
    assert n.sectors == ["Paints"] and n.symbols == ["ASIANPAINT"]           # made-up industry / symbol dropped
    assert "brent" in n.keywords and n.effects[0]["direction"] == "positive" and n.embedding
    assert set(n.applies) == set(notes.AREAS)


def test_save_without_ai_still_indexes_words(db):
    _stocks(db)
    n = notes.save(db, body=CRUDE, title="Crude and paints", kind="macro", applies=["news"])
    assert n.digest_status == "basic" and "crude" in n.keywords and n.applies == ["news"] and n.title == "Crude and paints"


def test_matching(db, fake_llm):
    _stocks(db)
    crude = notes.save(db, body=CRUDE)
    rule = notes.save(db, body="Never chase a stock that is already up more than 8% on the day of the news.", pinned=True)
    it_note = notes.save(db, body="IT services stocks fall when the US announces visa curbs.", applies=["ask"])
    hits = {m["note"].id: m for m in notes.match(db, "news", "Brent crude slumps 6% as OPEC raises output", [])}
    assert crude.id in hits and rule.id in hits and it_note.id not in hits          # IT note: Syena AI only
    assert "mentions" in hits[crude.id]["why"]
    by_stock = {m["note"].id for m in notes.match(db, "news", "Berger Paints Q2 results", ["BERGEPAINT"])}
    assert crude.id in by_stock                                                      # same industry as Asian Paints
    assert {m["note"].id for m in notes.match(db, "news", "Infosys wins a large deal", ["INFY"])} == {rule.id}
    crude.active = False
    db.commit()
    assert crude.id not in {m["note"].id for m in notes.match(db, "news", "Brent crude slumps", [])}
    settings_store.put(db, "owner_notes", {**settings_store.get(db, "owner_notes"), "enabled": False})
    assert notes.match(db, "news", "Brent crude slumps 6% OPEC", []) == []


def test_ai_reads_related_notes_and_says_which_changed_its_view(db, fake_llm):
    from syena import ask

    _stocks(db)
    crude = notes.save(db, body=CRUDE)
    fake_llm.ask = {"in_scope": True, "answer": "Paint makers benefit from cheap crude.", "notes_used": ["N%d" % crude.id]}
    out = ask.answer(db, "Crude oil is falling, what about Asian Paints?")
    assert out["owner_notes"] == [{"id": crude.id, "title": crude.title, "influenced": True}]
    assert "<owner_notes>" in fake_llm.prompts[-1] and CRUDE in fake_llm.prompts[-1]
    db.refresh(crude)
    use = db.query(NoteUse).one()
    assert crude.used_count == 1 and use.purpose == "ask_ai" and use.influenced and use.ai_call_id
    # unrelated question: no notes, nothing recorded
    ask.answer(db, "How is Infosys doing?")
    assert "<owner_notes>" not in fake_llm.prompts[-1] and db.query(NoteUse).count() == 1


def test_evals_and_shadow_runs_never_see_notes(db, fake_llm):
    _stocks(db)
    notes.save(db, body=CRUDE, pinned=True)
    llm.call(db, "ask_ai", "ask_ai", tier="premium", eval_run_id=1, notes=("ask", "crude", None),
             variables={"question": "q", "history": "", "context": ""})
    llm.call(db, "ask_ai", "ask_ai", tier="premium", shadow=True, notes=("ask", "crude", None),
             variables={"question": "q", "history": "", "context": ""})
    assert all("<owner_notes>" not in p for p in fake_llm.prompts[-2:]) and db.query(NoteUse).count() == 0


def test_owner_theme_lets_macro_mapper_see_items_outside_builtin_words(db, fake_llm):
    from syena.news import macro

    notes.save(db, body="When the price of cashew rises, cashew processors and snack makers see margin pressure.")
    fake_llm_kw = db.query(OwnerNote).one()
    fake_llm_kw.keywords = ["cashew", "cashew price", "snack makers"]
    db.commit()
    item = NewsItem(source="ET markets", url="https://x/1", title="Cashew price jumps 30% after poor harvest", body="",
                    published_at=utcnow(), content_hash="h1")
    assert not macro.looks_macro(item) and macro.owner_theme(db, item)
    filing = NewsItem(source="NSE announcements", url="https://nseindia.com/x", title="Cashew price update cashew", body="",
                      published_at=utcnow(), content_hash="h2")
    assert not macro.owner_theme(db, filing)


def test_outcomes_follow_news_trades(db, fake_llm):
    _stocks(db)
    n = notes.save(db, body=CRUDE)
    item = NewsItem(source="ET", url="u", title="Crude falls", published_at=utcnow(), content_hash="c1")
    db.add(item)
    db.add(AICall(id=901, purpose="news_analysis", tier="analysis", model="m", prompt_name="news_analysis", prompt_version="v", prompt="p"))
    db.commit()
    db.add(NewsAnalysis(news_item_id=item.id, stage="analysis", relevant=True, symbols=["ASIANPAINT"], ai_call_id=901))
    db.add(NoteUse(note_id=n.id, ai_call_id=901, purpose="news_analysis"))
    db.add(Trade(source="news", mode="PAPER", style=Style.SWING, stock="ASIANPAINT", qty=1, invested_amount=100,
                 status=TradeStatus.CLOSED, profit_loss=250.0, news_item_ids=[item.id]))
    db.commit()
    assert notes.outcomes(db, n.id) == {"trades": 1, "closed": 1, "wins": 1, "pnl": 250.0}


def test_notes_api(authed, db, fake_llm):
    _stocks(db)
    r = authed.post("/api/notes", json={"body": CRUDE})
    assert r.status_code == 200 and r.json()["symbols"] == ["ASIANPAINT"]
    nid = r.json()["id"]
    assert authed.post("/api/notes", json={"body": "hi"}).status_code == 400
    assert authed.post("/api/notes", json={"body": "x" * 20, "kind": "bogus"}).status_code == 422
    t = authed.post("/api/notes/try", json={"text": "Brent crude crashes on OPEC supply news", "area": "news"}).json()
    assert [m["id"] for m in t["matches"]] == [nid]
    assert authed.patch(f"/api/notes/{nid}", json={"active": False}).json()["active"] is False
    assert authed.post("/api/notes/try", json={"text": "Brent crude crashes on OPEC news"}).json()["matches"] == []
    u = authed.put(f"/api/notes/{nid}", json={"body": CRUDE + " Tyre makers too.", "pinned": True}).json()
    assert u["pinned"] and "Tyre" in u["body"]
    assert authed.get(f"/api/notes/{nid}/uses").json()["uses"] == []
    assert len(authed.get("/api/notes").json()) == 1 and "news" in authed.get("/api/notes/meta").json()["areas"]
    assert authed.delete(f"/api/notes/{nid}").json() == {"deleted": True} and authed.get("/api/notes").json() == []


def test_news_verdict_reads_notes_and_shows_them(db, user, fake_llm, md):
    from datetime import timedelta

    from sqlalchemy import select

    from syena.execution.brokers import PaperBroker
    from syena.news import pipeline
    from tests.test_news import _news_now

    n = notes.save(db, body="Large IT services deal wins (over $1 billion) usually lift Infosys for a few days.", applies=["news"])
    n.keywords = ["deal", "billion", "infosys", "it services"]
    db.commit()
    now = _news_now(md)
    item = NewsItem(source="t", url="u", title="Infosys wins $1.5 billion deal", published_at=now - timedelta(minutes=10), content_hash="nh1")
    db.add(item)
    db.commit()
    fake_llm.analysis = {**fake_llm.analysis, "notes_used": [n.id]}
    pipeline.process_symbol(db, "INFY", [item], md, "order_win", lambda db, mode: PaperBroker(), now=now)
    a = db.scalar(select(NewsAnalysis).where(NewsAnalysis.stage == "analysis"))
    assert a.result["owner_notes"] == [{"id": n.id, "title": n.title, "influenced": True}]
    assert "<owner_notes>" in next(p for p in fake_llm.prompts if "Infosys wins" in p)
